from fastapi_utils.dependencies import storage
from fastapi_utils.services import cloud_storage, protocols


def test_uploader():
    uploader = storage.uploader(cloud_storage.Uploader)

    assert isinstance(uploader(), protocols.Uploader)
