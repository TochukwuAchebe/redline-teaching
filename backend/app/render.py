"""PPTX -> one PNG per slide, via LibreOffice (PDF) and PyMuPDF."""
import shutil
import subprocess
import tempfile
import time
import os
import signal
from pathlib import Path

import pymupdf

TARGET_WIDTH_PX = 1600
TOTAL_FILE = "total.txt"
# LibreOffice defaults ExportHiddenSlides to false. Extraction includes hidden
# slides, so omitting this shifts every later image away from its finding IDs.
# https://help.libreoffice.org/latest/en-US/text/shared/guide/pdf_params.html
PDF_FILTER = 'pdf:impress_pdf_Export:{"ExportHiddenSlides":{"type":"boolean","value":"true"}}'


def _soffice() -> str:
    for name in ("soffice", "libreoffice"):
        if path := shutil.which(name):
            return path
    raise RuntimeError("LibreOffice (soffice) is not installed")


def render_slides(pptx_path: Path, out_dir: Path, cancelled=None) -> int:
    """Render every slide to out_dir/{n}.png (1-based). Returns the slide count."""
    out_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        # A private profile dir lets several conversions run at once.
        profile = Path(tmp, "profile").as_uri()
        with tempfile.TemporaryFile() as output:
            proc = subprocess.Popen([_soffice(), f"-env:UserInstallation={profile}", "--headless",
                "--convert-to", PDF_FILTER, "--outdir", tmp, str(pptx_path)],
                stdout=output, stderr=output, start_new_session=True)
            started = time.monotonic()
            try:
                while proc.poll() is None:
                    if cancelled and cancelled(): raise RuntimeError('Rendering cancelled')
                    if time.monotonic()-started > 240: raise TimeoutError('LibreOffice render timed out')
                    time.sleep(.1)
                if proc.returncode: raise RuntimeError('LibreOffice could not render this file')
            finally:
                if proc.poll() is None:
                    os.killpg(proc.pid, signal.SIGTERM)
                    try: proc.wait(timeout=2)
                    except subprocess.TimeoutExpired:
                        os.killpg(proc.pid, signal.SIGKILL); proc.wait()
        pdf = next(Path(tmp).glob("*.pdf"), None)
        if pdf is None:
            raise RuntimeError("LibreOffice did not produce a slide PDF")
        with pymupdf.open(pdf) as doc:
            # The total lets the UI show "n of m slides ready" while images appear.
            (out_dir / TOTAL_FILE).write_text(str(doc.page_count))
            for i, page in enumerate(doc, 1):
                if cancelled and cancelled(): raise RuntimeError('Rendering cancelled')
                zoom = TARGET_WIDTH_PX / page.rect.width
                part = out_dir / f"{i}.png.part"
                page.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom)).save(part, output="png")
                part.replace(out_dir / f"{i}.png")  # never serve a half-written image
            return doc.page_count
