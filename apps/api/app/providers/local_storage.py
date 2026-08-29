"""
Local filesystem storage adapter - development only (CLAUDE.md section 20).
Never used in production; S3Storage is the production adapter.
"""

import asyncio
from pathlib import Path

from app.providers.storage import StorageObjectNotFound, StorageProviderError


class LocalStorage:
    """
    Writes objects under a configured base directory. Wrapped in
    asyncio.to_thread since this is a control-plane upload request, not the
    real-time audio path the "no blocking I/O" rule targets.
    """

    def __init__(self, base_dir: str) -> None:
        self._base_dir = Path(base_dir).resolve()
        self._base_dir.mkdir(parents=True, exist_ok=True)

    def _resolve(self, key: str) -> Path:
        path = (self._base_dir / key).resolve()

        if self._base_dir not in path.parents and path != self._base_dir:
            raise StorageProviderError(f"Key {key!r} escapes the storage directory")

        return path

    async def upload(self, key: str, content: bytes, *, content_type: str) -> None:
        path = self._resolve(key)

        def _write() -> None:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(content)

        await asyncio.to_thread(_write)

    async def download(self, key: str) -> bytes:
        path = self._resolve(key)

        if not path.is_file():
            raise StorageObjectNotFound(key)

        return await asyncio.to_thread(path.read_bytes)

    async def delete(self, key: str) -> None:
        path = self._resolve(key)

        def _delete() -> None:
            path.unlink(missing_ok=True)

        await asyncio.to_thread(_delete)
