from unittest.mock import AsyncMock

import pytest
from httpx import Request, Response, codes

from fastapi_extras.security.authorization import RemoteAuthorization


@pytest.mark.anyio
async def test_remote_authorization(httpx_asyncmock: AsyncMock):
    url = "http://test"
    headers = {"x-api-key": "supers3cr3t"}
    payload = {"token": "cmFwYWR1cmEK"}

    request = Request("POST", url, json=payload, headers=headers)
    response = Response(request=request, status_code=codes.OK)
    httpx_asyncmock.return_value = response

    authority = RemoteAuthorization(url, headers=headers)
    result = await authority.authorize(json=payload)

    assert result.status_code == codes.OK
