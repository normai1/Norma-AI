"""
Re-chunk and re-embed a website knowledge source from the pages already
stored, without crawling anything again.

Why this exists rather than a recrawl: the pages have not changed, only the
chunk size has. `CrawledPage.extracted_text` is the crawler's own output and
is kept, so the whole pipeline downstream of the fetch can be replayed
offline - no traffic to someone else's site, no dependence on it still
serving the same content, and no risk of a partial crawl replacing a
complete chunk set.

Scope is deliberately narrow. Only `website` sources are touched. Manual FAQ
entries are one chunk per question and already measure a median of 224
characters, well inside the new budget; splitting one would divide a question
from its answer, which is the opposite of the point.

    python scripts/rechunk_website_sources.py --assistant <id> --dry-run
    python scripts/rechunk_website_sources.py --assistant <id>

--dry-run re-chunks and reports what would change without embedding or
writing anything, which is how to see the cost before paying it.

Safe to interrupt. Each source is replaced in one transaction only after its
every chunk has embedded successfully, so a failure or a Ctrl-C leaves the
previous chunk set exactly as it was - the property `replace_for_source`
already guarantees for the crawl path.
"""

import argparse
import asyncio
import os
import pathlib
import sys
import time

# Importable from the repository root and from inside the API container,
# where the same code is mounted at /app rather than at apps/api.
for _candidate in (
    pathlib.Path(__file__).resolve().parent.parent / "apps" / "api",
    pathlib.Path("/app"),
):
    if (_candidate / "app").is_dir():
        sys.path.insert(0, str(_candidate))

        break


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--assistant", required=True, help="assistant id")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="report what would change without embedding or writing",
    )
    args = parser.parse_args()

    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):  # pragma: no cover
        pass

    import uuid

    from sqlalchemy import func, select

    # Every model, not just the three used below: SQLAlchemy resolves foreign
    # keys against the whole registry, and Chunk's workspace_id points at a
    # table that is only mapped if its module has been imported. Importing
    # three models got through the entire embedding run and then failed on
    # the final write with NoReferencedTableError, wasting all of it.
    import app.db.base  # noqa: F401
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
    from sqlalchemy.pool import NullPool

    from app.core.config import settings
    from app.models.chunk import Chunk
    from app.models.crawled_page import CrawledPage
    from app.models.knowledge_source import KnowledgeSource
    from app.providers.factory import get_embedding_provider
    from app.repositories import chunk as chunk_repo
    from app.repositories.chunk import ChunkWrite
    from app.services.chunker import MAX_CHUNK_TOKENS, chunk_text
    from app.services.embedding_batch import embed_in_batches

    assistant_id = uuid.UUID(args.assistant)
    # This job's own engine, with pooling switched off.
    #
    # Sharing the application's pooled engine failed twice with
    # MissingGreenlet on a connection ping: a one-shot script that reads,
    # then spends twenty minutes embedding, then writes, is exactly the
    # shape a connection pool handles worst. NullPool opens a connection
    # when one is needed and closes it after, so there is nothing to go
    # stale in between and no pre-ping to trip over.
    engine = create_async_engine(settings.database_url, poolclass=NullPool)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    print(f"chunk budget is now {MAX_CHUNK_TOKENS} tokens\n")

    # Three phases, with no database session held across the slow one.
    #
    # The first attempt kept one session open for the whole job and failed
    # after twenty minutes of embedding with MissingGreenlet: the pooled
    # connection had gone stale underneath it while nothing was using it.
    # Reading, embedding and writing are separate concerns with very
    # different durations, and only two of them need a connection.

    # Phase one: read, and prove the write path, then let the session go.
    plans: list[dict] = []

    async with factory() as db:
        sources = (
            await db.scalars(
                select(KnowledgeSource).where(
                    KnowledgeSource.assistant_id == assistant_id,
                    KnowledgeSource.type == "website",
                )
            )
        ).all()

        if not sources:
            print("No website sources for that assistant.", file=sys.stderr)

            return 1

        for source in sources:
            pages = (
                await db.scalars(
                    select(CrawledPage)
                    .where(CrawledPage.knowledge_source_id == source.id)
                    .order_by(CrawledPage.url)
                )
            ).all()

            existing = await db.scalar(
                select(func.count())
                .select_from(Chunk)
                .where(Chunk.knowledge_source_id == source.id)
            )

            spans_by_url = [
                (page.url, span)
                for page in pages
                if page.extracted_text
                for span in chunk_text(page.extracted_text)
            ]

            sizes = sorted(len(span.text) for _url, span in spans_by_url)
            median = sizes[len(sizes) // 2] if sizes else 0

            print(f"source {str(source.id)[:8]} ({source.source_url or 'website'})")
            print(f"  {len(pages)} stored pages")
            print(f"  {existing} chunks now -> {len(spans_by_url)} after re-chunking")
            print(
                f"  new chunk size: median {median} chars, "
                f"max {sizes[-1] if sizes else 0}"
            )

            if not spans_by_url:
                print("  nothing to write - leaving the existing chunks alone\n")

                continue

            # Everything needed later, read off the ORM object *now* and kept
            # as plain values.
            #
            # Not a style preference. The rollback below expires every
            # instance in the session, so a later `source.id` is a lazy
            # refresh - a database round trip triggered from plain attribute
            # access, outside any await, which asyncpg answers with
            # MissingGreenlet. That failed three runs, each after the
            # embedding had already been paid for, and the traceback points
            # at the attribute rather than at the rollback that caused it.
            plan = {
                "id": source.id,
                "organization_id": source.organization_id,
                "workspace_id": source.workspace_id,
                "assistant_id": source.assistant_id,
                "spans": spans_by_url,
            }

            if not args.dry_run:
                # Prove the write path before spending twenty minutes
                # embedding. Replacing a source with nothing is a no-op the
                # database will roll back, and it exercises exactly the
                # mapper resolution and constraints the real write needs -
                # an earlier attempt got through the entire embedding run and
                # then failed here on an unresolvable foreign key.
                await chunk_repo.replace_for_source(
                    db,
                    organization_id=plan["organization_id"],
                    workspace_id=plan["workspace_id"],
                    assistant_id=plan["assistant_id"],
                    knowledge_source_id=plan["id"],
                    chunks=[],
                )
                await db.rollback()

            plans.append(plan)

    if args.dry_run:
        print("dry run: nothing embedded, nothing written")
        await engine.dispose()

        return 0

    # Phase two: embed, with no connection held.
    for plan in plans:
        spans = plan["spans"]
        started = time.monotonic()
        provider = get_embedding_provider()

        print(f"  embedding {len(spans)} chunks...", flush=True)

        plan["vectors"] = await embed_in_batches(
            provider, [span.text for _url, span in spans]
        )

        print(f"  embedded in {time.monotonic() - started:.0f}s", flush=True)

    # Phase three: write, on a fresh session.
    async with factory() as db:
        for plan in plans:
            spans = plan["spans"]

            await chunk_repo.replace_for_source(
                db,
                organization_id=plan["organization_id"],
                workspace_id=plan["workspace_id"],
                assistant_id=plan["assistant_id"],
                knowledge_source_id=plan["id"],
                chunks=[
                    ChunkWrite(
                        text=span.text,
                        metadata={
                            "url": page_url,
                            "char_start": span.char_start,
                            "char_end": span.char_end,
                        },
                        embedding=vector,
                    )
                    for (page_url, span), vector in zip(
                        spans, plan["vectors"], strict=True
                    )
                ],
            )
            await db.commit()

            print(f"  replaced {str(plan['id'])[:8]}: {len(spans)} chunks written")

    await engine.dispose()

    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
