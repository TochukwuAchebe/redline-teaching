"""Sustained-load soak: keep a steady stream of reviews in flight for a fixed time against one server.

Fictional, randomised decks only. Measures time to finish per review, failures, API latency while the
queue is busy, and the server's memory over time (so a leak or a stuck queue shows up).

Usage: SOAK_BASE=http://127.0.0.1:8050 SOAK_SECONDS=1800 SOAK_IN_FLIGHT=2 SOAK_PID=<server pid> \
       SESSION_SECRET=<server's secret> PYTHONPATH=backend python eval/soak/soak.py > result.json
"""
import asyncio, io, json, os, random, statistics, subprocess, time

import httpx
from pptx import Presentation
from pptx.util import Inches

from app.auth import COOKIE, issue_token

BASE = os.environ.get('SOAK_BASE', 'http://127.0.0.1:8050')
SECONDS = int(os.environ.get('SOAK_SECONDS', '1800'))
IN_FLIGHT = int(os.environ.get('SOAK_IN_FLIGHT', '2'))
PID = os.environ.get('SOAK_PID')
WORDS = 'peer median target bonus salary equity grant review screen revenue margin growth committee incentive plan award vesting payout cycle market'.split()
NAMES = ['Alder', 'Birch', 'Cedar', 'Dune', 'Ember', 'Fjord', 'Grove', 'Heath', 'Isle', 'Juniper', 'Kestrel', 'Larch']
PPTX = 'application/vnd.openxmlformats-officedocument.presentationml.presentation'


def deck(n_slides, seed):
    rnd = random.Random(seed)
    prs = Presentation()
    for i in range(n_slides):
        if i % 3 == 2:
            s = prs.slides.add_slide(prs.slide_layouts[5])
            s.shapes.title.text = f'{rnd.choice(NAMES)} Corp {rnd.choice(WORDS)} sits {rnd.randint(2, 40)}% above the {rnd.choice(WORDS)} median'
            t = s.shapes.add_table(8, 4, Inches(1), Inches(1.6), Inches(8), Inches(4)).table
            for c, h in enumerate(['Company', 'Revenue ($MM)', 'Growth', 'Target bonus']):
                t.cell(0, c).text = h
            for r in range(1, 8):
                t.cell(r, 0).text = f'{rnd.choice(NAMES)} {rnd.choice(["Bio", "Labs", "Group", "Systems"])} {seed}-{i}-{r}'
                t.cell(r, 1).text = f'${rnd.randint(500, 20000):,}'
                t.cell(r, 2).text = f'{rnd.randint(-5, 30)}%'
                t.cell(r, 3).text = f'{rnd.randint(40, 150)}%'
        else:
            s = prs.slides.add_slide(prs.slide_layouts[1])
            s.shapes.title.text = f'{rnd.choice(NAMES)} {rnd.choice(WORDS)} {rnd.choice(WORDS)} review {seed}-{i}'
            body = s.placeholders[1].text_frame
            for b in range(rnd.randint(3, 6)):
                p = body.paragraphs[0] if b == 0 else body.add_paragraph()
                p.text = ' '.join(rnd.choice(WORDS) for _ in range(rnd.randint(6, 14))) + ('.' if rnd.random() < .3 else '')
    buf = io.BytesIO(); prs.save(buf); return buf.getvalue()


def rss_mb():
    if not PID:
        return None
    # The server and its LibreOffice children: total resident memory.
    out = subprocess.run(['ps', '-o', 'rss=', '-g', subprocess.run(['ps', '-o', 'pgid=', '-p', PID], capture_output=True, text=True).stdout.strip()],
                         capture_output=True, text=True).stdout.split()
    return round(sum(int(x) for x in out) / 1024, 1)


def pct(v, p):
    return v[min(len(v) - 1, int(p * (len(v) - 1)))] if v else None


async def main():
    probes, reviews, memory, failures = [], [], [], []
    t0 = time.perf_counter()
    seq = 0
    async with httpx.AsyncClient(base_url=BASE, cookies={COOKIE: issue_token()}, timeout=180) as c:
        stop = asyncio.Event()

        async def probe():
            while not stop.is_set():
                for path in ('/api/health', '/api/decks'):
                    t = time.perf_counter()
                    try:
                        code = (await c.get(path)).status_code
                    except Exception as e:
                        code = repr(e)[:60]
                    probes.append((round(time.perf_counter() - t0), path, round((time.perf_counter() - t) * 1000), code))
                memory.append((round(time.perf_counter() - t0), rss_mb()))
                await asyncio.sleep(2)

        async def worker(w):
            nonlocal seq
            while time.perf_counter() - t0 < SECONDS:
                seq += 1
                k, n = seq, random.Random(seq).choice([8, 12, 16, 24, 40])
                start = time.perf_counter()
                try:
                    while True:  # admission control: wait Retry-After, as the browser does
                        r = await c.post('/api/decks', files={'file': (f'soak-{k}.pptx', deck(n, k), PPTX)})
                        if r.status_code != 429:
                            break
                        await asyncio.sleep(int(r.headers.get('retry-after', '5')))
                    r.raise_for_status()
                    deck_id = r.json()['deck_id']
                    while True:
                        d = (await c.get(f'/api/decks/{deck_id}')).json()
                        if d.get('status') in {'done', 'error', 'cancelled'}:
                            break
                        await asyncio.sleep(1)
                    reviews.append({'n': k, 'slides': n, 'status': d['status'], 'started_s': round(start - t0),
                                    'seconds': round(time.perf_counter() - start, 1),
                                    'findings': len((d.get('result') or {}).get('findings', []))})
                except Exception as e:
                    failures.append({'n': k, 'at_s': round(time.perf_counter() - t0), 'error': repr(e)[:200]})

        prober = asyncio.create_task(probe())
        await asyncio.gather(*(worker(w) for w in range(IN_FLIGHT)))
        stop.set(); await prober

    def lat(path):
        v = sorted(ms for _, p, ms, code in probes if p == path and code == 200)
        return {'n': len(v), 'p50': pct(v, .5), 'p95': pct(v, .95), 'max': v[-1] if v else None}

    done = [r for r in reviews if r['status'] == 'done']
    per_slide = sorted(r['seconds'] / r['slides'] for r in done)
    mem = [m for _, m in memory if m is not None]
    third = max(1, len(mem) // 3)
    print(json.dumps({
        'config': {'base': BASE, 'seconds': SECONDS, 'in_flight': IN_FLIGHT},
        'wall_s': round(time.perf_counter() - t0),
        'reviews': {'finished': len(reviews), 'done': len(done), 'not_done': [r for r in reviews if r['status'] != 'done'],
                    'failures': failures, 'slides_total': sum(r['slides'] for r in done),
                    'seconds_p50': pct(sorted(r['seconds'] for r in done), .5),
                    'seconds_p95': pct(sorted(r['seconds'] for r in done), .95),
                    'seconds_per_slide_p50': round(pct(per_slide, .5), 2) if per_slide else None},
        'api_ms_while_busy': {'/api/health': lat('/api/health'), '/api/decks': lat('/api/decks')},
        'probe_errors': [p for p in probes if p[3] != 200][:10],
        'memory_mb': {'first_third_median': statistics.median(mem[:third]) if mem else None,
                      'last_third_median': statistics.median(mem[-third:]) if mem else None,
                      'max': max(mem) if mem else None},
        'timeline': reviews,
    }, indent=1))


asyncio.run(main())
