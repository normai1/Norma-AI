"""
Object storage provider contract: the interface every storage implementation
in this codebase is built against - mock (tests), local filesystem
(development only, CLAUDE.md section 20), and S3 (production).
"""

from typing import Protocol


class StorageProviderError(Exception):
    """
    Base class for a storage provider's own failures, distinct from a bug in
    the calling code.
    """


class StorageObjectNotFound(StorageProviderError):
    """
    No object exists at the requested key.
    """


class StorageProvider(Protocol):
    """
    Durable object storage: upload, download, delete by key. Every
    implementation must confine an object to exactly the key given - no
    provider may silently reinterpret or relocate a key.
    """

    async def upload(self, key: str, content: bytes, *, content_type: str) -> None:
        """
        Store content at key, overwriting any existing object there.
        """
        ...

    async def download(self, key: str) -> bytes:
        """
        Return the bytes stored at key. Raises StorageObjectNotFound if no
        object exists there.
        """
        ...

    async def delete(self, key: str) -> None:
        """
        Remove the object at key. Deleting a key that does not exist is not
        an error - the end state (nothing there) is what the caller wants.
        """
        ...
