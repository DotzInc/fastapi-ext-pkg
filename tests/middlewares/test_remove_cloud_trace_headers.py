from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.requests import Request

from fastapi_extras.middlewares.remove_cloud_trace_headers import RemoveCloudTraceHeadersMiddleware

app = FastAPI()
app.add_middleware(RemoveCloudTraceHeadersMiddleware)


@app.get("/")
async def read_main(request: Request):
    return {"headers": dict(request.headers)}


def test_remove_cloud_trace_headers():
    client = TestClient(app)

    response = client.get(
        "/",
        headers={"x-cloud-trace-context": "12345", "traceparent": "67890", "other-header": "value"},
    )

    assert "x-cloud-trace-context" not in response.json()["headers"]
    assert "traceparent" not in response.json()["headers"]

    assert "other-header" in response.json()["headers"]
    assert response.json()["headers"]["other-header"] == "value"
