from starlette.types import ASGIApp, Receive, Scope, Send


class RemovePathMiddleware:
    def __init__(self, app: ASGIApp, path: str = "") -> None:
        self.app = app
        self.path = path if not path.endswith("/") else path[:-1]

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            return await self.app(scope, receive, send)

        scope["path"] = scope["path"].replace(self.path, "", 1)

        await self.app(scope, receive, send)
