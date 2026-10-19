import hashlib
import http.server
import json
import os
import subprocess
import threading
import time
import urllib.parse

import pytest

from ytm import integrations, playlist_io, sponsorblock, tags, visualizer, ytdl
from ytm.lyrics import Lyrics, guess_names, parse_lrc
from ytm.models import Track, fmt_time
from ytm.player import PlayQueue, Player, build_audio_filter
from ytm.session import load_session, save_session


def T(n, **kw):
    return Track(id=f"{n:011d}", title=f"t{n}", artist="a", **kw)


# ------------------------------------------------------------------ cola
def test_queue_navigation_and_repeat():
    q = PlayQueue()
    q.set([T(i) for i in range(4)], 0)
    assert q.next().title == "t1" and q.next().title == "t2"
    assert q.prev().title == "t1"
    assert q.next(auto=True).title == "t2" and q.next(auto=True).title == "t3"
    assert q.next(auto=True) is None
    q.repeat = "all"
    assert q.next(auto=True).title == "t0"
    q.repeat = "one"
    assert q.next(auto=True).title == "t0"
    assert q.next().title == "t1"  # manual avanza aunque sea "repetir una"


def test_queue_shuffle_never_repeats_until_done():
    q = PlayQueue()
    q.set([T(i) for i in range(6)], 0)
    q.set_shuffle(True)
    seen = [q.current.title]
    while (t := q.next(auto=True)):
        seen.append(t.title)
    assert sorted(seen) == [f"t{i}" for i in range(6)]


def test_queue_edit_operations():
    q = PlayQueue()
    q.set([T(i) for i in range(5)], 2)
    q.move(2, -1)
    assert q.index == 1 and [t.title for t in q.items] == ["t0", "t2", "t1", "t3", "t4"]
    q.move(0, 3)
    assert q.current.title == "t2" and q.index == 0
    assert q.move(0, -5) == 0
    q.add_next(T(9))
    assert q.items[1].title == "t9"
    q.remove(0)
    assert q.index == 0
    q.clear_upcoming()
    assert len(q.items) == 1


# ------------------------------------------------------------------ varios
def test_lyrics_and_names():
    ly = Lyrics(synced=parse_lrc("[00:01.50] hola\n[00:10.00]mundo\n[01:02.25][01:30.00]coro"))
    assert len(ly.synced) == 4 and ly.index_at(0) == -1 and ly.index_at(11) == 1
    assert guess_names(Track(title="Shakira - Waka Waka (Official Video) [HD]", artist="ShakiraVEVO")) == ("Shakira", "Waka Waka")
    assert fmt_time(3725) == "1:02:05"


def test_entry_mapping_and_url_detection():
    t = ytdl.entry_to_track({"id": "dQw4w9WgXcQ", "title": "x", "uploader": "Rick - Topic", "duration": 212, "album": "Alb"})
    assert t.artist == "Rick" and t.album == "Alb"
    assert ytdl.entry_to_track({"id": "UC12345"}) is None
    assert ytdl.is_url("youtu.be/abc") and not ytdl.is_url("daddy yankee")
    pl = ytdl.entry_to_collection({"url": "https://www.youtube.com/playlist?list=PL1", "title": "Mix", "playlist_count": 12})
    ch = ytdl.entry_to_collection({"url": "https://www.youtube.com/@artista", "title": "Artista"})
    assert pl.kind == "playlist" and "12" in pl.artist and ch.kind == "channel"
    assert ytdl.entry_to_collection({"url": "https://example.com/x", "title": "x"}) is None


def test_library_persistence_and_features(cfg, lib):
    from ytm.library import Library

    tr = T(1)
    assert lib.toggle_favorite(tr) and not lib.toggle_favorite(tr)
    assert lib.create_playlist("Rock") and not lib.create_playlist("Rock")
    for i in range(3):
        lib.add_to_playlist("Rock", T(i))
    assert lib.move_in_playlist("Rock", 0, 2) == 2 and [t.title for t in lib.playlists["Rock"]] == ["t1", "t2", "t0"]
    assert lib.toggle_autodl("Rock") and not lib.toggle_autodl("Rock")
    sub = Track(title="Lista", url="https://www.youtube.com/playlist?list=PL1", kind="playlist")
    assert lib.toggle_subscription(sub) and lib.is_subscribed(sub.url)
    lib.add_search("bad bunny"); lib.add_search("queen"); lib.add_search("Bad Bunny")
    assert lib.searches == ["Bad Bunny", "queen"]
    lib.add_history(T(1)); lib.add_history(T(2)); lib.add_history(T(1))
    assert [t.title for t in lib.history] == ["t1", "t2"]
    lib2 = Library(cfg); lib2.path = lib.path; lib2._load()
    assert lib2.is_subscribed(sub.url) and lib2.searches[0] == "Bad Bunny"


def test_session_roundtrip():
    q = PlayQueue(); q.set([T(i) for i in range(3)], 1)
    save_session(q, 33.5)
    items, idx, pos = load_session()
    assert [t.title for t in items] == ["t0", "t1", "t2"] and idx == 1 and pos == 33.5


# ------------------------------------------------------------------ etiquetas
@pytest.mark.parametrize("ext", ["mp3", "m4a", "opus", "flac"])
def test_write_tags_all_formats(tmp_path, ext):
    import mutagen

    f = str(tmp_path / f"x.{ext}")
    subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-f", "lavfi", "-i", "sine=d=1", f], check=True)
    tr = Track(id="AAAAAAAAAAA", title="Shakira - Waka Waka (Official Video) [HD]", artist="ShakiraVEVO", album="Sale el Sol")
    assert tags.write_tags(f, tr)
    m = mutagen.File(f, easy=True) if ext == "mp3" else mutagen.File(f)
    t = m.tags
    title = t.get("title") or t.get("\xa9nam")
    artist = t.get("artist") or t.get("\xa9ART")
    assert list(title) == ["Waka Waka"] and list(artist) == ["Shakira"]


def test_read_cover(tmp_path):
    mp3, jpg, out = str(tmp_path / "x.mp3"), str(tmp_path / "c.jpg"), str(tmp_path / "cv.mp3")
    subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-f", "lavfi", "-i", "sine=d=1", mp3], check=True)
    subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-f", "lavfi", "-i", "color=c=red:s=64x64:d=1", "-frames:v", "1", jpg], check=True)
    subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-i", mp3, "-i", jpg, "-map", "0", "-map", "1", "-c", "copy",
                    "-id3v2_version", "3", out], check=True)
    assert len(tags.read_cover(out) or b"") > 100 and tags.read_cover(mp3) is None


# ------------------------------------------------------------ importar/exportar
def test_playlist_io_roundtrip_and_formats(tmp_path):
    local = tmp_path / "l.mp3"; local.write_bytes(b"x")
    m3u = str(tmp_path / "l.m3u8")
    n = playlist_io.export_m3u([Track(id="dQw4w9WgXcQ", title="Never", artist="Rick", duration=212),
                                Track(title="Local", path=str(local)), Track(kind="playlist", title="x")], m3u)
    assert n == 2
    e = playlist_io.parse_file(m3u)
    assert e[0]["id"] == "dQw4w9WgXcQ" and e[0]["artist"] == "Rick" and e[1]["path"] == str(local)

    csv_ = tmp_path / "sp.csv"
    csv_.write_text('Track URI,Track Name,Artist Name(s),Album Name\nspotify:1,Hola Mundo,"Ana;Luis",X\nspotify:2,"Adiós, mundo",Bea,Y\n')
    e = playlist_io.parse_file(str(csv_))
    assert (e[0]["title"], e[0]["artist"], e[1]["title"]) == ("Hola Mundo", "Ana", "Adiós, mundo")

    txt = tmp_path / "l.txt"; txt.write_text("# c\nbad bunny - tití me preguntó\nhttps://youtu.be/dQw4w9WgXcQ\n")
    e = playlist_io.parse_file(str(txt))
    tracks, failed = playlist_io.resolve_entries(
        e + [{"title": "nada", "artist": ""}],
        lambda q: Track(id="ZZZZZZZZZZZ", title=q) if "nada" not in q else None)
    assert len(tracks) == 2 and failed == ["nada"]


# ------------------------------------------------------------------ servicios (mock)
def _serve(handler):
    srv = http.server.HTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, f"http://127.0.0.1:{srv.server_address[1]}"


def test_sponsorblock_segments(monkeypatch):
    vid = "dQw4w9WgXcQ"
    prefix = hashlib.sha256(vid.encode()).hexdigest()[:4]

    class H(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            ok = self.path.startswith(f"/api/skipSegments/{prefix}")
            body = json.dumps([{"videoID": vid, "segments": [{"segment": [0, 10.5]}, {"segment": [100, 100.5]}]},
                               {"videoID": "otro", "segments": [{"segment": [1, 50]}]}]).encode() if ok else b"[]"
            self.send_response(200 if ok else 404); self.end_headers(); self.wfile.write(body)

        def log_message(self, *a): pass

    srv, url = _serve(H)
    monkeypatch.setattr(sponsorblock, "API", url)
    seg = sponsorblock.fetch_segments(vid)
    assert seg == [(0.0, 10.5)]
    assert sponsorblock.skip_target(seg, 3) == 10.5 and sponsorblock.skip_target(seg, 20) is None
    srv.shutdown()


def test_lastfm_signing_and_scrobble_rule(monkeypatch):
    seen = []

    class H(http.server.BaseHTTPRequestHandler):
        def do_POST(self):
            q = dict(urllib.parse.parse_qsl(self.rfile.read(int(self.headers["Content-Length"])).decode()))
            seen.append(dict(q))
            sig = q.pop("api_sig"); q.pop("format")
            exp = hashlib.md5(("".join(k + q[k] for k in sorted(q)) + "SECRET").encode()).hexdigest()
            out = {"error": 13, "message": "bad sig"} if sig != exp else (
                {"session": {"key": "SK123"}} if q["method"] == "auth.getMobileSession" else {"ok": 1})
            self.send_response(200); self.end_headers(); self.wfile.write(json.dumps(out).encode())

        def log_message(self, *a): pass

    srv, url = _serve(H)
    monkeypatch.setenv("YTM_LASTFM_URL", url + "/")
    lf = integrations.LastFM("KEY", "SECRET")
    assert lf.connect("u", "pw") == "SK123"
    lf.now_playing("Artista", "Título", "Álbum", 200)
    lf.scrobble("Artista", "Título", 1700000000, duration=200)
    assert [s["method"] for s in seen] == ["auth.getMobileSession", "track.updateNowPlaying", "track.scrobble"]
    with pytest.raises(RuntimeError):
        integrations.LastFM("KEY", "WRONG", "SK").now_playing("a", "b")
    assert not integrations.should_scrobble(25, 25) and not integrations.should_scrobble(200, 99)
    assert integrations.should_scrobble(200, 100) and integrations.should_scrobble(1000, 240)
    srv.shutdown()


# ------------------------------------------------------------------ audio real
def test_visualizer_detects_tone(media_server, tmp_path):
    if not visualizer.available():
        pytest.skip("sin numpy/ffmpeg")
    wav = tmp_path / "t.wav"
    subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-f", "lavfi", "-i", "sine=frequency=440:duration=8", str(wav)], check=True)
    s = visualizer.Spectrum(32); s.load(str(wav))
    deadline = time.time() + 5
    lv = []
    while time.time() < deadline:
        lv = s.levels(3.0)
        if max(lv) > 0.3:
            break
        time.sleep(0.2)
    s.stop()
    assert max(lv) > 0.5 and sum(v > 0.3 for v in lv) < 12  # un tono puro activa pocas barras


def test_mpv_filters_speed_and_resume(media_server, tmp_path):
    wav = str(tmp_path / "long.wav")  # archivo local: el servidor de prueba no admite Range (YouTube sí)
    subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-f", "lavfi", "-i", "sine=frequency=440:duration=40", wav], check=True)
    p = Player(ao="null")
    try:
        p.play(wav, start=20)
        time.sleep(1.2)
        assert 20 < p.position < 24
        p.set_audio("rock", True); time.sleep(0.3)
        assert "equalizer" in p.audio_filter and "dynaudnorm" in p.audio_filter
        p.speed = 1.5; time.sleep(0.2)
        t0 = p.position; time.sleep(1.0)
        assert 1.2 < p.position - t0 < 1.8
        p.set_audio("flat", False)
        p.play(media_server + "b.wav"); time.sleep(1.0)
        assert p.position < 3  # sin start: empieza desde el principio
    finally:
        p.terminate()
    assert build_audio_filter("flat", False) == ""
