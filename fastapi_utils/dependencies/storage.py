from typing import Type

from fastapi_utils.services.protocols import Uploader


def uploader(service: Type[Uploader]) -> Type[Uploader]:
    return service
