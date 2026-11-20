"""Bound request bodies before multipart parsing and reject excess upload admission."""
import asyncio
from weakref import WeakKeyDictionary
from starlette.exceptions import HTTPException
from starlette.responses import JSONResponse


class RequestLimits:
    def __init__(self, app):
        self.app = app
        self.slots = WeakKeyDictionary()

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope["method"] not in {"POST", "PUT", "PATCH"}:
            return await self.app(scope, receive, send)
        upload = scope["method"] == "POST" and scope["path"] == "/api/decks"
        budget = 51 * 1024 * 1024 if upload else 1024 * 1024
        headers = dict(scope.get("headers", []))
        try:
            declared = int(headers.get(b"content-length", b"0"))
            if declared < 0:
                raise ValueError
        except ValueError:
            return await JSONResponse({"detail": "Invalid request size"}, 400)(scope, receive, send)
        if declared > budget:
            return await JSONResponse({"detail": "This request is too large. Upload one deck up to 50 MB, or make a smaller change."}, 413)(scope, receive, send)
        slot = None
        if upload:
            loop = asyncio.get_running_loop()
            slot = self.slots.setdefault(loop, asyncio.Semaphore(2))
            if slot.locked():
                return await JSONResponse({"detail": "Two uploads are already being received. Wait for one to finish, then try again."}, 429, headers={"Retry-After": "5"})(scope, receive, send)
            await slot.acquire()
        total = 0

        async def bounded_receive():
            nonlocal total
            message = await receive()
            if message["type"] == "http.request":
                total += len(message.get("body", b""))
                if total > budget:
                    raise HTTPException(413, "This request is too large. Split it into smaller files or changes.")
            return message

        try:
            await self.app(scope, bounded_receive, send)
        finally:
            if slot:
                slot.release()
