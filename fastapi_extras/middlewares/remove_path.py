import re

from starlette.types import ASGIApp, Receive, Scope, Send


class RemovePathMiddleware:
    def __init__(self, app: ASGIApp, path: str = "") -> None:
        self.app = app
        self.path = path.strip()

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or self.path == "":
            return await self.app(scope, receive, send)

        scope["path"] = re.sub(f"^({self.path})(/.*)?$", r"\g<2>", scope["path"])

        await self.app(scope, receive, send)
