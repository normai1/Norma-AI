"""
S3 storage adapter - the production StorageProvider (CLAUDE.md section 20).
boto3 is synchronous; every call is wrapped in asyncio.to_thread since this
is a control-plane upload request, not the real-time audio path the "no
blocking I/O" rule targets.
"""

import asyncio
from typing import Any

import boto3
from botocore.exceptions import ClientError

from app.providers.storage import StorageObjectNotFound, StorageProviderError


class S3Storage:
    """
    Accepts an injected boto3 S3 client for testing (a stub, matching how
    ElevenLabsSTT/TTS accept an injected httpx.AsyncClient); constructs a
    real one when not provided.
    """

    def __init__(
        self,
        *,
        bucket: str,
        region: str,
        access_key_id: str,
        secret_access_key: str,
        client: Any | None = None,
    ) -> None:
        self._bucket = bucket
        self._client = client or boto3.client(
            "s3",
            region_name=region,
            aws_access_key_id=access_key_id,
            aws_secret_access_key=secret_access_key,
        )

    async def upload(self, key: str, content: bytes, *, content_type: str) -> None:
        try:
            await asyncio.to_thread(
                self._client.put_object,
                Bucket=self._bucket,
                Key=key,
                Body=content,
                ContentType=content_type,
            )
        except ClientError as exc:
            raise StorageProviderError(f"S3 upload failed for key {key!r}") from exc

    async def download(self, key: str) -> bytes:
        try:
            response = await asyncio.to_thread(
                self._client.get_object,
                Bucket=self._bucket,
                Key=key,
            )
        except ClientError as exc:
            error_code = exc.response.get("Error", {}).get("Code")

            if error_code in ("NoSuchKey", "404"):
                raise StorageObjectNotFound(key) from exc

            raise StorageProviderError(f"S3 download failed for key {key!r}") from exc

        return await asyncio.to_thread(response["Body"].read)

    async def delete(self, key: str) -> None:
        try:
            await asyncio.to_thread(
                self._client.delete_object,
                Bucket=self._bucket,
                Key=key,
            )
        except ClientError as exc:
            raise StorageProviderError(f"S3 delete failed for key {key!r}") from exc
