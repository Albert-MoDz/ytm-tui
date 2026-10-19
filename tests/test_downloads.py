import os
import time

from ytm import ytdl
from ytm.models import Track


def wait(dm, timeout=60):
    t = time.time()
    while dm.active_count() and time.time() - t < timeout:
        time.sleep(0.2)


def test_download_tags_lrc_errors_and_duplicates(cfg, lib, media_server, monkeypatch):
    from ytm.lyrics import Lyrics

    monkeypatch.setattr(ytdl, "fetch_lyrics", lambda t: Lyrics(synced=[(0.0, "hola"), (65.5, "mundo")]))
    import mutagen

    events = []
    dm = ytdl.DownloadManager(cfg, lib, on_change=lambda j: events.append(j.status))
    ok = Track(id="AAAAAAAAAAA", title="Artista - Canción: Prueba/Uno (Official Video)", artist="ArtistaVEVO", url=media_server + "a.wav")
    bad = Track(id="CCCCCCCCCCC", title="Falla", artist="X", url=media_server + "noexiste.wav")
    j1, _ = dm.add(ok); j2, _ = dm.add(bad)
    assert dm.add(ok)[0] is None  # duplicado en curso
    wait(dm)

    assert j1.status == "done" and j1.path.endswith(".mp3") and os.path.getsize(j1.path) > 1000
    assert "/" not in os.path.basename(j1.path) and ":" not in os.path.basename(j1.path)
    assert j2.status == "error" and "404" in j2.message
    m = mutagen.File(j1.path, easy=True)
    assert list(m["title"]) == ["Canción: Prueba/Uno"] and list(m["artist"]) == ["Artista"]
    lrc = os.path.splitext(j1.path)[0] + ".lrc"
    content = open(lrc, encoding="utf-8").read()
    assert "[00:00.00]hola" in content and "[01:05.50]mundo" in content
    assert lib.local_path(ok) == j1.path
    assert dm.add(ok) == (None, "Ya está descargada")
    assert [t.path for t in lib.local_tracks()] == [j1.path]


def test_cancel_download(cfg, lib, media_server):
    dm = ytdl.DownloadManager(cfg, lib)
    cfg_workers = [Track(id=f"{i:011d}", title=f"x{i}", url=media_server + "a.wav") for i in range(8)]
    jobs = [dm.add(t)[0] for t in cfg_workers]
    dm.cancel(jobs[-1])
    wait(dm)
    assert jobs[-1].status == "cancelled"
    assert not [f for f in os.listdir(cfg["download_dir"]) if f.endswith(".part")]


def test_sponsorblock_option_only_for_youtube(cfg, lib):
    cfg["sponsorblock_download"] = True
    dm = ytdl.DownloadManager(cfg, lib)
    yt = ytdl.Job(Track(id="dQw4w9WgXcQ", title="a"))
    other = ytdl.Job(Track(id="AAAAAAAAAAA", title="a", url="http://127.0.0.1/a.wav"))
    keys = lambda job: [p["key"] for p in dm._opts(job).get("postprocessors", [])]
    assert "SponsorBlock" in keys(yt) and "ModifyChapters" in keys(yt)
    assert "SponsorBlock" not in keys(other)
