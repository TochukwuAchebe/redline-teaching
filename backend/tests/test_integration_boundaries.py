"""Regression checks for C1 boundaries required by the deployable B5 runtime."""
import asyncio
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import threading
import time

from fastapi import HTTPException
import pytest


def test_oversized_upload_stops_reading_at_limit_plus_one(monkeypatch):
    from app import main
    monkeypatch.setattr(main, 'MAX_UPLOAD', 8)
    created=[]
    monkeypatch.setattr(main.jobs, 'create_deck', lambda *args: created.append(args))

    class LargeUpload:
        filename='oversized.pptx'
        closed=False
        position=0
        requests=[]
        async def read(self,size=-1):
            assert size>0, 'Unbounded read would materialize the entire upload'
            self.requests.append(size)
            self.position+=size
            return b'x'*size
        async def close(self):
            self.closed=True

    upload=LargeUpload()
    with pytest.raises(HTTPException) as error:
        asyncio.run(main.upload(upload))
    assert error.value.status_code==413
    assert upload.position==9 and upload.requests==[9]
    assert upload.closed and not created


def test_concurrent_decisions_preserve_every_accepted_finding(tmp_path,monkeypatch):
    from app import jobs
    monkeypatch.setattr(jobs,'DATA_DIR',tmp_path)
    directory=tmp_path/'concurrent';directory.mkdir()
    start=threading.Event()
    original_read=jobs._read

    def deliberately_slow_read(path,default=None):
        value=original_read(path,default)
        time.sleep(.003)  # exposes the old read/modify/write race
        return value

    monkeypatch.setattr(jobs,'_read',deliberately_slow_read)
    def accept(number):
        start.wait()
        return jobs.update_decisions('concurrent',{f'finding-{number}':'accepted'})
    with ThreadPoolExecutor(max_workers=16) as pool:
        futures=[pool.submit(accept,n) for n in range(32)]
        start.set()
        for future in futures:future.result(timeout=10)
    saved=json.loads((directory/'decisions.json').read_text())
    assert saved=={f'finding-{n}':'accepted' for n in range(32)}
    assert not list(directory.glob('*.tmp'))


def test_atomic_writes_have_independent_temporary_files(tmp_path):
    from app.jobs import _write
    target=tmp_path/'shared.json'
    with ThreadPoolExecutor(max_workers=8) as pool:
        futures=[pool.submit(_write,target,{'writer':n,'payload':'x'*1000}) for n in range(24)]
        for future in futures:future.result(timeout=10)
    assert json.loads(target.read_text())['writer'] in range(24)
    assert not list(tmp_path.glob('*.tmp'))


def test_renderer_explicitly_preserves_hidden_slides():
    from app.render import PDF_FILTER
    extension,export_filter,options=PDF_FILTER.split(':',2)
    assert extension=='pdf' and export_filter=='impress_pdf_Export'
    assert json.loads(options)['ExportHiddenSlides']=={'type':'boolean','value':'true'}


@pytest.mark.skipif(os.environ.get('REDLINE_RENDER_INTEGRATION')!='1',reason='Opt in to real LibreOffice rendering')
def test_real_renderer_keeps_hidden_middle_slide(tmp_path):
    from pptx import Presentation
    from pptx.dml.color import RGBColor
    from app.render import render_slides
    import pymupdf
    prs=Presentation()
    for number,colour in enumerate(['FF0000','00FF00','0000FF'],1):
        slide=prs.slides.add_slide(prs.slide_layouts[6])
        slide.background.fill.solid()
        slide.background.fill.fore_color.rgb=RGBColor.from_string(colour)
        if number==2:slide._element.set('show','0')
    source=tmp_path/'hidden-middle.pptx';prs.save(source)
    output=tmp_path/'slides'
    assert render_slides(source,output)==3
    colours=[]
    for number in range(1,4):
        image=pymupdf.Pixmap(str(output/f'{number}.png'))
        colours.append(image.pixel(image.width//2,image.height//2)[:3])
    assert colours==[(255,0,0),(0,255,0),(0,0,255)]


def test_job_capacity_keeps_waiting_decks_queued(tmp_path,monkeypatch):
    from app import jobs
    from weakref import WeakKeyDictionary
    monkeypatch.setattr(jobs,'DATA_DIR',tmp_path)
    monkeypatch.setattr(jobs,'MAX_CONCURRENT_JOBS',1)
    monkeypatch.setattr(jobs,'_slots_by_loop',WeakKeyDictionary())
    ids=[jobs.create_deck(f'deck-{n}.pptx',b'fixture') for n in range(3)]

    async def scenario():
        started=asyncio.Event();release=asyncio.Event()
        active=0;peak=0
        async def fake_process(deck_id):
            nonlocal active,peak
            active+=1;peak=max(peak,active)
            jobs.set_status(deck_id,status='rendering')
            started.set()
            await release.wait()
            active-=1
            jobs.set_status(deck_id,status='done')
        monkeypatch.setattr(jobs,'_process_active',fake_process)
        tasks=[asyncio.create_task(jobs.process(deck_id)) for deck_id in ids]
        await asyncio.wait_for(started.wait(),1)
        await asyncio.sleep(0)
        statuses=[jobs.get_deck(deck_id)['status'] for deck_id in ids]
        assert statuses.count('rendering')==1 and statuses.count('queued')==2
        release.set()
        await asyncio.gather(*tasks)
        assert peak==1
        assert all(jobs.get_deck(deck_id)['status']=='done' for deck_id in ids)
    asyncio.run(scenario())


def test_unicode_export_filename_is_valid_http_header(monkeypatch):
    from app import main
    from urllib.parse import unquote
    monkeypatch.setattr(main,'_deck_or_404',lambda deck_id:{
        'filename':'分析.pptx','result':{'findings':[]},'decisions':{}})
    response=main.export_csv('fixture')
    disposition=response.headers['content-disposition']
    assert disposition.startswith("attachment; filename*=UTF-8''")
    assert unquote(disposition.split("''",1)[1])=='分析 - review.csv'


@pytest.mark.parametrize('text', ['=1+1', '+SUM(A1:A2)', '-1+2', '@SUM(A1:A2)', ' \t=1+1', '\r@SUM(A1:A2)'])
def test_export_keeps_formula_like_slide_text_literal(text, monkeypatch):
    import csv
    import io
    from app import main
    monkeypatch.setattr(main, '_deck_or_404', lambda deck_id: {
        'filename': 'review.pptx', 'decisions': {'finding': 'accepted'},
        'result': {'findings': [{'id': 'finding', 'slide': 1, 'severity': 'warning',
                                 'title': 'Issue', 'message': text, 'actual': text}]}})
    response = main.export_csv('fixture')
    rows = list(csv.reader(io.StringIO(response.body.decode())))
    assert rows[1][0] == '1'
    assert rows[1][3] == "'" + text and rows[1][5] == "'" + text
    assert rows[1][2] == 'Issue'


def test_invalid_decision_is_rejected_without_deleting_existing_value(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from app import jobs, main
    monkeypatch.setenv('APP_PASSWORD', 'test-password')
    monkeypatch.setattr(jobs, 'DATA_DIR', tmp_path)
    deck_id = jobs.create_deck('fixture.pptx', b'fixture')
    jobs.update_decisions(deck_id, {'finding': 'accepted'})
    with TestClient(main.app) as client:
        assert client.post('/api/login', json={'password': 'test-password'}).status_code == 200
        response = client.put(f'/api/decks/{deck_id}/decisions', json={'finding': 'accept'})
        assert response.status_code == 422
        assert client.get(f'/api/decks/{deck_id}').json()['decisions'] == {'finding': 'accepted'}
