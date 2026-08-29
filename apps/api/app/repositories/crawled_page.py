import uuid
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.crawled_page import CrawledPage


async def get_by_url(
    db: AsyncSession,
    knowledge_source_id: uuid.UUID,
    url: str,
) -> CrawledPage | None:
    """
    Look up a crawled page by its URL, scoped to one knowledge source.
    """

    return await db.scalar(
        select(CrawledPage).where(
            CrawledPage.knowledge_source_id == knowledge_source_id,
            CrawledPage.url == url,
        ),
    )


async def list_for_source(
    db: AsyncSession,
    knowledge_source_id: uuid.UUID,
) -> list[CrawledPage]:
    """
    Every crawled page for one knowledge source.
    """

    result = await db.scalars(
        select(CrawledPage)
        .where(CrawledPage.knowledge_source_id == knowledge_source_id)
        .order_by(CrawledPage.url),
    )

    return list(result.all())


async def list_for_sources(
    db: AsyncSession,
    knowledge_source_ids: list[uuid.UUID],
) -> list[CrawledPage]:
    """
    Every crawled page belonging to any of the given knowledge sources - one
    query for a whole list page, not one query per row.
    """

    if not knowledge_source_ids:
        return []

    result = await db.scalars(
        select(CrawledPage).where(
            CrawledPage.knowledge_source_id.in_(knowledge_source_ids),
        ),
    )

    return list(result.all())


async def upsert(
    db: AsyncSession,
    *,
    knowledge_source_id: uuid.UUID,
    url: str,
    extracted_text: str,
    content_hash: str,
    fetched_at: datetime,
) -> CrawledPage:
    """
    Insert a new crawled page, or update an existing one. fetched_at is
    always refreshed; extracted_text/content_hash are only overwritten when
    the hash actually changed - the dedup that makes a recrawl cheap.
    """

    existing = await get_by_url(db, knowledge_source_id, url)

    if existing is None:
        crawled_page = CrawledPage(
            knowledge_source_id=knowledge_source_id,
            url=url,
            extracted_text=extracted_text,
            content_hash=content_hash,
            fetched_at=fetched_at,
        )
        db.add(crawled_page)
        await db.flush()

        return crawled_page

    existing.fetched_at = fetched_at

    if existing.content_hash != content_hash:
        existing.extracted_text = extracted_text
        existing.content_hash = content_hash

    await db.flush()

    return existing
