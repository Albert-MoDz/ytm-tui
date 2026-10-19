"""Pruebas de la interfaz con mpv real (salida de audio nula) y YouTube simulado. Todo con ratón."""
import asyncio
import io
import json
import socket
import subprocess
import time
import urllib.request

import pytest

from ytm import ui, ytdl
from ytm.library import Library
from ytm.models import Track
from ytm.player import Player
from ytm.widgets import IconButton, SeekBar, VolumeBar


@pytest.fixture(scope="module")
def wavs(tmp_path_factory):
    d = tmp_path_factory.mktemp("wavs")
    out = {}
    for name, f, secs in (("a", 440, 40), ("b", 660, 40), ("c", 880, 2)):
        p = d / f"{name}.wav"
        subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-f", "lavfi", "-i", f"sine=frequency={f}:duration={secs}", str(p)], check=True)
        out[name] = str(p)
    return out


@pytest.fixture()
def world(monkeypatch, wavs, media_server):
    """Un 'YouTube' falso: resultados, radio, streams locales y colecciones."""
    def mk(i, title, f, dur=40, artist="Artista"):
        return Track(id=f"{i:011d}".replace("0", "A", 1), title=title, artist=artist, duration=dur, url=media_server + f + ".wav")

    A, B, C = mk(1, "Canción [Official Video]", "a"), mk(2, "Segunda", "b"), mk(3, "Tercera", "c", dur=2)
    R1, R2 = mk(4, "Radio Uno", "a"), mk(5, "Radio Dos", "b")
    PL = Track(title="Lista de prueba", artist="Playlist · 3 canciones", url="https://www.youtube.com/playlist?list=PLx", kind="playlist")
    CH = Track(title="Artista Oficial", artist="Artista / canal", url="https://www.youtube.com/@oficial", kind="channel")
    local = {t.id: wavs[t.url[-5]] for t in (A, B, C, R1, R2)}
    w = type("W", (), dict(A=A, B=B, C=C, R1=R1, R2=R2, PL=PL, CH=CH, calls=[]))
    monkeypatch.setattr(ytdl, "search_tracks", lambda q, cfg: [A, B, C])
    monkeypatch.setattr(ytdl, "search_collections", lambda q, kind, cfg: [PL] if kind == "playlists" else [CH])
    monkeypatch.setattr(ytdl, "open_collection", lambda item, cfg: [A, B, C])
    monkeypatch.setattr(ytdl, "radio", lambda t, cfg, n=25: [Track.from_dict(R1.to_dict()), Track.from_dict(R2.to_dict())])
    monkeypatch.setattr(ytdl, "resolve_stream", lambda t, cfg: {"url": local[t.id], "headers": {}, "duration": t.duration})
    monkeypatch.setattr(ytdl, "search_one", lambda q, cfg: B)
    monkeypatch.setattr(ytdl, "suggest", lambda q: [q + " completo"])
    monkeypatch.setattr(ui, "fetch_lyrics", lambda t: ui.Lyrics(synced=[(0, "primera"), (1, "segunda"), (2, "tercera")]))
    return w


def run(coro):
    return asyncio.run(asyncio.wait_for(coro, 120))


async def wait(cond, timeout=10, msg=""):
    t = time.time()
    while time.time() - t < timeout:
        if cond():
            return
        await asyncio.sleep(0.1)
    raise AssertionError("timeout " + msg)


def make_app(cfg, **kw):
    lib = Library(cfg)
    return ui.YTMApp(cfg, lib, Player(ao="null"), **kw), lib


ROW0 = (10, 2)  # primera fila: borde(1) + cabecera(1)


def row(n):
    return (10, 2 + n)


async def search_and_play(app, pilot, w):
    await pilot.click("#search")
    await pilot.press(*"hola")
    await pilot.click("#s-go")                            # botón Buscar
    await wait(lambda: len(app.results) == 3, msg="resultados")
    await pilot.click("#table", offset=row(0), times=2)  # doble clic = reproducir
    await wait(lambda: app.state == "playing", msg="playing")
    await wait(lambda: (app.player.position or 0) > 0.3, msg="avanza")


def test_everything_with_the_mouse(cfg, world):
    async def go():
        app, lib = make_app(cfg)
        async with app.run_test(size=(170, 45)) as p:
            await search_and_play(app, p, world)
            assert app.now.title.startswith("Canción") and app.table.cursor_row == 0

            # --- transporte
            await p.click("#b-play"); assert app.state == "paused" and app.player.paused
            await p.click("#b-play"); assert app.state == "playing"
            pos = app.player.position
            await p.click("#b-fwd"); await p.pause(0.3); assert app.player.position > pos + 8
            await p.click("#b-back"); await p.pause(0.3)
            # --- barra de progreso: clic al 50 %
            bar = app.query_one("#seek", SeekBar)
            await p.click("#seek", offset=(bar._x0 + bar._w // 2, 0)); await p.pause(0.4)
            assert 17 < app.player.position < 23, app.player.position
            # --- arrastrar la barra hasta el 25 %
            await p.mouse_down("#seek", offset=(bar._x0 + bar._w // 2, 0))
            await p.hover("#seek", offset=(bar._x0 + bar._w // 4, 0))
            await p.mouse_up("#seek", offset=(bar._x0 + bar._w // 4, 0)); await p.pause(0.4)
            assert 8 < app.player.position < 13, app.player.position
            # --- volumen: clic en la barra, botones y silencio
            await p.click("#volbar", offset=(7, 0)); assert 45 <= app.volume <= 60 and app.player.volume == app.volume
            v = app.volume
            await p.click("#b-vup"); assert app.volume == v + 5
            await p.click("#b-vdn"); await p.click("#b-vdn"); assert app.volume == v - 5
            await p.click("#b-mute"); assert app.player.muted and app.query_one("#b-mute").has_class("on")
            await p.click("#b-mute"); assert not app.player.muted
            # --- aleatorio / repetir (botones con estado)
            await p.click("#b-shuf"); assert app.queue.shuffle and app.query_one("#b-shuf").has_class("on")
            await p.click("#b-shuf")
            await p.click("#b-rep"); assert app.queue.repeat == "all"
            await p.click("#b-rep"); assert app.queue.repeat == "one"
            await p.click("#b-rep"); assert app.queue.repeat == "off"
            # --- barra de herramientas sobre la 2.ª fila
            await p.click("#table", offset=row(1))
            assert app.selected_track().title == "Segunda"
            await p.click("#t-fav"); assert lib.is_favorite(world.B)
            await p.click("#t-add"); assert [t.title for t in app.queue.items][-1] == "Segunda"
            await p.click("#t-next"); assert app.queue.items[app.queue.index + 1].title == "Segunda"
            await p.click("#t-dl"); await wait(lambda: lib.local_path(world.B), timeout=40, msg="descarga"); 
            assert lib.local_path(world.B).endswith(".mp3")
            await p.click("#t-pl"); await p.pause(0.2)
            assert isinstance(app.screen, ui.PickScreen)
            await p.press("enter"); await p.pause(0.2)                    # «Nueva playlist…» (única opción)
            await p.press(*"Mis"); await p.press("enter"); await p.pause(0.3)
            assert lib.playlists["Mis"][0].title == "Segunda"
            # --- siguiente / anterior
            await p.click("#b-next"); await wait(lambda: app.state == "playing" and app.now.title == "Segunda", msg="next")
            await p.click("#b-prev"); await wait(lambda: app.state == "playing" and app.now.title.startswith("Canción"), msg="prev")
            # --- favorito de la pista que suena
            await p.click("#b-fav"); assert lib.is_favorite(world.A) and app.query_one("#b-fav").has_class("on")
        return True

    assert run(go())


def test_right_click_menu_and_view_switching(cfg, world):
    async def go():
        app, lib = make_app(cfg)
        async with app.run_test(size=(170, 45)) as p:
            await p.click("#search"); await p.press(*"x"); await p.click("#s-go")
            await wait(lambda: len(app.results) == 3)
            await p.click("#table", offset=row(2), button=3)           # clic derecho
            await wait(lambda: isinstance(app.screen, ui.PickScreen), msg="menú contextual")
            labels = [str(o.prompt) for o in app.screen.query_one("OptionList").options]
            assert any("Añadir a la cola" in l for l in labels) and any("Radio" in l for l in labels)
            await p.press("down", "enter"); await p.pause(0.3)          # «Añadir a la cola»
            assert app.queue.items[-1].title == "Tercera" or app.state == "loading"
            # menú lateral con clic: Favoritos
            await p.click("#sidebar", offset=(4, 3)); await p.pause(0.3)
            assert app.view == "favorites"
            await p.click("#sidebar", offset=(4, 2)); await p.pause(0.2)
            assert app.view == "queue"
        return True

    assert run(go())


def test_queue_reorder_filter_and_session_restore(cfg, world):
    async def go():
        app, lib = make_app(cfg)
        async with app.run_test(size=(170, 45)) as p:
            await p.click("#search"); await p.press(*"x"); await p.click("#s-go")
            await wait(lambda: len(app.results) == 3)
            await p.click("#table", offset=row(0), times=2)            # reproduce A
            await wait(lambda: app.state == "playing")
            for r in (1, 2):
                await p.click("#table", offset=row(r)); await p.click("#t-add")
            await p.press("2"); await p.pause(0.3)                      # vista Cola
            assert [t.title for t in app.queue.items][1:] == ["Segunda", "Tercera"]
            await p.click("#table", offset=row(2)); await p.click("#t-up")   # sube «Tercera»
            assert [t.title for t in app.queue.items] == ["Canción [Official Video]", "Tercera", "Segunda"]
            assert app.table.cursor_row == 1
            await p.click("#t-down"); assert [t.title for t in app.queue.items][1] == "Segunda"
            # filtro dentro de la lista
            await p.click("#t-filter"); await p.press(*"terc"); await p.pause(0.3)
            assert app.table.row_count == 1 and app.selected_track().title == "Tercera"
            await p.press("escape"); await p.pause(0.2); assert app.table.row_count == 3
            app.player.seek_to(12); await p.pause(0.5)
        # --- la sesión se guardó al cerrar: reabrir restaura cola y posición
        app2, _ = make_app(cfg)
        async with app2.run_test(size=(170, 45)) as p:
            assert [t.title for t in app2.queue.items][0].startswith("Canción") and len(app2.queue.items) == 3
            assert app2.state == "stopped" and app2.now.title.startswith("Canción")
            await p.click("#b-play")                                    # reanuda donde se quedó
            await wait(lambda: app2.state == "playing" and (app2.player.position or 0) > 0, msg="reanuda")
            assert 11 < app2.player.position < 16, app2.player.position
        return True

    assert run(go())


def test_panels_audio_settings_and_icons(cfg, world, monkeypatch):
    from PIL import Image

    buf = io.BytesIO(); Image.new("RGB", (480, 360), (200, 30, 30)).save(buf, "JPEG")

    class Resp:
        def __enter__(self): return self
        def __exit__(self, *a): pass
        def read(self): return buf.getvalue()

    monkeypatch.setattr(ui.urllib.request, "urlopen", lambda *a, **k: Resp())

    async def go():
        cfg["lyrics"] = True
        app, lib = make_app(cfg)
        async with app.run_test(size=(170, 45)) as p:
            await search_and_play(app, p, world)
            await p.click("#b-lyr"); await p.pause(1.2)
            assert app.query_one("#lyrics").has_class("visible") and app.query_one("#side").has_class("visible")
            assert "segunda" in str(app.query_one("#lyrics").render()) or "primera" in str(app.query_one("#lyrics").render())
            await p.click("#b-cov")
            await wait(lambda: app.query_one("#cover").image is not None, msg="portada")
            await p.click("#b-viz"); await p.pause(1.5)
            viz_text = str(app.query_one("#viz").render())
            assert any(ch in viz_text for ch in "▁▂▃▄▅▆▇█"), viz_text
            # ecualizador por clic
            await p.click("#b-eq"); await p.pause(0.3)
            assert isinstance(app.screen, ui.AudioScreen)
            ol = app.screen.query_one("OptionList")
            await p.click(ol, offset=(4, 1 + 3)); await p.pause(0.3)          # 3.er preset
            assert cfg["eq"] != "flat" and "equalizer" in app.player.audio_filter
            idx = ol.get_option_index("sp:1.5"); ol.highlighted = idx; await p.press("enter"); await p.pause(0.2)
            assert app.player.speed == 1.5
            ol.highlighted = ol.get_option_index("norm"); await p.press("enter"); await p.pause(0.2)
            assert "dynaudnorm" in app.player.audio_filter
            await p.press("escape")
            assert app.query_one("#b-eq").has_class("on")
            # ajustes: iconos ascii
            await p.click("#b-set"); await p.pause(0.3)
            assert isinstance(app.screen, ui.SettingsScreen)
            from ytm.screens import SETTINGS
            i = [k for k, *_ in SETTINGS].index("icons")
            ol = app.screen.query_one("OptionList"); ol.highlighted = ol.get_option_index(str(i)); await p.press("enter"); await p.pause(0.3)
            assert cfg["icons"] == "ascii" and app.query_one("#b-next").label == ">|"
            await p.press("escape")
            await p.click("#b-help"); await p.pause(0.2); assert isinstance(app.screen, ui.HelpScreen)
            await p.click(app.screen); await p.pause(0.2)
        return True

    assert run(go())


def test_collections_subscriptions_import_export(cfg, world, tmp_path):
    async def go():
        app, lib = make_app(cfg)
        async with app.run_test(size=(170, 45)) as p:
            await p.click("#s-lists"); await p.click("#search"); await p.press(*"rock"); await p.click("#s-go")
            await wait(lambda: any(t.kind == "playlist" for t in app.results), msg="búsqueda de listas")
            assert app.query_one("#s-lists").has_class("on")
            await p.click("#table", offset=row(0), button=3); await p.pause(0.3)   # menú de una colección
            labels = [str(o.prompt) for o in app.screen.query_one("OptionList").options]
            assert any("Suscribirse" in l for l in labels)
            await p.press("down", "enter"); await p.pause(0.3)
            assert lib.is_subscribed(world.PL.url)
            await p.press("escape")
            await p.click("#table", offset=row(0), times=2)                        # doble clic abre la lista
            await wait(lambda: app.results_source == world.PL.url and len(app.results) == 3, msg="abrir lista")
            # exportar
            out = tmp_path / "salida.m3u8"
            app.push_screen(ui.PromptScreen("x", value=str(out)), lambda path: None)
            await p.pause(0.2); await p.press("escape")
            import ytm.playlist_io as pio
            assert pio.export_m3u(app.results, str(out)) == 3
            # importar un M3U (con una canción sin enlace -> búsqueda simulada)
            m3u = tmp_path / "mi lista.m3u8"
            m3u.write_text("#EXTM3U\n#EXTINF:10,Alguien - Algo\nalgo suelto\n")
            app._import_worker(str(m3u))
            await wait(lambda: "mi lista" in lib.playlists, msg="importación")
            assert lib.playlists["mi lista"][0].title == "Segunda" and app.view == "pl:mi lista"
            # auto-descarga de playlist y suscripciones sincronizadas
            await p.press("w"); await p.pause(0.3)
            assert "mi lista" in lib.autodl_playlists
            await wait(lambda: lib.local_path(world.B), timeout=40, msg="auto-descarga")
        return True

    assert run(go())


def test_remote_control_api(cfg, world):
    s = socket.socket(); s.bind(("127.0.0.1", 0)); port = s.getsockname()[1]; s.close()
    cfg["remote_port"] = port

    def http(path, data=None, token="T0KEN"):
        req = urllib.request.Request(f"http://127.0.0.1:{port}{path}", data=json.dumps(data).encode() if data is not None else None,
                                     headers={"X-Token": token, "Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=10) as r:
            return json.loads(r.read().decode()) if "json" in r.headers["Content-Type"] else r.read().decode()

    async def go():
        cfg["remote_token"] = "T0KEN"
        app, _ = make_app(cfg)
        async with app.run_test(size=(170, 45)) as p:
            await p.click("#b-rem"); await p.pause(0.3)
            assert app.remote is not None and app.query_one("#b-rem").has_class("on")
            call = lambda *a, **k: asyncio.to_thread(http, *a, **k)
            with pytest.raises(urllib.error.HTTPError) as e:
                await call("/api/state", token="mal")
            assert e.value.code == 401
            assert "YTM-TUI" in await call("/?token=T0KEN")
            res = await call("/api/search?q=hola"); assert [t["title"] for t in res][:2] == ["Canción [Official Video]", "Segunda"]
            assert (await call("/api/cmd", {"cmd": "play", "track": res[0]}))["ok"]
            await wait(lambda: app.state == "playing", msg="play remoto")
            await call("/api/cmd", {"cmd": "toggle_pause"}); await p.pause(0.2); assert app.state == "paused"
            await call("/api/cmd", {"cmd": "volume", "value": 33}); assert app.volume == 33
            await call("/api/cmd", {"cmd": "toggle_pause"})
            await call("/api/cmd", {"cmd": "enqueue", "track": res[1]})
            await call("/api/cmd", {"cmd": "seek_to", "seconds": 15}); await p.pause(0.4)
            st = await call("/api/state")
            assert st["state"] == "playing" and st["volume"] == 33 and len(st["queue"]) == 2 and 14 < st["pos"] < 18, st
            assert not (await call("/api/cmd", {"cmd": "inventado"}))["ok"]
            await p.click("#b-rem"); await p.pause(0.3); assert app.remote is None
        return True

    assert run(go())


def test_sponsorblock_scrobble_sleep_and_suggestions(cfg, world):
    async def go():
        cfg["sponsorblock"] = True
        app, lib = make_app(cfg)
        calls = []
        app.lastfm.session = "SK"; app.lastfm.key = "K"; app.lastfm.secret = "S"
        app.lastfm.now_playing = lambda *a, **k: calls.append(("np", a[:2]))
        app.lastfm.scrobble = lambda *a, **k: calls.append(("sc", a[:2]))
        async with app.run_test(size=(170, 45)) as p:
            await search_and_play(app, p, world)
            await wait(lambda: calls and calls[0][0] == "np", msg="now playing")
            assert calls[0][1] == ("Artista", "Canción")
            # SponsorBlock: el segmento 0-6 s se salta solo
            app.skip_segments = [(0.0, 6.0)]
            await wait(lambda: (app.player.position or 0) >= 6, timeout=5, msg="salto")
            # scrobble tras escuchar la mitad (40 s -> 20 s)
            app._scrobble["played"] = 19.8; await p.pause(1.0)
            assert [c[0] for c in calls].count("sc") == 1
            # temporizador de apagado por clic
            await p.click("#b-sleep"); assert app.sleep_at > 0 and "15m" in app.query_one("#b-sleep").label
            app.sleep_at = time.time() - 1; await p.pause(1.0)
            assert app.state == "paused" and app.sleep_at == 0
            # sugerencias de búsqueda
            sug = ui.SearchSuggester(app)
            lib.add_search("bad bunny"); assert await sug.get_suggestion("bad b") == "bad bunny"
        return True

    assert run(go())


def test_radio_autoplay_and_repeat_modes(cfg, world):
    async def go():
        app, lib = make_app(cfg)
        async with app.run_test(size=(170, 45)) as p:
            await search_and_play(app, p, world)
            app.player.seek_to(39.0)                                   # al terminar sigue la radio
            await wait(lambda: app.now.title == "Radio Uno" and app.state == "playing", timeout=15, msg="radio")
            assert [t.title for t in app.queue.items][-1] == "Radio Dos"
            await p.click("#t-radio"); await p.pause(0.5)              # botón Radio sobre la seleccionada
        return True

    assert run(go())
