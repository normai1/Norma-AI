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

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "apps" / "api"))


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

    from app.core.database import get_session_factory
    from app.models.chunk import Chunk
    from app.models.crawled_page import CrawledPage
    from app.models.knowledge_source import KnowledgeSource
    from app.providers.factory import get_embedding_provider
    from app.repositories import chunk as chunk_repo
    from app.repositories.chunk import ChunkWrite
    from app.services.chunker import MAX_CHUNK_TOKENS, chunk_text
    from app.services.embedding_batch import embed_in_batches

    assistant_id = uuid.UUID(args.assistant)
    factory = get_session_factory()

    print(f"chunk budget is now {MAX_CHUNK_TOKENS} tokens\n")

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

            print(f"source {str(source.id)[:8]} ({source.url or 'website'})")
            print(f"  {len(pages)} stored pages")
            print(f"  {existing} chunks now -> {len(spans_by_url)} after re-chunking")
            print(f"  new chunk size: median {median} chars, max {sizes[-1] if sizes else 0}")

            if args.dry_run:
                print("  dry run: nothing embedded, nothing written\n")

                continue

            if not spans_by_url:
                print("  nothing to write - leaving the existing chunks alone\n")

                continue

            started = time.monotonic()
            provider = get_embedding_provider()

            print(f"  embedding {len(spans_by_url)} chunks...", flush=True)

            vectors = await embed_in_batches(
                provider, [span.text for _url, span in spans_by_url]
            )

            await chunk_repo.replace_for_source(
                db,
                organization_id=source.organization_id,
                workspace_id=source.workspace_id,
                assistant_id=source.assistant_id,
                knowledge_source_id=source.id,
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
                        spans_by_url, vectors, strict=True
                    )
                ],
            )
            await db.commit()

            print(
                f"  replaced in {time.monotonic() - started:.0f}s "
                f"({len(spans_by_url)} chunks)\n"
            )

    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
