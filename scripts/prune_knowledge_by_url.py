"""
Remove pages from an assistant's knowledge base by URL, without re-crawling.

A crawl takes a whole site, and a whole site is not the same thing as a
business's current answers. A documentation site keeps its legacy pages; a
blog keeps every pricing change it has ever announced. All of it is true,
none of it is marked as superseded, and retrieval has no notion of which
version of a fact is the one in force today - so a caller asking "how much
is it" can be told last year's price, this year's price, or the price of a
scheme that no longer exists, depending on which chunk happened to score
highest. Reported as the assistant giving a different, wrong answer to a
question it answered correctly before.

Measured on a 337-page crawl of one site: six pages talk about pricing and
four of them are historical - two dated blog announcements, one "legacy"
docs page, and one superseded teams post - against a single canonical
pricing page that is 0.1% of the corpus by volume.

This is deliberately not a crawl filter. The judgement of which pages are
superseded belongs to whoever knows the business, cannot be inferred from a
URL in general, and is wrong often enough that it has to be reviewable
before it deletes anything - hence --dry-run, which is the default.

    python scripts/prune_knowledge_by_url.py --assistant <id> --match /blog/2024
    python scripts/prune_knowledge_by_url.py --assistant <id> --match x --apply

Patterns are plain substrings matched against the chunk's recorded source
URL, case-insensitively. Nothing is deleted without --apply.

Reversible: the crawled pages themselves are untouched, so re-running the
source's re-chunk restores anything removed here.
"""

import argparse
import asyncio
import pathlib
import sys

# Runs from the repository (apps/api on the path) or inside the api
# container, where the application is already at /app.
_REPO_API = pathlib.Path(__file__).resolve().parent.parent / "apps" / "api"

for _candidate in (_REPO_API, pathlib.Path("/app")):
    if (_candidate / "app").is_dir():
        sys.path.insert(0, str(_candidate))
        break


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--assistant", required=True, help="assistant id")
    parser.add_argument(
        "--match",
        action="append",
        default=[],
        help="substring of the source URL to remove; repeatable",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="actually delete. Without this, nothing is written.",
    )
    args = parser.parse_args()

    if not args.match:
        print("nothing to match - pass at least one --match")

        return 2

    import uuid

    import app.db.base  # noqa: F401
    from sqlalchemy import delete, func, or_, select
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
    from sqlalchemy.pool import NullPool

    from app.core.config import settings
    from app.models.chunk import Chunk

    assistant_id = uuid.UUID(args.assistant)
    engine = create_async_engine(settings.database_url, poolclass=NullPool)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    async with factory() as db:
        source_url = Chunk.__table__.c.metadata["url"].astext

        matched = or_(*[source_url.ilike(f"%{p}%") for p in args.match])

        total = (
            await db.execute(
                select(func.count())
                .select_from(Chunk)
                .where(Chunk.assistant_id == assistant_id)
            )
        ).scalar_one()

        rows = (
            await db.execute(
                select(source_url, func.count())
                .where(Chunk.assistant_id == assistant_id, matched)
                .group_by(source_url)
                .order_by(func.count().desc())
            )
        ).all()

        going = sum(count for _url, count in rows)

        print(f"assistant has {total} chunks; {going} match")
        print()

        for page_url, count in rows[:40]:
            print(f"  {count:5}  {page_url}")

        if len(rows) > 40:
            print(f"  ... and {len(rows) - 40} more pages")

        print()

        if not args.apply:
            print("dry run: nothing deleted. Re-run with --apply to remove these.")

            return 0

        if going == 0:
            print("nothing to delete")

            return 0

        await db.execute(
            delete(Chunk).where(Chunk.assistant_id == assistant_id, matched)
        )
        await db.commit()

        print(f"deleted {going} chunks from {len(rows)} pages")
        print(f"{total - going} chunks remain")

    await engine.dispose()

    return 0


raise SystemExit(asyncio.run(main()))
