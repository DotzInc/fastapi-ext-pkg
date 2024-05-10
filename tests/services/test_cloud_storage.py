from unittest.mock import MagicMock

from fastapi_utils.services import cloud_storage, protocols


def test_uploader(storage_mock: MagicMock):
    uploader = cloud_storage.Uploader()

    assert isinstance(uploader, protocols.Uploader)

    uploader.upload("test-bucket", "destination.txt", "source.txt")

    assert storage_mock.bucket.call_count == 1
    assert storage_mock.bucket.return_value.blob.call_count == 1
    assert storage_mock.bucket.return_value.blob.return_value.upload_from_filename.call_count == 1
