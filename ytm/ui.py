from __future__ import annotations

import asyncio
import io
import logging
import os
import random
import threading
import time
import urllib.request
from pathlib import Path

from rich.text import Text
from textual import on, work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.css.query import NoMatches
from textual.suggester import Suggester
from textual.widgets import Footer, Header, Input, OptionList, Static
from textual.widgets.option_list import Option
from textual.worker import get_current_worker

try:  # portada con imágenes reales (kitty/sixel/iTerm) o bloques Unicode como respaldo
    from textual_image.widget import Image as CoverImage
except Exception:  # noqa: BLE001  (sin textual-image o sin Pillow)
    CoverImage = None

from . import playlist_io, ytdl
from . import visualizer as viz
from .config import Config
from .icons import ic, set_icon_set
from .integrations import DiscordPresence, LastFM, MediaKeys, desktop_notify, should_scrobble
from .library import Library
from .lyrics import Lyrics, fetch_lyrics, parse_lrc
from .models import Track, fmt_time, truncate
from .player import EQ_PRESETS, PlayQueue
from .remote import RemoteServer
from .screens import AudioScreen, ConfirmScreen, HelpScreen, PickScreen, PromptScreen, SettingsScreen
from .session import load_session, save_session
from .sponsorblock import fetch_segments, skip_target
from .tags import clean_names, read_cover
from .widgets import IconButton, SeekBar, TrackTable, VolumeBar
from .ytdl import DownloadManager, friendly_error

log = logging.getLogger("ytm")

SLEEP_STEPS = [0, 15, 30, 60, 90]
REPEAT_LABEL = {"off": "no", "all": "todo", "one": "una"}
JOB_LABEL = {"queued": "En cola", "downloading": "Descargando", "converting": "Convirtiendo",
             "done": "Completada", "error": "Error", "cancelled": "Cancelada"}

# (id, icono, texto, acción, ayuda)
SEARCH_ROW = [
    ("s-go", "search", "Buscar", "submit_search", "Buscar (Enter)"),
    ("s-songs", "songs", "Canciones", "set_kind('songs')", "Buscar canciones"),
    ("s-lists", "albums", "Listas", "set_kind('playlists')", "Buscar playlists y álbumes"),
    ("s-art", "artists", "Artistas", "set_kind('artists')", "Buscar artistas y canales"),
    ("s-music", "music", "YT Music", "toggle_source", "Usar YouTube Music como fuente (S)"),
    ("s-only", "only", "Solo canciones", "toggle_songs_only", "Ocultar videos largos (t)"),
    ("s-rec", "recent", "", "recent_searches", "Búsquedas recientes (h)"),
]
TOOLBAR = [
    ("t-play", "play", "Reproducir", "activate_selected", "Reproducir la seleccionada (Enter / doble clic)"),
    ("t-all", "playall", "Todo", "play_all", "Reproducir toda la lista"),
    ("t-mix", "mixall", "Mezclar", "play_all(True)", "Reproducir toda la lista en orden aleatorio"),
    ("t-add", "add", "Cola", "sel('queue_add')", "Añadir a la cola (a)"),
    ("t-next", "addnext", "Siguiente", "sel('queue_next')", "Reproducir a continuación (A)"),
    ("t-fav", "fav", "Favorito", "sel('favorite')", "Marcar como favorita (f)"),
    ("t-dl", "dl", "Bajar", "sel('download')", "Descargar (d)"),
    ("t-dlall", "dl_all", "Lista", "download_all", "Descargar toda la lista (D)"),
    ("t-pl", "playlist", "Playlist", "sel('add_to_playlist')", "Añadir a una playlist (p)"),
    ("t-radio", "radio", "Radio", "sel('radio')", "Radio: canciones similares (R)"),
    ("t-up", "up", "", "move(-1)", "Subir en la cola o playlist (Ctrl+↑)"),
    ("t-down", "down", "", "move(1)", "Bajar en la cola o playlist (Ctrl+↓)"),
    ("t-rm", "remove", "Quitar", "remove", "Quitar de la lista (x)"),
    ("t-filter", "filter", "Filtrar", "toggle_filter", "Filtrar la lista (Ctrl+F)"),
    ("t-more", "more", "", "more_menu", "Más opciones"),
]
CONTROLS_L = [
    ("b-prev", "prev", "", "prev_track", "Anterior (b)"),
    ("b-back", "back", "10", "seek(-10)", "Retroceder 10 s  (← = 5 s)"),
    ("b-play", "play", "", "toggle_pause", "Reproducir / pausa (Espacio)"),
    ("b-fwd", "fwd", "10", "seek(10)", "Adelantar 10 s  (→ = 5 s)"),
    ("b-next", "next", "", "next_track", "Siguiente (n)"),
    ("b-shuf", "shuffle", "", "shuffle", "Aleatorio (s)"),
    ("b-rep", "repeat", "", "repeat", "Repetir: no → todo → una (r)"),
    ("b-vdn", "vol_dn", "", "volume(-5)", "Bajar volumen (-)"),
]
CONTROLS_R = [
    ("b-vup", "vol_up", "", "volume(5)", "Subir volumen (=)"),
    ("b-mute", "mute", "", "mute", "Silenciar (m)"),
    ("b-fav", "fav", "", "now_fav", "Favorito (la canción que suena)"),
]
PANELS_R = [
    ("b-lyr", "lyrics", "Letra", "toggle_lyrics", "Letra sincronizada (y)"),
    ("b-cov", "cover", "", "toggle_cover", "Portada (i)"),
    ("b-viz", "viz", "", "toggle_viz", "Visualizador de espectro (v)"),
    ("b-eq", "eq", "", "audio", "Ecualizador, velocidad y normalización (e)"),
    ("b-sleep", "sleep", "", "sleep_timer", "Temporizador de apagado (z)"),
    ("b-rem", "remote", "", "toggle_remote", "Control remoto web para el celular (Ctrl+R)"),
    ("b-set", "settings", "", "settings", "Ajustes (o)"),
    ("b-help", "help", "", "help", "Ayuda (?)"),
    ("b-quit", "quit", "", "quit_app", "Salir (q)"),
]


class SearchSuggester(Suggester):
    """Autocompleta con tus búsquedas anteriores y con las sugerencias de YouTube."""

    def __init__(self, app: "YTMApp"):
        super().__init__(use_cache=True, case_sensitive=False)
        self._app = app

    async def get_suggestion(self, value: str) -> str | None:
        low = value.lower()
        if len(low) < 2:
            return None
        for q in self._app.lib.searches:
            if q.lower().startswith(low) and q.lower() != low:
                return q
        await asyncio.sleep(0.3)  # espera a que el usuario deje de escribir
        try:
            if self._app.query_one("#search", Input).value != value:
                return None
            for s in await asyncio.to_thread(ytdl.suggest, value):
                if s.lower().startswith(low) and s.lower() != low:
                    return s
        except Exception:  # noqa: BLE001
            pass
        return None


class YTMApp(App):
    TITLE = "YTM-TUI"
    CSS = """
    #searchrow { height: 3; margin: 0 1; align-vertical: middle; }
    #searchrow Input { width: 1fr; }
    #toolbar { height: 1; margin: 0 1; overflow: hidden; }
    #main { height: 1fr; }
    #sidebar { width: 26; height: 100%; border: round $primary-darken-2; }
    #sidebar:focus, #table:focus { border: round $accent; }
    #center { width: 1fr; height: 100%; }
    #table { width: 100%; border: round $primary-darken-2; height: 1fr; }
    #filter { display: none; height: 3; }
    #filter.visible { display: block; }
    #side { width: 46; height: 100%; display: none; }
    #side.visible { display: block; }
    #cover { display: none; height: 17; width: 100%; border: round $primary-darken-2; }
    #cover.visible { display: block; }
    #viz { display: none; height: 8; border: round $primary-darken-2; padding: 0 1; }
    #viz.visible { display: block; }
    #lyrics { display: none; height: 1fr; border: round $primary-darken-2; padding: 0 1; }
    #lyrics.visible { display: block; }
    #player { height: 5; border: round $accent; padding: 0 1; }
    #now { height: 1; }
    #controls { height: 1; overflow: hidden; }
    #spacer { width: 1fr; }
    """

    BINDINGS = [
        Binding("space", "toggle_pause", "Pausa"),
        Binding("n", "next_track", "Sig."),
        Binding("b", "prev_track", "Ant."),
        Binding("slash", "focus_search", "Buscar"),
        Binding("a", "key_do('queue_add')", "+Cola"),
        Binding("A", "key_do('queue_next')", "Siguiente", show=False),
        Binding("d", "key_do('download')", "Descargar"),
        Binding("D", "download_all", "Desc.todo", show=False),
        Binding("f", "key_do('favorite')", "♥"),
        Binding("p", "key_do('add_to_playlist')", "Playlist"),
        Binding("R", "key_do('radio')", "Radio"),
        Binding("x", "remove", "Quitar", show=False),
        Binding("c", "clear", "Vaciar", show=False),
        Binding("X", "delete_playlist", "Borrar playlist", show=False),
        Binding("ctrl+up", "move(-1)", "Subir", show=False),
        Binding("ctrl+down", "move(1)", "Bajar", show=False),
        Binding("ctrl+f", "toggle_filter", "Filtrar", show=False),
        Binding("s", "shuffle", "Aleat."),
        Binding("r", "repeat", "Repetir"),
        Binding("equals_sign,plus", "volume(5)", "Vol+", show=False),
        Binding("minus", "volume(-5)", "Vol-", show=False),
        Binding("m", "mute", "Mute", show=False),
        Binding("left", "seek(-5)", "-5s", show=False),
        Binding("right", "seek(5)", "+5s", show=False),
        Binding("y", "toggle_lyrics", "Letra"),
        Binding("i", "toggle_cover", "Portada", show=False),
        Binding("v", "toggle_viz", "Visual.", show=False),
        Binding("e", "audio", "EQ", show=False),
        Binding("o", "settings", "Ajustes"),
        Binding("h", "recent_searches", "Recientes", show=False),
        Binding("E", "export_list", "Exportar", show=False),
        Binding("I", "import_list", "Importar", show=False),
        Binding("u", "subscribe", "Suscribir", show=False),
        Binding("w", "toggle_autodl", "Auto-desc.", show=False),
        Binding("z", "sleep_timer", "Sleep", show=False),
        Binding("t", "toggle_songs_only", "Filtro", show=False),
        Binding("S", "toggle_source", "Fuente", show=False),
        Binding("ctrl+r", "toggle_remote", "Remoto", show=False),
        Binding("escape", "escape", "Lista", show=False),
        Binding("1", "view('search')", show=False),
        Binding("2", "view('queue')", show=False),
        Binding("3", "view('favorites')", show=False),
        Binding("4", "view('history')", show=False),
        Binding("5", "view('local')", show=False),
        Binding("6", "view('downloads')", show=False),
        Binding("7", "view('subs')", show=False),
        Binding("question_mark", "help", "Ayuda"),
        Binding("q", "quit_app", "Salir"),
    ]

    # ======================================================================= init
    def __init__(self, cfg: Config, library: Library, player, query: str | None = None, restore: bool = True):
        super().__init__()
        self.cfg, self.lib, self.player = cfg, library, player
        self.initial_query = query
        self.restore = restore
        set_icon_set(cfg["icons"])
        self.queue = PlayQueue()
        self.queue.shuffle = bool(cfg["shuffle"])
        self.queue.repeat = cfg["repeat"] if cfg["repeat"] in REPEAT_LABEL else "off"
        self.dm = DownloadManager(cfg, library, on_change=lambda job: self.post(self._dm_event, job))
        self.player.on_end = lambda reason: self.post(self._on_player_end, reason)

        self.view = "search"
        self.results: list[Track] = []
        self.results_source = ""        # URL de la playlist/canal abierto
        self.results_title = ""
        self.results_is_collection = False
        self.search_kind = "songs"      # songs | playlists | artists
        self.local_cache: list[Track] = []
        self.view_map: list[int] = []
        self.filter_text = ""
        self.last_query = ""
        self.now: Track | None = None
        self.state = "stopped"          # stopped | loading | playing | paused
        self.volume = int(cfg["volume"])
        self.lyrics: Lyrics | None = None
        self.lyrics_state = ""
        self.skip_segments: list[tuple[float, float]] = []
        self._last_skip = -1.0
        self.resume: tuple[str, float] | None = None
        self.current_src = ""
        self.current_headers: dict = {}
        self.sleep_at = 0.0
        self.sleep_idx = 0
        self.fail_streak = 0
        self._last_activate = 0.0
        self._scrobble: dict | None = None
        self._ticks = 0
        self._remote_snapshot: dict = {}
        self.remote: RemoteServer | None = None
        self.mediakeys: MediaKeys | None = None
        self.spectrum = viz.Spectrum(20)
        self.lastfm = LastFM(cfg["lastfm_api_key"], cfg["lastfm_api_secret"], cfg["lastfm_session"])
        self.discord = DiscordPresence(cfg["discord_client_id"])
        self._labels: dict[str, tuple[str, str]] = {}
        self._ui_thread = 0
        self._closing = False

    # ================================================================== utilidades
    def post(self, fn, *args) -> None:
        """Ejecuta fn en el hilo de la interfaz desde cualquier hilo."""
        if self._closing:
            return
        if threading.get_ident() == self._ui_thread:
            fn(*args)
            return
        try:
            self.call_from_thread(fn, *args)
        except Exception:  # noqa: BLE001
            log.exception("post falló")

    def run_in_ui(self, fn, *args):
        """Como post(), pero devuelve el resultado (lo usa el control remoto)."""
        if threading.get_ident() == self._ui_thread:
            return fn(*args)
        return self.call_from_thread(fn, *args)

    @property
    def table(self) -> TrackTable:
        return self.query_one("#table", TrackTable)

    @property
    def sidebar(self) -> OptionList:
        return self.query_one("#sidebar", OptionList)

    def say(self, message: str, severity: str = "information") -> None:
        self.notify(message, severity=severity, timeout=4)

    def btn(self, spec) -> IconButton:
        bid, icon, text, action, tip = spec
        self._labels[bid] = (icon, text)
        return IconButton(self._label(icon, text), action, tip, id=bid)

    @staticmethod
    def _label(icon: str, text: str) -> str:
        return f"{ic(icon)} {text}".strip()

    def refresh_labels(self) -> None:
        for bid, (icon, text) in self._labels.items():
            try:
                self.query_one(f"#{bid}", IconButton).set_label(self._label(icon, text))
            except Exception:  # noqa: BLE001
                pass

    # ===================================================================== compose
    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with Horizontal(id="searchrow"):
            yield Input(placeholder="Buscar canciones o artistas…  (o pega un enlace de YouTube)", id="search",
                        suggester=SearchSuggester(self))
            for spec in SEARCH_ROW:
                yield self.btn(spec)
        with Horizontal(id="toolbar"):
            for spec in TOOLBAR:
                yield self.btn(spec)
        with Horizontal(id="main"):
            yield OptionList(id="sidebar")
            with Vertical(id="center"):
                yield Input(placeholder="Filtrar esta lista…  (Esc para cerrar)", id="filter")
                yield TrackTable(id="table", cursor_type="row", zebra_stripes=True)
            with Vertical(id="side"):
                if CoverImage is not None:
                    yield CoverImage(id="cover")
                else:
                    yield Static(Text("Portada no disponible: pip install textual-image", style="dim"), id="cover")
                yield Static(id="viz")
                yield Static(id="lyrics")
        with Vertical(id="player"):
            yield Static(id="now")
            yield SeekBar(id="seek")
            with Horizontal(id="controls"):
                for spec in CONTROLS_L:
                    yield self.btn(spec)
                yield VolumeBar(id="volbar")
                for spec in CONTROLS_R:
                    yield self.btn(spec)
                yield Static(id="spacer")
                for spec in PANELS_R:
                    yield self.btn(spec)
        yield Footer()

    def on_mount(self) -> None:
        self._ui_thread = threading.get_ident()
        self.sub_title = "Reproductor de YouTube para la terminal"
        try:
            self.player.volume = self.volume
        except Exception:  # noqa: BLE001
            pass
        self.apply_audio()
        self.query_one("#volbar", VolumeBar).volume = self.volume
        for panel, flag in (("lyrics", "show_lyrics"), ("cover", "show_cover"), ("viz", "show_viz")):
            self.query_one(f"#{panel}").set_class(bool(self.cfg[flag]), "visible")
        self._update_side()
        self._restore_session()
        self.refresh_sidebar()
        self.show_view("search")
        self.set_interval(0.5, self.tick)
        self.set_interval(0.09, self.tick_viz)
        interval = max(5, int(self.cfg["subscription_check_minutes"])) * 60
        self.set_interval(interval, self.sync_subscriptions)
        if self.lib.subscriptions:
            self.set_timer(15, self.sync_subscriptions)
        if self.cfg["media_keys"]:
            self.mediakeys = MediaKeys(lambda act: self.call_from_thread(self.run_action, act))
            self.mediakeys.start()
        if self.cfg["remote_enabled"]:
            self.start_remote(announce=True)
        if self.initial_query:
            self.query_one("#search", Input).value = self.initial_query
            self.call_after_refresh(self.do_search, self.initial_query)
        else:
            self.query_one("#search", Input).focus()
        self.refresh_controls()

    def on_unmount(self) -> None:
        self._closing = True
        self.cfg["volume"] = self.volume
        self.cfg["shuffle"] = self.queue.shuffle
        self.cfg["repeat"] = self.queue.repeat
        self.cfg.save()
        self.lib.save()
        pos = self.player.position if self.state in ("playing", "paused") else (self.resume[1] if self.resume else 0)
        save_session(self.queue, pos)
        self.stop_remote()
        self.spectrum.stop()
        self.discord.clear()
        if self.mediakeys:
            self.mediakeys.stop()
        threading.Thread(target=self.player.terminate, daemon=True).start()

    def _restore_session(self) -> None:
        if not self.restore:
            return
        items, index, pos = load_session()
        if items:
            self.queue.items, self.queue.index = items, index
            self.queue._reset_play_state()
            self.now = self.queue.current
            self.resume = (self.now.key, pos) if self.now and pos > 1 else None

    # ===================================================================== sidebar
    def sidebar_options(self) -> list[Option | None]:
        opts: list[Option | None] = [
            Option(Text(f"{ic('search')} Buscar"), id="search"),
            Option(Text(f"{ic('queue')} Cola ({len(self.queue.items)})"), id="queue"),
            Option(Text(f"{ic('fav_on')} Favoritos ({len(self.lib.favorites)})"), id="favorites"),
            Option(Text(f"{ic('recent')} Historial"), id="history"),
            Option(Text(f"{ic('dl')} Descargadas ({self.lib.downloaded_count()})"), id="local"),
            Option(Text(f"{ic('dl_all')} Descargas ({self.dm.active_count()})"), id="downloads"),
            Option(Text(f"{ic('sub')} Suscripciones ({len(self.lib.subscriptions)})"), id="subs"),
            None,
        ]
        for name, items in self.lib.playlists.items():
            auto = " ⬇" if name in self.lib.autodl_playlists else ""
            opts.append(Option(Text(f"{ic('playlist')} {name} ({len(items)}){auto}"), id=f"pl:{name}"))
        opts.append(Option(Text(f"{ic('add')} Nueva playlist", style="dim"), id="new_pl"))
        return opts

    def refresh_sidebar(self) -> None:
        ol = self.sidebar
        ol.clear_options()
        ol.add_options(self.sidebar_options())
        try:
            ol.highlighted = ol.get_option_index(self.view)
        except Exception:  # noqa: BLE001
            pass

    @on(OptionList.OptionSelected, "#sidebar")
    def _sidebar_selected(self, event: OptionList.OptionSelected) -> None:
        if event.option_id == "new_pl":
            self.push_screen(PromptScreen("Nombre de la nueva playlist"), self._create_playlist_cb)
            return
        self.show_view(event.option_id)
        if event.option_id == "search":
            self.query_one("#search", Input).focus()
        else:
            self.table.focus()

    def _create_playlist_cb(self, name: str | None) -> None:
        if name:
            if self.lib.create_playlist(name):
                self.refresh_sidebar()
                self.say(f"Playlist «{name}» creada")
            else:
                self.say("Ya existe una playlist con ese nombre", "warning")

    # ======================================================================= vistas
    def view_title(self) -> str:
        if self.view.startswith("pl:"):
            return f"{ic('playlist')} Playlist · {self.view[3:]}"
        if self.view == "search":
            if self.results_source:
                return f"{ic('collection')} {self.results_title or 'Lista'}"
            return "Resultados" + (f" · «{self.last_query}»" if self.last_query else "")
        base = {"queue": "Cola de reproducción", "favorites": "Favoritos", "history": "Historial",
                "local": "Descargadas", "downloads": "Descargas", "subs": "Suscripciones (se descargan solas)"}[self.view]
        if self.filter_text:
            base += f" · filtro «{self.filter_text}»"
        return base

    def current_tracks(self) -> list[Track]:
        v = self.view
        if v == "search":
            return self.results
        if v == "queue":
            return self.queue.items
        if v == "favorites":
            return self.lib.favorites
        if v == "history":
            return self.lib.history
        if v == "local":
            return self.local_cache
        if v == "downloads":
            return [j.track for j in self.dm.jobs]
        if v == "subs":
            return self.lib.subscriptions
        if v.startswith("pl:"):
            return self.lib.playlists.get(v[3:], [])
        return []

    def show_view(self, name: str) -> None:
        if name.startswith("pl:") and name[3:] not in self.lib.playlists:
            name = "search"
        self.view = name
        if name == "local":
            self.local_cache = self.lib.local_tracks()
        self.refresh_table(reset=True)
        self.refresh_sidebar()

    def action_view(self, name: str) -> None:
        self.show_view(name)
        self.table.focus()

    def refresh_table(self, reset: bool = False) -> None:
        t = self.table
        prev = 0 if reset else t.cursor_row
        t.clear(columns=True)
        t.border_title = self.view_title()
        playing_key = self.now.key if self.now else None
        tracks = self.current_tracks()
        needle = self.filter_text.lower()
        self.view_map = [i for i, tr in enumerate(tracks)
                         if not needle or needle in f"{tr.title} {tr.artist}".lower()]

        if self.view == "downloads":
            t.add_column("Título", width=46)
            t.add_column("Estado", width=14)
            t.add_column("Progreso", width=26)
            for i in self.view_map:
                job = self.dm.jobs[i]
                filled = int(job.progress / 100 * 16)
                bar = "█" * filled + "░" * (16 - filled) + f" {int(job.progress):3d}%"
                status = JOB_LABEL[job.status] + (f" · {job.message}" if job.status == "error" else "")
                style = {"done": "green", "error": "red", "cancelled": "yellow"}.get(job.status, "")
                t.add_row(Text(truncate(job.track.title, 44)), Text(truncate(status, 40), style=style),
                          Text(bar if job.status in ("downloading", "converting", "done") else ""), key=str(i))
        else:
            t.add_column("#", width=4)
            t.add_column("Título", width=40)
            t.add_column("Artista", width=24)
            t.add_column("Dur.", width=6)
            t.add_column("", width=4)
            for i in self.view_map:
                tr = tracks[i]
                is_now = tr.is_track and playing_key is not None and tr.key == playing_key
                style = "bold red" if is_now else ""
                if tr.is_track:
                    marks = ("♥" if self.lib.is_favorite(tr) else " ") + ("⬇" if self.lib.local_path(tr) else " ")
                    title = tr.title
                else:
                    icon = ic("collection") if tr.kind == "playlist" else ic("channel")
                    marks = "✦" if self.lib.is_subscribed(tr.url) else " "
                    title = f"{icon} {tr.title}"
                t.add_row(
                    Text("▶" if is_now else str(i + 1), style=style),
                    Text(truncate(title, 38), style=style),
                    Text(truncate(tr.artist, 22), style="dim" if not is_now else style),
                    Text(fmt_time(tr.duration) if tr.is_track else ""),
                    Text(marks, style="red"),
                    key=str(i),
                )
        if self.view_map:
            t.move_cursor(row=min(prev, len(self.view_map) - 1))

    def selected_index(self) -> int | None:
        t = self.table
        if t.row_count == 0 or not self.view_map:
            return None
        row = min(t.cursor_row, len(self.view_map) - 1)
        return self.view_map[row]

    def selected_track(self) -> Track | None:
        i = self.selected_index()
        tracks = self.current_tracks()
        return tracks[i] if i is not None and i < len(tracks) else None

    def target_track(self) -> Track | None:
        """Atajos de teclado: la seleccionada si el foco está en la lista; si no, la que suena."""
        if self.focused is self.table:
            return self.selected_track() or self.now
        return self.now or self.selected_track()

    def selected_or_now(self) -> Track | None:
        """Botones de la barra de herramientas: siempre la seleccionada."""
        return self.selected_track() or self.now

    # ====================================================================== búsqueda
    @on(Input.Submitted, "#search")
    def _search_submitted(self, event: Input.Submitted) -> None:
        q = event.value.strip()
        if q:
            self.do_search(q)

    def action_submit_search(self) -> None:
        q = self.query_one("#search", Input).value.strip()
        if q:
            self.do_search(q)
        else:
            self.query_one("#search", Input).focus()

    @work(thread=True, exclusive=True, group="search")
    def do_search(self, query: str) -> None:
        self.post(self._set_status, f"Buscando «{query}»…")
        source, title, is_coll = "", "", False
        try:
            if ytdl.is_url(query):
                tracks = ytdl.tracks_from_url(query, self.cfg)
                source, title, is_coll = query, query, True
            elif self.search_kind == "songs":
                tracks = ytdl.search_tracks(query, self.cfg)
            else:
                tracks = ytdl.search_collections(query, self.search_kind, self.cfg)
        except Exception as e:  # noqa: BLE001
            self.post(self._set_status, "")
            self.post(self.say, f"Búsqueda fallida: {friendly_error(e)}", "error")
            return
        if get_current_worker().is_cancelled:
            return
        self.post(self._show_results, query, tracks, source, title, is_coll)

    def _set_status(self, text: str) -> None:
        self.sub_title = text or "Reproductor de YouTube para la terminal"

    def _show_results(self, query: str, tracks: list[Track], source: str = "", title: str = "",
                      is_coll: bool = False) -> None:
        self._set_status("")
        self.last_query = query
        if not source:
            self.lib.add_search(query)
        self.results, self.results_source = tracks, source
        self.results_title, self.results_is_collection = title, is_coll
        self.show_view("search")
        if tracks:
            self.table.focus()
            self.say(f"{len(tracks)} resultados")
        else:
            self.say("Sin resultados", "warning")

    @work(thread=True, exclusive=True, group="search")
    def _open_collection(self, item: Track) -> None:
        self.post(self._set_status, f"Abriendo «{item.title}»…")
        try:
            tracks = ytdl.open_collection(item, self.cfg)
        except Exception as e:  # noqa: BLE001
            self.post(self._set_status, "")
            self.post(self.say, f"No se pudo abrir: {friendly_error(e)}", "error")
            return
        self.post(self._show_results, self.last_query, tracks, item.url, item.title, True)

    def action_focus_search(self) -> None:
        inp = self.query_one("#search", Input)
        inp.focus()
        inp.select_all()

    def action_set_kind(self, kind: str) -> None:
        self.search_kind = kind
        self.refresh_controls()
        if self.last_query:
            self.do_search(self.last_query)

    def action_toggle_songs_only(self) -> None:
        self.cfg["songs_only"] = not self.cfg["songs_only"]
        self.say("Filtro «solo canciones»: " + ("activado" if self.cfg["songs_only"] else "desactivado"))
        self.refresh_controls()
        if self.last_query:
            self.do_search(self.last_query)

    def action_toggle_source(self) -> None:
        self.cfg["search_source"] = "music" if self.cfg["search_source"] == "youtube" else "youtube"
        self.say("Fuente de búsqueda: " + ("YouTube Music" if self.cfg["search_source"] == "music" else "YouTube"))
        self.refresh_controls()
        if self.last_query:
            self.do_search(self.last_query)

    def action_recent_searches(self) -> None:
        recents = self.lib.searches[:20]
        if not recents:
            self.say("Aún no hay búsquedas recientes", "warning")
            return

        def chosen(i: int | None) -> None:
            if i is not None:
                q = recents[i]
                self.query_one("#search", Input).value = q
                self.do_search(q)

        self.push_screen(PickScreen("Búsquedas recientes", [f"{ic('recent')} {q}" for q in recents]), chosen)

    # ---------------------------------------------------------------- filtro en lista
    def action_toggle_filter(self) -> None:
        box = self.query_one("#filter", Input)
        if box.has_class("visible"):
            self._close_filter()
        else:
            box.add_class("visible")
            box.focus()
        self.refresh_controls()

    def _close_filter(self) -> None:
        box = self.query_one("#filter", Input)
        box.remove_class("visible")
        box.value = ""
        self.filter_text = ""
        self.refresh_table(reset=True)
        self.table.focus()

    @on(Input.Changed, "#filter")
    def _filter_changed(self, event: Input.Changed) -> None:
        self.filter_text = event.value.strip()
        self.refresh_table(reset=True)

    @on(Input.Submitted, "#filter")
    def _filter_submitted(self, event: Input.Submitted) -> None:
        self.table.focus()

    def action_escape(self) -> None:
        if self.query_one("#filter", Input).has_class("visible"):
            self._close_filter()
            self.refresh_controls()
        else:
            self.table.focus()

    # ===================================================================== reproducir
    @on(TrackTable.RowSelected, "#table")
    def _row_selected(self, event: TrackTable.RowSelected) -> None:
        self.activate_row(int(event.row_key.value))

    def activate_selected(self) -> None:
        i = self.selected_index()
        if i is not None:
            self.activate_row(i)

    def action_activate_selected(self) -> None:
        self.activate_selected()

    def activate_row(self, idx: int) -> None:
        if time.time() - self._last_activate < 0.4:  # un doble clic genera dos eventos
            return
        self._last_activate = time.time()
        self.play_from_view(idx)

    def play_from_view(self, idx: int) -> None:
        tracks = self.current_tracks()
        if not 0 <= idx < len(tracks):
            return
        track = tracks[idx]
        if not track.is_track:
            self._open_collection(track)
            return
        if self.view == "downloads":
            job = self.dm.jobs[idx]
            if job.status != "done":
                self.say("Esa descarga aún no está completa", "warning")
                return
            track = Track.from_dict({**job.track.to_dict(), "path": job.path})
            self.queue.set([track])
        elif self.view == "queue":
            self.queue.jump(idx)
        elif self.view == "search" and not self.results_is_collection:
            self.queue.set([track])  # una sola; al terminar sigue la radio
        elif self.view == "history":
            self.queue.set([track])
        elif self.view == "subs":
            return
        else:
            playable = [t for t in tracks if t.is_track]
            self.queue.set(playable, playable.index(track))
        if self.queue.current:
            self.play_track(self.queue.current)
        self.refresh_sidebar()

    def action_play_all(self, shuffle: bool = False) -> None:
        tracks = [t for t in self.current_tracks() if t.is_track]
        if not tracks:
            self.say("No hay canciones en esta lista", "warning")
            return
        self.queue.set(tracks, random.randrange(len(tracks)) if shuffle else 0)
        if shuffle:
            self.queue.set_shuffle(True)
        self.play_track(self.queue.current)
        self.refresh_sidebar()

    def play_track(self, track: Track, resume: bool = False) -> None:
        if not resume:
            self.resume = None
        self.now = track
        self.state = "loading"
        self.lyrics, self.lyrics_state = None, ""
        self.skip_segments, self._last_skip = [], -1.0
        self.refresh_table()
        self.render_now()
        self._load_and_play(track)

    @work(thread=True, exclusive=True, group="play")
    def _load_and_play(self, track: Track) -> None:
        worker = get_current_worker()
        try:
            local = self.lib.local_path(track)
            if local:
                src, headers, dur = local, None, None
            else:
                r = ytdl.resolve_stream(track, self.cfg)
                src, headers, dur = r["url"], r["headers"], r["duration"]
        except Exception as e:  # noqa: BLE001
            if not worker.is_cancelled:
                self.post(self._play_failed, track, friendly_error(e))
            return
        if not worker.is_cancelled:
            self.post(self._start, track, src, headers, dur)

    def _start(self, track: Track, src: str, headers, dur) -> None:
        if self.now is not track:
            return
        start = 0.0
        if self.resume and self.resume[0] == track.key:
            start = self.resume[1]
        self.resume = None
        try:
            self.player.play(src, headers, start=start)
            self.player.volume = self.volume
        except Exception as e:  # noqa: BLE001
            self._play_failed(track, friendly_error(e))
            return
        self.current_src, self.current_headers = src, headers or {}
        self.state = "playing"
        self.fail_streak = 0
        if dur and not track.duration:
            track.duration = dur
        self.lib.add_history(track)
        if self.cfg["lyrics"]:
            self.lyrics_state = "loading"
            self._load_lyrics(track)
        if self.cfg["sponsorblock"] and track.id and not self.lib.local_path(track):
            self._load_segments(track)
        if self.query_one("#cover").has_class("visible"):
            self._load_cover(track)
        if self.query_one("#viz").has_class("visible"):
            self.spectrum.load(src, (headers or {}).get("User-Agent", ""))
        self._begin_scrobble(track, dur)
        self._prefetch()
        self.refresh_table()
        self.refresh_sidebar()
        self.render_now()
        save_session(self.queue, 0)

    def _play_failed(self, track: Track, reason: str) -> None:
        if self.now is not track:
            return
        self.fail_streak += 1
        self.say(f"No se pudo reproducir «{truncate(track.title, 40)}»: {reason}", "error")
        self.state = "stopped"
        if self.fail_streak < 3:
            nxt = self.queue.next(auto=True)
            if nxt:
                self.play_track(nxt)
                return
        self.render_now()

    def _on_player_end(self, reason: str) -> None:
        if self.state not in ("playing", "paused"):
            return
        if reason == "error":
            if self.now:
                self._play_failed(self.now, "error de reproducción")
            return
        self._advance_after_end()

    def _advance_after_end(self) -> None:
        nxt = self.queue.next(auto=True)
        if nxt:
            self.play_track(nxt)
        elif self.cfg["autoplay_radio"] and self.now and self.now.id:
            self.state = "loading"
            self.render_now()
            self._radio_continue(self.now, play=True)
        else:
            self.state = "stopped"
            self.discord.clear()
            self.render_now()

    @work(thread=True, group="prefetch")
    def _prefetch(self) -> None:
        nxt = self.queue.peek_next()
        if nxt and nxt.id and not self.lib.local_path(nxt):
            try:
                ytdl.resolve_stream(nxt, self.cfg)
            except Exception:  # noqa: BLE001
                pass

    # ========================================================================= radio
    @work(thread=True, exclusive=True, group="radio")
    def _radio_continue(self, seed: Track, play: bool) -> None:
        try:
            similar = ytdl.radio(seed, self.cfg)
        except Exception as e:  # noqa: BLE001
            self.post(self._radio_done, seed, [], play, friendly_error(e))
            return
        self.post(self._radio_done, seed, similar, play, "")

    def _radio_done(self, seed: Track, similar: list[Track], play: bool, error: str) -> None:
        have = {t.key for t in self.queue.items}
        fresh = [t for t in similar if t.key not in have]
        if fresh:
            self.queue.extend(fresh)
            self.refresh_sidebar()
            if self.view == "queue":
                self.refresh_table()
        if play:
            if fresh:
                nxt = self.queue.next(auto=True)
                if nxt:
                    self.play_track(nxt)
                    return
            self.state = "stopped"
            self.render_now()
            if error:
                self.say(f"Radio: {error}", "warning")
        else:
            self.say(f"Radio: {len(fresh)} canciones añadidas a la cola" if fresh else (error or "Radio sin resultados"),
                     "information" if fresh else "warning")

    # ================================================================ acciones sobre pistas
    def action_key_do(self, name: str) -> None:
        getattr(self, f"_do_{name}")(self.target_track())

    def action_sel(self, name: str) -> None:
        getattr(self, f"_do_{name}")(self.selected_or_now())

    def action_now_fav(self) -> None:
        self._do_favorite(self.now)

    def _do_radio(self, t: Track | None) -> None:
        if not t or not t.id:
            self.say("Selecciona una canción de YouTube", "warning")
            return
        self.queue.set([t])
        self.play_track(t)
        self.say(f"Iniciando radio de «{truncate(t.title, 40)}»")
        self._radio_continue(t, play=False)

    def _do_queue_add(self, t: Track | None) -> None:
        if not t or not t.is_track:
            return
        was_idle = self.state == "stopped"
        self.queue.add(t)
        self.say(f"Añadida a la cola: {truncate(t.title, 40)}")
        self.refresh_sidebar()
        if self.view == "queue":
            self.refresh_table()
        if was_idle:
            self.queue.jump(len(self.queue.items) - 1)
            self.play_track(t)

    def _do_queue_next(self, t: Track | None) -> None:
        if not t or not t.is_track:
            return
        if not self.queue.items:
            self.queue.set([t])
            self.play_track(t)
            return
        self.queue.add_next(t)
        self.say(f"Sonará a continuación: {truncate(t.title, 40)}")
        self.refresh_sidebar()
        if self.view == "queue":
            self.refresh_table()

    def _do_favorite(self, t: Track | None) -> None:
        if not t or not t.is_track:
            return
        added = self.lib.toggle_favorite(t)
        self.say(("♥ Añadida a favoritos: " if added else "Quitada de favoritos: ") + truncate(t.title, 40))
        if added and self.cfg["auto_download_favorites"]:
            self.dm.add(t)
        self.refresh_sidebar()
        self.refresh_table()
        self.refresh_controls()

    def _do_download(self, t: Track | None) -> None:
        if not t:
            return
        if not t.is_track:
            self.say("Abre la lista y usa «Lista» para descargarla entera", "warning")
            return
        job, msg = self.dm.add(t)
        self.say(f"{msg}: {truncate(t.title, 40)}" if job else msg, "information" if job else "warning")
        self.refresh_sidebar()

    def _do_add_to_playlist(self, t: Track | None) -> None:
        if not t or not t.is_track:
            return
        names = list(self.lib.playlists)

        def add(name: str) -> None:
            ok = self.lib.add_to_playlist(name, t)
            self.say(f"Añadida a «{name}»" if ok else "Ya estaba en esa playlist", "information" if ok else "warning")
            if ok and name in self.lib.autodl_playlists:
                self.dm.add(t)
            self.refresh_sidebar()
            if self.view == f"pl:{name}":
                self.refresh_table()

        def chosen(i: int | None) -> None:
            if i is None:
                return
            if i == len(names):
                def created(name: str | None) -> None:
                    if name and (self.lib.create_playlist(name) or name in self.lib.playlists):
                        add(name)

                self.push_screen(PromptScreen("Nombre de la nueva playlist"), created)
            else:
                add(names[i])

        labels = [f"{ic('playlist')} {n}" for n in names] + [f"{ic('add')} Nueva playlist…"]
        self.push_screen(PickScreen("Añadir a playlist", labels), chosen)

    def _do_subscribe(self, t: Track | None) -> None:
        item = None
        if t and not t.is_track:
            item = t
        elif self.view == "search" and self.results_source:
            item = Track(title=self.results_title or self.last_query, url=self.results_source,
                         kind="playlist", artist="Playlist")
        if not item or not item.url:
            self.say("Selecciona una playlist/canal, o abre uno, para suscribirte", "warning")
            return
        on = self.lib.toggle_subscription(item)
        self.say(f"✦ Suscrito a «{truncate(item.title, 40)}»: se descargará lo nuevo" if on else "Suscripción cancelada")
        self.refresh_sidebar()
        self.refresh_table()
        if on:
            self.sync_subscriptions()

    def action_subscribe(self) -> None:
        self._do_subscribe(self.selected_track() if self.focused is self.table else None)

    # ----------------------------------------------------------- quitar / mover / vaciar
    def action_remove(self) -> None:
        i = self.selected_index()
        if i is None:
            return
        v = self.view
        if v == "queue":
            if i == self.queue.index and self.state != "stopped":
                self.say("No puedes quitar la pista que está sonando", "warning")
                return
            self.queue.remove(i)
        elif v.startswith("pl:"):
            self.lib.remove_from_playlist(v[3:], i)
        elif v == "favorites":
            self.lib.toggle_favorite(self.lib.favorites[i])
        elif v == "history":
            self.lib.remove_history(i)
        elif v == "downloads":
            self.dm.cancel(self.dm.jobs[i])
        elif v == "subs":
            self.lib.toggle_subscription(self.lib.subscriptions[i])
        elif v == "local":
            t = self.local_cache[i]

            def confirmed(yes: bool | None) -> None:
                if yes:
                    try:
                        os.remove(t.path)
                        lrc = os.path.splitext(t.path)[0] + ".lrc"
                        if os.path.isfile(lrc):
                            os.remove(lrc)
                    except OSError as e:
                        self.say(f"No se pudo borrar: {e}", "error")
                        return
                    self.lib.downloads.pop(t.key, None)
                    self.lib.save()
                    self.local_cache = self.lib.local_tracks()
                    self.refresh_table()
                    self.refresh_sidebar()

            self.push_screen(ConfirmScreen(f"¿Borrar del disco «{truncate(t.title, 40)}»?"), confirmed)
            return
        else:
            self.say("En los resultados no hay nada que quitar", "warning")
            return
        self.refresh_table()
        self.refresh_sidebar()

    def action_move(self, delta: int) -> None:
        i = self.selected_index()
        if i is None:
            return
        if self.view == "queue":
            new = self.queue.move(i, int(delta))
        elif self.view.startswith("pl:"):
            new = self.lib.move_in_playlist(self.view[3:], i, int(delta))
        else:
            self.say("Solo se puede reordenar la cola y las playlists", "warning")
            return
        self.refresh_table()
        if new in self.view_map:
            self.table.move_cursor(row=self.view_map.index(new))

    def action_clear(self) -> None:
        if self.view == "downloads":
            self.dm.clear_finished()
        else:
            self.queue.clear_upcoming()
            self.say("Cola vaciada (se mantiene la pista actual)")
        self.refresh_table(reset=True)
        self.refresh_sidebar()

    def action_delete_playlist(self) -> None:
        if not self.view.startswith("pl:"):
            self.say("Abre una playlist para eliminarla", "warning")
            return
        name = self.view[3:]

        def confirmed(yes: bool | None) -> None:
            if yes:
                self.lib.delete_playlist(name)
                self.say(f"Playlist «{name}» eliminada")
                self.show_view("search")

        self.push_screen(ConfirmScreen(f"¿Eliminar la playlist «{name}»?"), confirmed)

    def action_toggle_autodl(self) -> None:
        if not self.view.startswith("pl:"):
            self.say("Abre una playlist para activar la descarga automática", "warning")
            return
        name = self.view[3:]
        on = self.lib.toggle_autodl(name)
        if on:
            for t in self.lib.playlists.get(name, []):
                self.dm.add(t)
        self.say(f"Descarga automática de «{name}»: " + ("activada" if on else "desactivada"))
        self.refresh_sidebar()

    # =============================================================== descargas / sync
    def action_download_all(self) -> None:
        tracks = [t for t in self.current_tracks() if t.is_track and t.id and not self.lib.local_path(t)]
        if not tracks:
            self.say("No hay nada nuevo que descargar en esta lista", "warning")
            return

        def go(yes: bool | None = True) -> None:
            if yes:
                n = sum(1 for t in tracks if self.dm.add(t)[0])
                self.say(f"{n} canciones añadidas a descargas")
                self.refresh_sidebar()

        if len(tracks) > 10:
            self.push_screen(ConfirmScreen(f"¿Descargar {len(tracks)} canciones?"), go)
        else:
            go()

    def _dm_event(self, job) -> None:
        if job.status == "done":
            self.say(f"⬇ Descargada: {truncate(job.track.title, 40)}")
        elif job.status == "error":
            self.say(f"Descarga fallida: {job.message}", "error")
        self.refresh_sidebar()
        if self.view in ("downloads", "local"):
            if self.view == "local":
                self.local_cache = self.lib.local_tracks()
            self.refresh_table()

    @work(thread=True, exclusive=True, group="sync")
    def sync_subscriptions(self) -> None:
        added = 0
        for sub in list(self.lib.subscriptions):
            try:
                for t in ytdl.open_collection(sub, self.cfg):
                    if self.dm.add(t)[0]:
                        added += 1
            except Exception:  # noqa: BLE001
                log.warning("no se pudo sincronizar %s", sub.url)
        if added:
            self.post(self.say, f"✦ Suscripciones: {added} canciones nuevas en descargas")

    # ================================================= exportar / importar / menús
    def action_export_list(self) -> None:
        tracks = [t for t in self.current_tracks() if t.is_track]
        if not tracks:
            self.say("No hay canciones que exportar en esta vista", "warning")
            return
        name = (self.view[3:] if self.view.startswith("pl:") else self.view_title().split(" · ")[0]).replace("/", "-")
        default = str(self.cfg.download_dir / f"{name}.m3u8")

        def done(path: str | None) -> None:
            if path:
                try:
                    n = playlist_io.export_m3u(tracks, str(Path(path).expanduser()))
                    self.say(f"Exportadas {n} canciones a {path}")
                except OSError as e:
                    self.say(f"No se pudo exportar: {e}", "error")

        self.push_screen(PromptScreen("Exportar lista a M3U", value=default), done)

    def action_import_list(self) -> None:
        def done(path: str | None) -> None:
            if path:
                self._import_worker(str(Path(path).expanduser()))

        self.push_screen(PromptScreen("Importar lista (M3U, CSV de Spotify/Exportify o TXT)", "ruta del archivo"), done)

    @work(thread=True, exclusive=True, group="import")
    def _import_worker(self, path: str) -> None:
        try:
            entries = playlist_io.parse_file(path)
        except Exception as e:  # noqa: BLE001
            self.post(self.say, f"No se pudo leer el archivo: {e}", "error")
            return
        if not entries:
            self.post(self.say, "El archivo no tiene canciones", "warning")
            return
        tracks, failed = playlist_io.resolve_entries(
            entries, lambda q: ytdl.search_one(q, self.cfg),
            progress=lambda i, n: self.post(self._set_status, f"Importando {i}/{n}…"))
        self.post(self._import_done, Path(path).stem, tracks, failed)

    def _import_done(self, stem: str, tracks: list[Track], failed: list[str]) -> None:
        self._set_status("")
        name, n = stem, 2
        while name in self.lib.playlists:
            name, n = f"{stem} ({n})", n + 1
        self.lib.create_playlist(name)
        for t in tracks:
            self.lib.add_to_playlist(name, t)
        self.refresh_sidebar()
        msg = f"Importada «{name}»: {len(tracks)} canciones" + (f" ({len(failed)} no encontradas)" if failed else "")
        self.say(msg, "information" if tracks else "warning")
        self.show_view(f"pl:{name}")

    def open_context_menu(self) -> None:
        t = self.selected_track()
        if not t:
            return
        items: list[tuple[str, object]] = [(f"{ic('play')} Reproducir", self.activate_selected)]
        if t.is_track:
            items += [
                (f"{ic('add')} Añadir a la cola", lambda: self._do_queue_add(t)),
                (f"{ic('addnext')} Reproducir a continuación", lambda: self._do_queue_next(t)),
                (f"{ic('fav_on')} Favorito (sí/no)", lambda: self._do_favorite(t)),
                (f"{ic('dl')} Descargar", lambda: self._do_download(t)),
                (f"{ic('playlist')} Añadir a playlist…", lambda: self._do_add_to_playlist(t)),
                (f"{ic('radio')} Radio de esta canción", lambda: self._do_radio(t)),
            ]
        else:
            items.append((f"{ic('sub')} Suscribirse (sí/no)", lambda: self._do_subscribe(t)))
        if self.view in ("queue", "favorites", "history", "downloads", "subs", "local") or self.view.startswith("pl:"):
            items.append((f"{ic('remove')} Quitar", self.action_remove))
        if self.view == "queue" or self.view.startswith("pl:"):
            items += [(f"{ic('up')} Subir", lambda: self.action_move(-1)),
                      (f"{ic('down')} Bajar", lambda: self.action_move(1))]

        def chosen(i: int | None) -> None:
            if i is not None:
                items[i][1]()

        self.push_screen(PickScreen(truncate(t.title, 50), [lbl for lbl, _ in items]), chosen)

    def action_more_menu(self) -> None:
        items = [
            (f"{ic('export')} Exportar esta lista (M3U)…", self.action_export_list),
            (f"{ic('import_')} Importar lista (M3U / CSV / TXT)…", self.action_import_list),
            (f"{ic('sub')} Suscribirse a la playlist/canal", self.action_subscribe),
            (f"{ic('dl')} Auto-descargar esta playlist (sí/no)", self.action_toggle_autodl),
            (f"{ic('dl_all')} Descargar toda la lista", self.action_download_all),
            (f"{ic('remove')} Vaciar cola", self.action_clear),
            (f"{ic('recent')} Búsquedas recientes", self.action_recent_searches),
            (f"{ic('eq')} Ecualizador y velocidad", self.action_audio),
            (f"{ic('settings')} Ajustes", self.action_settings),
            (f"{ic('help')} Ayuda", self.action_help),
        ]

        def chosen(i: int | None) -> None:
            if i is not None:
                items[i][1]()

        self.push_screen(PickScreen("Más opciones", [lbl for lbl, _ in items]), chosen)

    # ====================================================================== controles
    def action_toggle_pause(self) -> None:
        if self.state in ("playing", "paused"):
            paused = self.player.toggle_pause()
            self.state = "paused" if paused else "playing"
            self.render_now()
        elif self.state == "stopped" and self.queue.current:
            self.play_track(self.queue.current, resume=True)
        elif self.state == "stopped" and (t := self.selected_track()) and t.is_track:
            self.queue.set([t])
            self.play_track(t)

    def action_next_track(self) -> None:
        nxt = self.queue.next(auto=False)
        if nxt:
            self.play_track(nxt)
        elif self.cfg["autoplay_radio"] and self.now and self.now.id:
            self.state = "loading"
            self.render_now()
            self._radio_continue(self.now, play=True)
        else:
            self.say("Fin de la cola")

    def action_prev_track(self) -> None:
        pos = self.player.position if self.state in ("playing", "paused") else None
        if pos is not None and pos > 5:
            self.player.seek_to(0)
            return
        prev = self.queue.prev()
        if prev:
            self.play_track(prev)

    def action_seek(self, secs: float) -> None:
        if self.state in ("playing", "paused"):
            self.player.seek(float(secs))

    def seek_fraction(self, frac: float) -> None:
        if self.state not in ("playing", "paused"):
            return
        dur = self.player.duration or (self.now.duration if self.now else None)
        if dur:
            self.player.seek_to(frac * dur)
            self.render_now()

    def set_volume(self, v: int) -> None:
        self.volume = max(0, min(130, int(v)))
        try:
            self.player.volume = self.volume
        except Exception:  # noqa: BLE001
            pass
        self.query_one("#volbar", VolumeBar).volume = self.volume
        self.render_now()

    def action_volume(self, delta: int) -> None:
        self.set_volume(self.volume + int(delta))

    def action_mute(self) -> None:
        self.player.toggle_mute()
        self.render_now()

    def action_shuffle(self) -> None:
        self.queue.set_shuffle(not self.queue.shuffle)
        self.say("Aleatorio " + ("activado" if self.queue.shuffle else "desactivado"))
        self.render_now()

    def action_repeat(self) -> None:
        order = ["off", "all", "one"]
        self.queue.repeat = order[(order.index(self.queue.repeat) + 1) % 3]
        self.say(f"Repetir: {REPEAT_LABEL[self.queue.repeat]}")
        self.render_now()

    def action_sleep_timer(self) -> None:
        self.sleep_idx = (self.sleep_idx + 1) % len(SLEEP_STEPS)
        minutes = SLEEP_STEPS[self.sleep_idx]
        self.sleep_at = time.time() + minutes * 60 if minutes else 0.0
        self.say(f"Apagado automático en {minutes} min" if minutes else "Temporizador desactivado")
        self.render_now()

    # ------------------------------------------------------------------ audio
    def apply_audio(self) -> None:
        try:
            self.player.set_audio(self.cfg["eq"], bool(self.cfg["normalize"]))
            self.player.speed = float(self.cfg["speed"])
        except Exception:  # noqa: BLE001
            log.exception("no se pudo aplicar el audio")

    def set_eq(self, name: str) -> None:
        self.cfg["eq"] = name
        self.cfg.save()
        self.apply_audio()
        self.refresh_controls()

    def set_speed(self, v: float) -> None:
        self.cfg["speed"] = v
        self.cfg.save()
        self.apply_audio()
        self.refresh_controls()

    def toggle_normalize(self) -> None:
        self.cfg["normalize"] = not self.cfg["normalize"]
        self.cfg.save()
        self.apply_audio()
        self.refresh_controls()

    def action_audio(self) -> None:
        self.push_screen(AudioScreen())

    # ============================================================== paneles laterales
    def _update_side(self) -> None:
        side = self.query_one("#side")
        side.set_class(any(self.query_one(f"#{p}").has_class("visible") for p in ("lyrics", "cover", "viz")), "visible")

    def _toggle_panel(self, panel: str, flag: str) -> bool:
        w = self.query_one(f"#{panel}")
        w.toggle_class("visible")
        on = w.has_class("visible")
        self.cfg[flag] = on
        self.cfg.save()
        self._update_side()
        self.refresh_controls()
        return on

    def action_toggle_lyrics(self) -> None:
        self._toggle_panel("lyrics", "show_lyrics")
        self.render_lyrics()

    def action_toggle_cover(self) -> None:
        if self._toggle_panel("cover", "show_cover") and self.now:
            self._load_cover(self.now)

    def action_toggle_viz(self) -> None:
        if self._toggle_panel("viz", "show_viz"):
            if not viz.available():
                self.say("Visualizador: instala numpy y ffmpeg", "warning")
            elif self.current_src and self.state in ("playing", "paused"):
                self.spectrum.load(self.current_src, self.current_headers.get("User-Agent", ""))
        else:
            self.spectrum.stop()

    # ---- letra
    @work(thread=True, exclusive=True, group="lyrics")
    def _load_lyrics(self, track: Track) -> None:
        found = None
        local = self.lib.local_path(track)
        if local:  # letra guardada junto al archivo (funciona sin internet)
            lrc = os.path.splitext(local)[0] + ".lrc"
            if os.path.isfile(lrc):
                try:
                    with open(lrc, encoding="utf-8") as f:
                        synced = parse_lrc(f.read())
                    found = Lyrics(synced=synced) if synced else None
                except OSError:
                    pass
        if not found:
            found = fetch_lyrics(track)
        if get_current_worker().is_cancelled:
            return
        self.post(self._lyrics_ready, track, found)

    def _lyrics_ready(self, track: Track, found: Lyrics | None) -> None:
        if self.now is not track:
            return
        self.lyrics, self.lyrics_state = found, ("" if found else "none")
        self.render_lyrics()

    def render_lyrics(self) -> None:
        panel = self.query_one("#lyrics", Static)
        if not panel.has_class("visible"):
            return
        panel.border_title = "Letra"
        if not self.now:
            panel.update(Text("Nada en reproducción", style="dim"))
        elif self.lyrics_state == "loading":
            panel.update(Text("Buscando letra…", style="dim"))
        elif not self.cfg["lyrics"]:
            panel.update(Text("La búsqueda de letras está desactivada (Ajustes → Buscar letra)", style="dim"))
        elif not self.lyrics:
            panel.update(Text("Letra no disponible", style="dim"))
        elif self.lyrics.synced:
            pos = self.player.position or 0.0
            cur = self.lyrics.index_at(pos)
            lines = self.lyrics.synced
            height = max(panel.size.height - 2, 5)
            start = max(0, min(cur - height // 2, len(lines) - height))
            out = Text(no_wrap=False)
            for i in range(start, min(start + height, len(lines))):
                out.append((lines[i][1] or "♪") + "\n", style="bold yellow" if i == cur else "dim")
            panel.update(out)
        else:
            panel.update(Text("\n".join(self.lyrics.plain)))

    # ---- portada
    @work(thread=True, exclusive=True, group="cover")
    def _load_cover(self, track: Track) -> None:
        if CoverImage is None:
            return
        try:
            from PIL import Image as PILImage

            data, from_web = None, False
            local = self.lib.local_path(track)
            if local:
                data = read_cover(local)
            if data is None and track.id:
                req = urllib.request.Request(f"https://i.ytimg.com/vi/{track.id}/hqdefault.jpg",
                                             headers={"User-Agent": "ytm-tui/1.0"})
                with urllib.request.urlopen(req, timeout=8) as r:  # noqa: S310
                    data = r.read()
                from_web = True
            if not data:
                return
            im = PILImage.open(io.BytesIO(data)).convert("RGB")
            w, h = im.size
            if from_web:
                top = int(h * 0.125)  # quita las franjas negras 16:9 de hqdefault
                im = im.crop((0, top, w, h - top))
                w, h = im.size
            side = min(w, h)
            im = im.crop(((w - side) // 2, (h - side) // 2, (w + side) // 2, (h + side) // 2)).resize((320, 320))
        except Exception:  # noqa: BLE001
            return
        if not get_current_worker().is_cancelled:
            self.post(self._set_cover, track, im)

    def _set_cover(self, track: Track, im) -> None:
        if self.now is track and CoverImage is not None:
            try:
                self.query_one("#cover", CoverImage).image = im
            except Exception:  # noqa: BLE001
                log.exception("portada")

    # ---- visualizador
    def tick_viz(self) -> None:
        try:
            self._tick_viz()
        except NoMatches:  # la pantalla se está cerrando
            pass

    def _tick_viz(self) -> None:
        if self._closing:
            return
        panel = self.query_one("#viz", Static)
        if not panel.has_class("visible"):
            return
        if not viz.available():
            panel.update(Text("Instala numpy + ffmpeg para ver el espectro", style="dim"))
            return
        if self.state != "playing":
            return
        height = max(panel.size.height - 2, 3)
        rows = viz.render_rows(self.spectrum.levels(self.player.position), height)
        out = Text(no_wrap=True)
        for r, line in enumerate(rows):
            frac = r / max(height - 1, 1)
            out.append(line + "\n", style="red" if frac < 0.34 else ("yellow" if frac < 0.67 else "green"))
        panel.update(out)

    # ===================================================== SponsorBlock / scrobble / avisos
    @work(thread=True, exclusive=True, group="sponsor")
    def _load_segments(self, track: Track) -> None:
        segs = fetch_segments(track.id, self.cfg["sponsorblock_categories"])
        if segs and not get_current_worker().is_cancelled:
            self.post(self._segments_ready, track, segs)

    def _segments_ready(self, track: Track, segs) -> None:
        if self.now is track:
            self.skip_segments = segs

    @staticmethod
    def _safe(fn, *args) -> None:
        try:
            fn(*args)
        except Exception:  # noqa: BLE001
            log.warning("integración falló", exc_info=True)

    def _begin_scrobble(self, track: Track, dur) -> None:
        self._scrobble = {"track": track, "ts": int(time.time()), "played": 0.0, "done": False}
        artist, title = clean_names(track)
        if self.lastfm.ready:
            threading.Thread(target=self._safe, daemon=True,
                             args=(self.lastfm.now_playing, artist, title, track.album, track.duration or dur)).start()
        if self.cfg["discord_client_id"]:
            threading.Thread(target=self._safe, args=(self.discord.update, track, track.duration or dur, 0.0),
                             daemon=True).start()
        if self.cfg["notifications"]:
            threading.Thread(target=desktop_notify, args=(title or track.title, artist), daemon=True).start()

    def _scrobble_tick(self) -> None:
        sc = self._scrobble
        if not sc or sc["done"] or self.state != "playing" or self.now is not sc["track"]:
            return
        sc["played"] += 0.5
        track = sc["track"]
        if self.lastfm.ready and should_scrobble(track.duration, sc["played"]):
            sc["done"] = True
            artist, title = clean_names(track)
            threading.Thread(target=self._safe, daemon=True,
                             args=(self.lastfm.scrobble, artist, title, sc["ts"], track.album, track.duration)).start()

    def connect_lastfm(self) -> None:
        """Pide API key, secreto, usuario y contraseña (la contraseña no se guarda)."""
        cfg = self.cfg

        def ask(title: str, key: str, nxt) -> None:
            def got(v: str | None) -> None:
                if v:
                    cfg[key] = v
                    nxt()

            self.push_screen(PromptScreen(title, value=str(cfg[key] or "")), got)

        def finish(password: str | None) -> None:
            if not password:
                return

            def work_() -> None:
                try:
                    lf = LastFM(cfg["lastfm_api_key"], cfg["lastfm_api_secret"])
                    cfg["lastfm_session"] = lf.connect(cfg["lastfm_user"], password)
                    cfg.save()
                    self.lastfm = lf
                    self.post(self.say, "Last.fm conectado ✔")
                except Exception as e:  # noqa: BLE001
                    self.post(self.say, f"Last.fm: {e}", "error")

            threading.Thread(target=work_, daemon=True).start()

        def pw() -> None:
            self.push_screen(PromptScreen("Contraseña de Last.fm (no se guarda)", password=True), finish)

        self.say("Crea tu API key gratis en last.fm/api/account/create")
        ask("API key de Last.fm", "lastfm_api_key",
            lambda: ask("Shared secret de la API", "lastfm_api_secret",
                        lambda: ask("Usuario de Last.fm", "lastfm_user", pw)))

    # ================================================================ control remoto
    def start_remote(self, announce: bool = True) -> None:
        if self.remote:
            return
        if not self.cfg["remote_token"]:
            import secrets

            self.cfg["remote_token"] = secrets.token_urlsafe(8)
        try:
            self.remote = RemoteServer(self, int(self.cfg["remote_port"]), self.cfg["remote_token"])
            self.remote.start()
        except OSError as e:
            self.remote = None
            self.say(f"No se pudo abrir el control remoto: {e}", "error")
            return
        self.cfg["remote_enabled"] = True
        self.cfg.save()
        if announce:
            self.notify(f"Abre en tu celular (misma red wifi):\n{self.remote.url}", title="Control remoto", timeout=20)
        self.refresh_controls()

    def stop_remote(self) -> None:
        if self.remote:
            self.remote.stop()
            self.remote = None

    def action_toggle_remote(self) -> None:
        if self.remote:
            self.stop_remote()
            self.cfg["remote_enabled"] = False
            self.say("Control remoto desactivado")
        else:
            self.start_remote()
        self.cfg.save()
        self.refresh_controls()

    def _build_remote_state(self) -> dict:
        playing = self.state in ("playing", "paused")
        pos = self.player.position if playing else 0
        dur = (self.player.duration if playing else None) or (self.now.duration if self.now else 0)
        return {
            "now": {"title": self.now.title, "artist": self.now.artist} if self.now else None,
            "state": self.state, "pos": pos or 0, "dur": dur or 0, "volume": self.volume,
            "shuffle": self.queue.shuffle, "repeat": self.queue.repeat, "index": self.queue.index,
            "queue": [{"title": t.title, "artist": t.artist} for t in self.queue.items[:200]],
        }

    def remote_state(self) -> dict:
        if self._remote_snapshot:
            return self._remote_snapshot
        try:
            return self._build_remote_state()
        except Exception:  # noqa: BLE001
            return {"now": None, "state": "stopped", "pos": 0, "dur": 0, "volume": self.volume,
                    "shuffle": False, "repeat": "off", "index": -1, "queue": []}

    def remote_search(self, q: str) -> list[dict]:
        if not q.strip():
            return []
        return [t.to_dict() for t in ytdl.search_tracks(q, self.cfg)]

    def remote_command(self, cmd: dict) -> dict:
        name = cmd.get("cmd")
        if name == "toggle_pause":
            self.action_toggle_pause()
        elif name == "next":
            self.action_next_track()
        elif name == "prev":
            self.action_prev_track()
        elif name == "shuffle":
            self.action_shuffle()
        elif name == "repeat":
            self.action_repeat()
        elif name == "radio":
            self._do_radio(self.now)
        elif name == "seek":
            self.action_seek(float(cmd.get("delta", 0)))
        elif name == "seek_to":
            if self.state in ("playing", "paused"):
                self.player.seek_to(float(cmd.get("seconds", 0)))
        elif name == "volume":
            self.set_volume(int(cmd["value"]) if "value" in cmd else self.volume + int(cmd.get("delta", 0)))
        elif name == "jump":
            t = self.queue.jump(int(cmd.get("index", -1)))
            if t:
                self.play_track(t)
        elif name in ("play", "enqueue") and isinstance(cmd.get("track"), dict):
            t = Track.from_dict(cmd["track"])
            if name == "play":
                self.queue.set([t])
                self.play_track(t)
            else:
                self._do_queue_add(t)
        else:
            return {"ok": False, "error": "comando desconocido"}
        self._remote_snapshot = self._build_remote_state()
        return {"ok": True}

    # ============================================================== ajustes (efectos)
    def action_settings(self) -> None:
        self.push_screen(SettingsScreen())

    def apply_setting(self, key: str) -> None:
        if key == "icons":
            set_icon_set(self.cfg["icons"])
            self.refresh_labels()
            self.refresh_sidebar()
            self.refresh_table()
        elif key in ("remote_enabled", "remote_port"):
            self.stop_remote()
            if self.cfg["remote_enabled"]:
                self.start_remote()
        elif key == "download_dir":
            self.cfg.download_dir.mkdir(parents=True, exist_ok=True)
        elif key == "discord_client_id":
            self.discord = DiscordPresence(self.cfg["discord_client_id"])
        self.refresh_controls()

    # =========================================================== barra de reproducción
    def tick(self) -> None:
        try:
            self._tick()
        except NoMatches:  # la pantalla se está cerrando
            pass

    def _tick(self) -> None:
        if self._closing:
            return
        self._ticks += 1
        if self.sleep_at and time.time() >= self.sleep_at:
            self.sleep_at, self.sleep_idx = 0.0, 0
            if self.state == "playing":
                self.player.toggle_pause()
                self.state = "paused"
            self.say("Temporizador: reproducción en pausa", "warning")
        if self.state == "playing" and self.skip_segments and self.cfg["sponsorblock"]:
            pos = self.player.position
            target = skip_target(self.skip_segments, pos) if pos is not None else None
            if target and target != self._last_skip:
                self._last_skip = target
                self.player.seek_to(target)
                self.say("⏭ Saltando una parte sin música")
        self._scrobble_tick()
        self.render_now()
        self.render_lyrics()
        if self.view == "downloads" and self.dm.active_count():
            self.refresh_table()
        if self.remote:
            try:
                self._remote_snapshot = self._build_remote_state()
            except Exception:  # noqa: BLE001
                pass
        if self._ticks % 40 == 0 and self.queue.items:
            pos = self.player.position if self.state in ("playing", "paused") else 0
            save_session(self.queue, pos)

    def refresh_controls(self) -> None:
        def b(bid: str) -> IconButton:
            return self.query_one(f"#{bid}", IconButton)

        try:
            b("b-play").set_label(ic("pause") if self.state == "playing" else ic("play"))
            b("b-shuf").set_class(self.queue.shuffle, "on")
            rep = self.queue.repeat
            b("b-rep").set_label(ic({"off": "repeat", "all": "repeat_all", "one": "repeat_one"}[rep]))
            b("b-rep").set_class(rep != "off", "on")
            b("b-mute").set_class(self.player.muted, "on")
            fav = bool(self.now and self.lib.is_favorite(self.now))
            b("b-fav").set_label(ic("fav_on") if fav else ic("fav"))
            b("b-fav").set_class(fav, "on")
            for bid, panel in (("b-lyr", "lyrics"), ("b-cov", "cover"), ("b-viz", "viz")):
                b(bid).set_class(self.query_one(f"#{panel}").has_class("visible"), "on")
            eq_on = self.cfg["eq"] != "flat" or abs(self.cfg["speed"] - 1) > 0.01 or bool(self.cfg["normalize"])
            b("b-eq").set_class(eq_on, "on")
            mins = max(int((self.sleep_at - time.time()) / 60) + 1, 0) if self.sleep_at else 0
            b("b-sleep").set_label(f"{ic('sleep')} {mins}m" if self.sleep_at else ic("sleep"))
            b("b-sleep").set_class(bool(self.sleep_at), "on")
            b("b-rem").set_class(self.remote is not None, "on")
            b("s-songs").set_class(self.search_kind == "songs", "on")
            b("s-lists").set_class(self.search_kind == "playlists", "on")
            b("s-art").set_class(self.search_kind == "artists", "on")
            b("s-music").set_class(self.cfg["search_source"] == "music", "on")
            b("s-only").set_class(bool(self.cfg["songs_only"]), "on")
            b("t-filter").set_class(self.query_one("#filter").has_class("visible"), "on")
        except Exception:  # noqa: BLE001  (la pantalla aún se está montando)
            pass

    def render_now(self) -> None:
        t = self.now
        l1 = Text(no_wrap=True, overflow="ellipsis")
        if not t:
            l1.append("Nada en reproducción — busca algo, haz doble clic en una canción o pulsa ? para la ayuda",
                      style="dim")
        else:
            icon = {"loading": "…", "playing": ic("play"), "paused": ic("pause"), "stopped": "■"}[self.state]
            l1.append(f"{icon} ", style="bold red")
            l1.append(t.title, style="bold")
            if t.artist:
                l1.append(f"  —  {t.artist}", style="dim")
            if self.lib.local_path(t):
                l1.append(f"  {ic('dl')}", style="green")
            extras = []
            if self.cfg["eq"] != "flat":
                extras.append(f"EQ {EQ_PRESETS[self.cfg['eq']][0]}")
            if abs(self.cfg["speed"] - 1) > 0.01:
                extras.append(f"{self.cfg['speed']:g}×")
            if self.cfg["normalize"]:
                extras.append("norm")
            if extras:
                l1.append("   [" + " · ".join(extras) + "]", style="cyan")
        self.query_one("#now", Static).update(l1)
        playing = self.state in ("playing", "paused")
        resume_pos = self.resume[1] if self.resume and t and self.resume[0] == t.key else None
        pos = self.player.position if playing else resume_pos
        dur = (self.player.duration if playing else None) or (t.duration if t else None)
        self.query_one("#seek", SeekBar).set_state(pos, dur, self.skip_segments)
        vb = self.query_one("#volbar", VolumeBar)
        vb.volume, vb.muted = self.volume, self.player.muted
        self.refresh_controls()

    # ======================================================================= varios
    def action_help(self) -> None:
        self.push_screen(HelpScreen())

    def action_quit_app(self) -> None:
        self._closing = True  # detiene los temporizadores antes de desmontar la pantalla
        self.exit()
