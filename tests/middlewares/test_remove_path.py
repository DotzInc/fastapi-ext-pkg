from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.testclient import TestClient

from fastapi_utils.middlewares.remove_path import RemovePathMiddleware

app = FastAPI()
app.add_middleware(RemovePathMiddleware, path="/test")


@app.get("/foo")
async def foo():
    return {"message": "bar"}


@app.websocket("/test")
async def ws(websocket: WebSocket):
    await websocket.accept()

    try:
        while True:
            data = await websocket.receive_text()
            await websocket.send_text(f"echo: {data}")
    except WebSocketDisconnect:
        pass


client = TestClient(app)


def test_middleware():
    response = client.get("/test/foo")

    assert response.status_code == 200
    assert response.json() == {"message": "bar"}


def test_middleware_using_websocket():
    with client.websocket_connect("/test") as websocket:
        websocket.send_text("test")
        data = websocket.receive_text()

        assert data == "echo: test"
