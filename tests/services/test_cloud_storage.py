from unittest.mock import MagicMock

import pytest

from fastapi_utils.services import cloud_storage, protocols


@pytest.fixture
def storage_mock(monkeypatch: pytest.MonkeyPatch) -> MagicMock:
    FakeClient = MagicMock()
    monkeypatch.setattr("google.cloud.storage.Client", FakeClient)

    return FakeClient.return_value


def test_uploader(storage_mock: MagicMock):
    uploader = cloud_storage.Uploader()

    assert isinstance(uploader, protocols.Uploader)

    uploader.upload("test-bucket", "destination.txt", "source.txt")

    assert storage_mock.bucket.call_count == 1
    assert storage_mock.bucket.return_value.blob.call_count == 1
    assert storage_mock.bucket.return_value.blob.return_value.upload_from_filename.call_count == 1
