import asyncio
import pytest
from app.limits import RequestLimits
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient


def test_streaming_body_is_bounded_even_without_content_length():
    api = FastAPI()
    api.add_middleware(RequestLimits)

    @api.post('/api/guidelines')
    async def accept(request: Request):
        await request.body()
        return {'ok': True}

    with TestClient(api) as client:
        # Generator transport has no content-length declaration to trust.
        response = client.post('/api/guidelines', content=(b'x' * 65536 for _ in range(17)))
        assert response.status_code == 413
        assert client.post('/api/guidelines', content=b'small').status_code == 200


def test_excess_upload_is_rejected_before_the_body_is_parsed():
    async def exercise():
        entered = asyncio.Event()
        release = asyncio.Event()
        count = 0

        async def app(scope, receive, send):
            nonlocal count
            count += 1
            if count == 2:
                entered.set()
            await release.wait()

        limited = RequestLimits(app)
        scope = {'type': 'http', 'method': 'POST', 'path': '/api/decks', 'headers': []}

        async def receive():
            raise AssertionError('Over-capacity body must not be read')

        messages = []
        async def send(message):
            messages.append(message)

        tasks = [asyncio.create_task(limited(scope, receive, send)) for _ in range(2)]
        await entered.wait()
        await limited(scope, receive, send)
        assert count == 2 and messages[0]['status'] == 429
        release.set()
        await asyncio.gather(*tasks)
        assert not limited.slots[asyncio.get_running_loop()].locked()
    asyncio.run(exercise())
