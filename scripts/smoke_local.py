"""Exercise a local product container without loading an Anthropic credential.

Start the server without ANTHROPIC_API_KEY. Supply APP_PASSWORD through the
process environment. Reports contain counts and filenames, never slide text,
cookies or credentials. Temporary uploads are retained in the test container
until it is removed.
"""
from __future__ import annotations

import argparse
import csv
import io
import json
import os
import time
from pathlib import Path

import httpx


def require(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def wait_for_health(client: httpx.Client) -> None:
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        try:
            if client.get('/api/health').status_code == 200:
                return
        except httpx.HTTPError:
            pass
        time.sleep(1)
    raise RuntimeError('API failed to become healthy within 60 seconds')


def review_deck(client: httpx.Client, source: Path, expected_slides: int) -> dict:
    with source.open('rb') as handle:
        response = client.post('/api/decks', files={'file': (source.name, handle)})
    require(response.status_code == 200, f'{source.name}: upload failed')
    deck_id = response.json()['deck_id']
    endpoint = f'/api/decks/{deck_id}'
    states: set[str] = set()
    start = time.monotonic()
    deck = {}
    while time.monotonic() - start < 300:
        response = client.get(endpoint)
        require(response.status_code == 200, 'Deck status request failed')
        deck = response.json()
        states.add(deck['status'])
        if deck['status'] in ('done', 'error'):
            break
        time.sleep(0.4)
    require(deck.get('status') == 'done', f'{source.name}: analysis did not complete')
    require(deck['slide_count'] == expected_slides, f'{source.name}: rendered slide count differs')
    require(deck['result']['slide_count'] == expected_slides, f'{source.name}: extracted slide count differs')
    for number in range(1, expected_slides + 1):
        response = client.get(f'{endpoint}/slides/{number}.png')
        require(response.status_code == 200 and response.content.startswith(b'\x89PNG\r\n\x1a\n'),
                f'{source.name}: slide {number} is not a served PNG')
    findings = deck['result']['findings']
    if not findings:
        response = client.get(f'{endpoint}/export.csv')
        require(response.status_code == 200, 'Empty review CSV export failed')
        require(len(list(csv.reader(io.StringIO(response.text)))) == 1,
                'Empty review CSV must contain only its header')
        return {'file': source.name, 'slides': expected_slides, 'findings': 0,
                'observed_states': sorted(states), 'seconds': round(time.monotonic() - start, 2),
                'decision_persisted': None, 'accepted_csv_rows': 0}
    patch = {findings[0]['id']: 'accepted'}
    if len(findings) > 1:
        patch[findings[1]['id']] = 'rejected'
    require(client.put(f'{endpoint}/decisions', json=patch).status_code == 200, 'Decision update failed')
    require(client.get(endpoint).json()['decisions'] == patch, 'Decisions did not persist after reload')
    response = client.get(f'{endpoint}/export.csv')
    require(response.status_code == 200, 'CSV export failed')
    rows = list(csv.reader(io.StringIO(response.text)))
    require(len(rows) == 2 and rows[1][2] == findings[0]['title'], 'CSV must contain only the accepted finding')
    with httpx.Client(base_url=str(client.base_url)) as anonymous:
        for suffix in ('', '/slides/1.png', '/export.csv'):
            require(anonymous.get(endpoint + suffix).status_code == 401, 'Unauthenticated deck data was accessible')
        require(anonymous.put(f'{endpoint}/decisions', json=patch).status_code == 401,
                'Unauthenticated decisions were accepted')
    return {'file': source.name, 'slides': expected_slides, 'findings': len(findings),
            'observed_states': sorted(states), 'seconds': round(time.monotonic() - start, 2),
            'decision_persisted': True, 'accepted_csv_rows': 1}


def main() -> None:
    parser = argparse.ArgumentParser(description='Smoke-test a local Redline container with AI disabled.')
    parser.add_argument('--base-url', default='http://127.0.0.1:18080')
    parser.add_argument('--deck', action='append', required=True, help='PPTX_PATH:EXPECTED_SLIDES; repeat as needed')
    parser.add_argument('--report', type=Path, required=True)
    args = parser.parse_args()
    password = os.environ.get('APP_PASSWORD')
    if not password:
        parser.error('APP_PASSWORD must be supplied through the environment')
    cases = []
    for value in args.deck:
        path, count = value.rsplit(':', 1)
        cases.append((Path(path), int(count)))
    with httpx.Client(base_url=args.base_url.rstrip('/'), timeout=30) as client:
        wait_for_health(client)
        response = client.get('/')
        require(response.status_code == 200 and '<div id="root">' in response.text, 'Built frontend is not served')
        require(client.get('/api/session').status_code == 401, 'Anonymous session must be rejected')
        require(client.post('/api/login', json={'password': 'wrong'}).status_code == 401, 'Wrong password was accepted')
        response = client.post('/api/login', json={'password': password})
        require(response.status_code == 200 and 'HttpOnly' in response.headers.get('set-cookie', ''), 'Login/cookie failed')
        require(client.get('/api/session').status_code == 200, 'Signed session not accepted')
        require(client.post('/api/decks', files={'file': ('bad.txt', b'bad')}).status_code == 400, 'Invalid extension was accepted')
        results = [review_deck(client, path, count) for path, count in cases]
        require(client.post('/api/logout').status_code == 200, 'Logout failed')
        require(client.get('/api/session').status_code == 401, 'Session remained usable after logout')
    args.report.write_text(json.dumps(results, indent=2))
    print(json.dumps(results, indent=2))


if __name__ == '__main__':
    main()
