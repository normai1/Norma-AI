"""
Deterministic storage mock. No filesystem, no network - the test suite's
actual storage provider, injected via app.dependency_overrides in
tests/conftest.py the same way get_db/get_redis already are.
"""

from app.providers.storage import StorageObjectNotFound


class MockStorage:
    """
    In-memory key/value store standing in for a real object store.
    """

    def __init__(self) -> None:
        # Public - a test inspects this directly to prove a real
        # upload/download round-trip happened, not just a DB write (the same
        # reasoning MockTTS.cancelled is public for).
        self.objects: dict[str, bytes] = {}

    async def upload(self, key: str, content: bytes, *, content_type: str) -> None:
        self.objects[key] = content

    async def download(self, key: str) -> bytes:
        if key not in self.objects:
            raise StorageObjectNotFound(key)

        return self.objects[key]

    async def delete(self, key: str) -> None:
        self.objects.pop(key, None)
