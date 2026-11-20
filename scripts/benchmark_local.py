"""Isolated HTTP/worker recovery measurement; never targets Azure or a real review.

Requires the dedicated local PostgreSQL test service (127.0.0.1:55432), credentials
via PGPASSFILE, and LibreOffice. Uses real extraction/rendering; AI is disabled.
Creates a random private test schema, temporary decks and local child processes.
Outputs summary measurements, not credentials. Removes only its own schema.
"""
from __future__ import annotations
import argparse
import asyncio
import hashlib
import io
import json
import os
from pathlib import Path
import platform
import secrets
import signal
import socket
import statistics
import subprocess
import sys
import tempfile
import time
import uuid

import httpx
import psycopg
from pptx import Presentation

ROOT = Path(__file__).resolve().parents[1]
LOCAL_DB = 'postgresql://postgres@127.0.0.1:55432/redline'


def synthetic_deck(slides=12):
    deck = Presentation()
    for number in range(slides):
        slide = deck.slides.add_slide(deck.slide_layouts[1])
        slide.shapes.title.text = f'Delivery improved in region {number + 1}'
        slide.placeholders[1].text = 'Average delivery time fell from five days to three.\nCustomer satisfaction increased.'
    stream = io.BytesIO()
    deck.save(stream)
    return stream.getvalue()


def percentile(values, fraction):
    return sorted(values)[min(len(values)-1, int((len(values)-1)*fraction))] if values else None


async def benchmark(output):
    schema = 'capacity_' + uuid.uuid4().hex
    with psycopg.connect(LOCAL_DB, autocommit=True) as conn:
        conn.execute('CREATE SCHEMA ' + schema)
    children = []
    files = []
    timings = []
    events = []
    started = time.monotonic()
    completed_at = {}
    def record(message):
        events.append(message)
        print(message, flush=True)
    result = {'source_commit': subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip(),
              'harness_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
              'scope': 'Local HTTP + PostgreSQL + real LibreOffice; local object adapter; no AI or Azure',
              'platform': {'system': platform.system(), 'architecture': platform.machine(), 'logical_cpus': os.cpu_count()},
              'queue_limit': 4, 'worker_concurrency': 1, 'workspace': 'capacity', 'events': events}
    with tempfile.TemporaryDirectory(prefix='redline-capacity-') as temp:
        directory = Path(temp).resolve()
        env = os.environ.copy()
        for key in ['ANTHROPIC_API_KEY','AZURE_STORAGE_ACCOUNT_URL','AZURE_STORAGE_CONTAINER','REDLINE_TEST_DATABASE_URL']:
            env.pop(key, None)
        env.update(PYTHONPATH=str(ROOT/'backend'), DATA_DIR=str(directory/'data'),
                   DATABASE_URL=LOCAL_DB+'?options=-csearch_path%3D'+schema,
                   APP_PASSWORD=secrets.token_urlsafe(24), SESSION_SECRET=secrets.token_urlsafe(48),
                   COOKIE_SECURE='0', REDLINE_DURABLE_JOBS='1', REDLINE_RUN_WORKERS='0',
                   MAX_CONCURRENT_JOBS='1', MAX_QUEUED_JOBS='4', REDLINE_WORKSPACE='capacity')
        with socket.socket() as sock:
            sock.bind(('127.0.0.1',0)); port=sock.getsockname()[1]
        base=f'http://127.0.0.1:{port}'

        def spawn(name, command):
            log=open(directory/(name+'.log'),'w'); files.append(log)
            child=subprocess.Popen([sys.executable,*command],cwd=ROOT,env=env,stdout=log,stderr=subprocess.STDOUT)
            children.append(child)
            return child

        def stop(child, graceful=False):
            if child.poll() is None:
                child.send_signal(signal.SIGINT if graceful else signal.SIGTERM)
                try: child.wait(timeout=15)
                except subprocess.TimeoutExpired:
                    child.kill(); child.wait(timeout=5)

        async def request(client, method, path, **kwargs):
            tick=time.monotonic()
            response=await client.request(method,path,**kwargs)
            timings.append({'method':method,'path':path.split('/api/decks/')[0] or '/',
                            'ms':(time.monotonic()-tick)*1000,'status':response.status_code})
            return response

        async def ready(client):
            deadline=time.monotonic()+20
            while time.monotonic()<deadline:
                try:
                    if (await client.get('/api/health')).status_code==200:return
                except httpx.TransportError:pass
                await asyncio.sleep(.1)
            raise RuntimeError('Local API did not start')

        async def wait_done(client, job, timeout=100):
            deadline=time.monotonic()+timeout
            while time.monotonic()<deadline:
                response=await request(client,'GET','/api/decks/'+job);response.raise_for_status()
                data=response.json()
                if data['status']=='done':
                    completed_at[job]=time.monotonic()
                    return data
                if data['status'] in ['error','cancelled']:raise RuntimeError('Review ended unexpectedly: '+data['status'])
                await asyncio.sleep(.15)
            raise RuntimeError('Local review exceeded measurement deadline')

        try:
            api=spawn('api',['-m','uvicorn','app.main:app','--host','127.0.0.1','--port',str(port),'--log-level','warning'])
            async with httpx.AsyncClient(base_url=base,timeout=30) as client:
                await ready(client)
                assert (await request(client,'POST','/api/login',json={'password':env['APP_PASSWORD']})).status_code==200
                payload=synthetic_deck()
                result.update(slides_per_deck=12, deck_bytes=len(payload), deck_sha256=hashlib.sha256(payload).hexdigest())
                async def upload(name):
                    response=await request(client,'POST','/api/decks',files={'file':(name,payload,'application/vnd.openxmlformats-officedocument.presentationml.presentation')})
                    return response
                seed=(await upload('completed-baseline.pptx')).json()['deck_id']
                worker=spawn('worker-seed',['-m','app.worker'])
                seed_start=time.monotonic(); completed=await wait_done(client,seed)
                result['baseline_completion_seconds']=time.monotonic()-seed_start
                stop(worker,graceful=True)
                record('Baseline 12-slide review completed using real extraction/rendering.')
                # Two at a time matches the documented upload reception bound.
                jobs=[]
                for batch in range(2):
                    responses=await asyncio.gather(*(upload(f'burst-{batch}-{i}.pptx') for i in range(2)))
                    for response in responses:
                        response.raise_for_status();jobs.append(response.json()['deck_id'])
                full=await upload('overflow.pptx')
                assert full.status_code==409 and 'queue is full' in full.json()['detail']
                result['queue_full_status']=full.status_code
                record('Four waiting reviews admitted; fifth rejected with a clear queue-full response.')
                cancel=await request(client,'POST',f'/api/decks/{jobs[-1]}/cancel');assert cancel.json()['cancelled']
                waiting=(await request(client,'GET','/api/decks/'+jobs[-1])).json()
                assert waiting['status']=='cancelled' and waiting['result'] is None
                record('Waiting review cancelled before processing; no result published.')
                stop(api)
                api=spawn('api-restarted',['-m','uvicorn','app.main:app','--host','127.0.0.1','--port',str(port),'--log-level','warning'])
                await ready(client)
                # Same signed session must survive, with queued work and saved baseline.
                assert (await request(client,'GET','/api/session')).status_code==200
                assert (await request(client,'GET','/api/decks/'+seed)).json()['status']=='done'
                record('API process restarted; existing session, completed review and queued work preserved.')
                worker=spawn('worker-interrupted',['-m','app.worker'])
                deadline=time.monotonic()+20
                while time.monotonic()<deadline:
                    state=(await request(client,'GET','/api/decks/'+jobs[0])).json()['status']
                    if state=='rendering':break
                    await asyncio.sleep(.05)
                assert state=='rendering'
                recovery_started=time.monotonic()
                stop(worker,graceful=True)
                record('Worker interrupted during rendering; replacement must recover through the real 60-second lease.')
                worker=spawn('worker-replacement',['-m','app.worker'])
                ids=[f['id'] for f in completed['result']['findings']][:32]
                assert len(ids)>=12
                patch_start=time.monotonic()
                patches=await asyncio.gather(*(request(client,'PUT',f'/api/decks/{seed}/decisions',json={fid:'accepted'}) for fid in ids))
                assert all(p.status_code==200 for p in patches)
                saved=(await request(client,'GET','/api/decks/'+seed)).json()['decisions']
                assert all(saved.get(fid)=='accepted' for fid in ids)
                result['concurrent_decision_writes']=len(ids)
                result['decision_batch_seconds']=time.monotonic()-patch_start
                record('Concurrent decisions on completed review saved without lost updates while replacement worker serves queue.')
                recovered=await asyncio.gather(*(wait_done(client,job) for job in jobs[:-1]))
                assert all(d['rendered']==12 and d['slides_total']==12 for d in recovered)
                for number in range(1,13):
                    image=await request(client,'GET',f'/api/decks/{jobs[0]}/slides/{number}.png')
                    image.raise_for_status();assert image.content.startswith(b'\x89PNG\r\n\x1a\n')
                with psycopg.connect(env['DATABASE_URL']) as conn:
                    attempts=conn.execute('SELECT attempts FROM capacity_queue WHERE id=%s',(jobs[0],)).fetchone()[0]
                assert attempts==2
                cancelled=(await request(client,'GET','/api/decks/'+jobs[-1])).json()
                assert cancelled['status']=='cancelled' and cancelled['result'] is None
                final_saved=(await request(client,'GET','/api/decks/'+seed)).json()['decisions']
                assert all(final_saved.get(fid)=='accepted' for fid in ids)
                result.update(worker_recovery_seconds=completed_at[jobs[0]]-recovery_started,
                              recovered_job_attempts=attempts,completed_burst_reviews=len(recovered),cancelled_reviews=1,
                              all_recovered_previews_available=True,passed=True)
                record('All three remaining reviews completed; interrupted review claimed twice; cancellation and decisions remained intact.')
        except BaseException as exc:
            result.update(passed=False,error_type=type(exc).__name__,error=str(exc))
            raise
        finally:
            for child in reversed(children):stop(child,graceful=True)
            for log in files:log.close()
            result['elapsed_seconds']=time.monotonic()-started
            successes=[r['ms'] for r in timings if r['status']==200]
            result['http']={'samples':len(timings),'successful_samples':len(successes),
                            'median_ms':statistics.median(successes) if successes else None,
                            'p95_ms':percentile(successes,.95),'max_ms':max(successes) if successes else None,
                            'status_counts':{str(code):sum(r['status']==code for r in timings) for code in sorted({r['status'] for r in timings})}}
            output.parent.mkdir(parents=True,exist_ok=True);output.write_text(json.dumps(result,indent=2)+'\n')
            with psycopg.connect(LOCAL_DB,autocommit=True) as conn:conn.execute('DROP SCHEMA '+schema+' CASCADE')
            print(json.dumps(result),flush=True)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--output',type=Path,required=True)
    asyncio.run(benchmark(parser.parse_args().output))
