import shutil
import tempfile
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from app.providers.factory import MissingS3ConfigError, get_storage_provider
from app.providers.local_storage import LocalStorage
from app.providers.mock_storage import MockStorage
from app.providers.s3_storage import S3Storage
from app.providers.storage import StorageObjectNotFound


async def test_mock_storage_round_trips_upload_and_download() -> None:
    storage = MockStorage()

    await storage.upload("a/b.txt", b"hello", content_type="text/plain")

    assert await storage.download("a/b.txt") == b"hello"


async def test_mock_storage_download_of_a_missing_key_raises() -> None:
    storage = MockStorage()

    with pytest.raises(StorageObjectNotFound):
        await storage.download("missing")


async def test_mock_storage_delete_removes_the_object() -> None:
    storage = MockStorage()
    await storage.upload("a", b"hello", content_type="text/plain")

    await storage.delete("a")

    with pytest.raises(StorageObjectNotFound):
        await storage.download("a")


@pytest.fixture
def local_storage_dir():
    directory = tempfile.mkdtemp()

    yield directory

    shutil.rmtree(directory, ignore_errors=True)


async def test_local_storage_round_trips_upload_and_download(local_storage_dir) -> None:
    storage = LocalStorage(base_dir=local_storage_dir)

    await storage.upload("nested/file.txt", b"hello", content_type="text/plain")

    assert await storage.download("nested/file.txt") == b"hello"
    assert (Path(local_storage_dir) / "nested" / "file.txt").is_file()


async def test_local_storage_download_of_a_missing_key_raises(
    local_storage_dir,
) -> None:
    storage = LocalStorage(base_dir=local_storage_dir)

    with pytest.raises(StorageObjectNotFound):
        await storage.download("missing")


async def test_local_storage_delete_removes_the_file(local_storage_dir) -> None:
    storage = LocalStorage(base_dir=local_storage_dir)
    await storage.upload("a.txt", b"hello", content_type="text/plain")

    await storage.delete("a.txt")

    assert not (Path(local_storage_dir) / "a.txt").is_file()


async def test_local_storage_confines_writes_to_its_base_directory(
    local_storage_dir,
) -> None:
    storage = LocalStorage(base_dir=local_storage_dir)

    with pytest.raises(Exception):  # noqa: B017 - StorageProviderError, any escape attempt
        await storage.upload("../escape.txt", b"hello", content_type="text/plain")


def _stub_s3_client() -> MagicMock:
    return MagicMock()


async def test_s3_storage_upload_calls_put_object() -> None:
    client = _stub_s3_client()
    storage = S3Storage(
        bucket="test-bucket",
        region="us-east-1",
        access_key_id="key",
        secret_access_key="secret",
        client=client,
    )

    await storage.upload("a/b.pdf", b"hello", content_type="application/pdf")

    client.put_object.assert_called_once_with(
        Bucket="test-bucket",
        Key="a/b.pdf",
        Body=b"hello",
        ContentType="application/pdf",
    )


async def test_s3_storage_download_calls_get_object_and_reads_body() -> None:
    client = _stub_s3_client()
    body = MagicMock()
    body.read.return_value = b"hello"
    client.get_object.return_value = {"Body": body}
    storage = S3Storage(
        bucket="test-bucket",
        region="us-east-1",
        access_key_id="key",
        secret_access_key="secret",
        client=client,
    )

    result = await storage.download("a/b.pdf")

    assert result == b"hello"
    client.get_object.assert_called_once_with(Bucket="test-bucket", Key="a/b.pdf")


async def test_s3_storage_delete_calls_delete_object() -> None:
    client = _stub_s3_client()
    storage = S3Storage(
        bucket="test-bucket",
        region="us-east-1",
        access_key_id="key",
        secret_access_key="secret",
        client=client,
    )

    await storage.delete("a/b.pdf")

    client.delete_object.assert_called_once_with(Bucket="test-bucket", Key="a/b.pdf")


async def test_s3_storage_download_of_a_missing_key_raises_object_not_found() -> None:
    from botocore.exceptions import ClientError

    client = _stub_s3_client()
    client.get_object.side_effect = ClientError(
        {"Error": {"Code": "NoSuchKey", "Message": "not found"}},
        "GetObject",
    )
    storage = S3Storage(
        bucket="test-bucket",
        region="us-east-1",
        access_key_id="key",
        secret_access_key="secret",
        client=client,
    )

    with pytest.raises(StorageObjectNotFound):
        await storage.download("missing")


def test_get_storage_provider_rejects_s3_without_credentials(monkeypatch) -> None:
    monkeypatch.setattr("app.providers.factory.settings.aws_s3_bucket", "")
    monkeypatch.setattr("app.providers.factory.settings.aws_region", "")
    monkeypatch.setattr("app.providers.factory.settings.aws_access_key_id", "")
    monkeypatch.setattr("app.providers.factory.settings.aws_secret_access_key", "")

    with pytest.raises(MissingS3ConfigError):
        get_storage_provider("s3")


def test_get_storage_provider_constructs_s3_with_full_credentials(monkeypatch) -> None:
    monkeypatch.setattr("app.providers.factory.settings.aws_s3_bucket", "bucket")
    monkeypatch.setattr("app.providers.factory.settings.aws_region", "us-east-1")
    monkeypatch.setattr("app.providers.factory.settings.aws_access_key_id", "key")
    monkeypatch.setattr(
        "app.providers.factory.settings.aws_secret_access_key", "secret"
    )

    provider = get_storage_provider("s3")

    assert isinstance(provider, S3Storage)


def test_get_storage_provider_returns_local_for_the_local_name(
    local_storage_dir, monkeypatch
) -> None:
    monkeypatch.setattr(
        "app.providers.factory.settings.local_storage_dir", local_storage_dir
    )

    provider = get_storage_provider("local")

    assert isinstance(provider, LocalStorage)


def test_get_storage_provider_rejects_an_unknown_name() -> None:
    from app.providers.factory import UnknownStorageProviderError

    with pytest.raises(UnknownStorageProviderError):
        get_storage_provider("nonexistent")
