"""Fixtures: carpeta de config aislada, servidor HTTP local con audios, y yt-dlp/YouTube simulados."""
import http.server
import os
import shutil
import subprocess
import sys
import tempfile
import threading
from functools import partial
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

HOME = tempfile.mkdtemp(prefix="ytm-test-")
os.environ["YTM_HOME"] = HOME  # debe fijarse antes de importar ytm


@pytest.fixture(scope="session")
def media_server():
    """Sirve dos audios largos (40 s) y uno corto (2 s) por HTTP local; sustituye a YouTube."""
    d = Path(tempfile.mkdtemp(prefix="ytm-media-"))
    for name, freq, secs in (("a.wav", 440, 40), ("b.wav", 660, 40), ("c.wav", 880, 2)):
        subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-f", "lavfi", "-i",
                        f"sine=frequency={freq}:duration={secs}", str(d / name)], check=True)

    class Quiet(http.server.SimpleHTTPRequestHandler):
        def log_message(self, *a):
            pass

    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), partial(Quiet, directory=str(d)))
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}/"
    httpd.shutdown()


@pytest.fixture()
def cfg(tmp_path):
    from ytm.config import Config

    c = Config()
    c["download_dir"] = str(tmp_path / "dl")
    c["media_keys"] = False
    c["lyrics"] = False
    os.makedirs(c["download_dir"], exist_ok=True)
    return c


@pytest.fixture()
def lib(cfg, tmp_path):
    from ytm import library

    library_path = tmp_path / "library.json"
    l = library.Library(cfg)
    l.path = library_path
    return l


@pytest.fixture(autouse=True)
def clean_session():
    for f in ("session.json", "config.json", "library.json"):
        p = Path(HOME) / f
        if p.exists():
            p.unlink()
    yield
