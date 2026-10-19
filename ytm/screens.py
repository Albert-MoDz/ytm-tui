from __future__ import annotations

from rich.text import Text
from textual import on
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Input, OptionList, Static
from textual.widgets.option_list import Option

from .player import EQ_PRESETS, SPEEDS

HELP = """\
[b]RATÓN[/b]  (todo lo de abajo también se hace con clic)
  Botones ⏮ ▶ ⏭ ⏪ ⏩       Anterior · pausa/reproducir · siguiente · ±10 s
  Barra de progreso         Clic o arrastrar para saltar a ese punto; rueda = ±5 s
  Barra de volumen          Clic o arrastrar; rueda = ±5
  Lista de canciones        Doble clic = reproducir · clic derecho = menú · rueda = desplazar
  Barra de herramientas     Actúa sobre la canción seleccionada (♥, ⬇, ✚, ♫, ◉...)
  Menú lateral              Clic para cambiar de vista; «+ Nueva playlist» crea una

[b]REPRODUCCIÓN[/b]
  Enter  Reproducir · Espacio  Pausa · n/b  Siguiente/anterior · ← →  ±5 s (Shift ±30 s)
  = -  Volumen · m  Mute · s  Aleatorio · r  Repetir · z  Temporizador · e  Ecualizador/velocidad

[b]BIBLIOTECA[/b]
  /  Buscar · h  Búsquedas recientes · a/A  A la cola / a continuación · R  Radio
  f  Favorito · p  Playlist · d/D  Descargar canción / lista · x  Quitar · c  Vaciar cola
  Ctrl+↑/↓  Mover en cola/playlist · Ctrl+F  Filtrar la lista · X  Borrar playlist
  E  Exportar lista (M3U) · I  Importar (M3U/CSV/TXT) · u  Suscribirse a la playlist/canal
  w  Auto-descargar la playlist actual

[b]PANELES Y VARIOS[/b]
  y  Letra · i  Portada · v  Visualizador · ctrl+r  Control remoto web · o  Ajustes
  t  Filtro «solo canciones» · S  Fuente YouTube/YT Music · 1-7  Vistas · ?  Ayuda · q  Salir
"""


class PromptScreen(ModalScreen[str | None]):
    BINDINGS = [Binding("escape", "cancel", "Cancelar")]
    DEFAULT_CSS = """
    PromptScreen { align: center middle; }
    PromptScreen > Vertical { width: 64; height: auto; border: round $accent; background: $surface; padding: 1 2; }
    """

    def __init__(self, title: str, placeholder: str = "", value: str = "", password: bool = False):
        super().__init__()
        self._title, self._ph, self._value, self._pw = title, placeholder, value, password

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Static(Text(self._title, style="bold"))
            yield Input(value=self._value, placeholder=self._ph, password=self._pw, id="prompt-input")

    def on_mount(self) -> None:
        self.query_one(Input).focus()

    @on(Input.Submitted)
    def _submit(self, event: Input.Submitted) -> None:
        self.dismiss(event.value.strip() or None)

    def action_cancel(self) -> None:
        self.dismiss(None)


class PickScreen(ModalScreen[int | None]):
    """Lista de opciones con clic o Enter. Devuelve el índice elegido."""

    BINDINGS = [Binding("escape", "cancel", "Cancelar")]
    DEFAULT_CSS = """
    PickScreen { align: center middle; }
    PickScreen > Vertical { width: 62; height: auto; max-height: 26; border: round $accent; background: $surface; padding: 1 2; }
    PickScreen OptionList { height: auto; max-height: 18; }
    """

    def __init__(self, title: str, labels: list[str]):
        super().__init__()
        self._title, self._labels = title, labels

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Static(Text(self._title, style="bold"))
            yield OptionList(*[Option(Text(lbl), id=str(i)) for i, lbl in enumerate(self._labels)])

    def on_mount(self) -> None:
        self.query_one(OptionList).focus()

    @on(OptionList.OptionSelected)
    def _selected(self, event: OptionList.OptionSelected) -> None:
        self.dismiss(int(event.option_id))

    def action_cancel(self) -> None:
        self.dismiss(None)


class ConfirmScreen(ModalScreen[bool]):
    BINDINGS = [Binding("escape,n", "no", "No"), Binding("y,enter", "yes", "Sí")]
    DEFAULT_CSS = """
    ConfirmScreen { align: center middle; }
    ConfirmScreen > Vertical { width: 60; height: auto; border: round $warning; background: $surface; padding: 1 2; }
    ConfirmScreen OptionList { height: 4; margin-top: 1; }
    """

    def __init__(self, question: str):
        super().__init__()
        self._q = question

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Static(Text(self._q))
            yield OptionList(Option("Sí", id="y"), Option("No", id="n"))

    def on_mount(self) -> None:
        self.query_one(OptionList).focus()

    @on(OptionList.OptionSelected)
    def _selected(self, event: OptionList.OptionSelected) -> None:
        self.dismiss(event.option_id == "y")

    def action_yes(self) -> None:
        self.dismiss(True)

    def action_no(self) -> None:
        self.dismiss(False)


class HelpScreen(ModalScreen[None]):
    BINDINGS = [Binding("escape,question_mark,q,enter", "close", "Cerrar")]
    DEFAULT_CSS = """
    HelpScreen { align: center middle; }
    HelpScreen > Vertical { width: 96; height: auto; max-height: 92%; border: round $accent; background: $surface; padding: 1 2; overflow-y: auto; }
    """

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Static(HELP)

    def on_click(self) -> None:
        self.dismiss(None)

    def action_close(self) -> None:
        self.dismiss(None)


class AudioScreen(ModalScreen[None]):
    """Ecualizador, velocidad y normalización. Se aplica al instante, sin cerrar la ventana."""

    BINDINGS = [Binding("escape,e", "close", "Cerrar")]
    DEFAULT_CSS = """
    AudioScreen { align: center middle; }
    AudioScreen > Vertical { width: 50; height: auto; max-height: 90%; border: round $accent; background: $surface; padding: 1 2; }
    AudioScreen OptionList { height: auto; max-height: 28; }
    """

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Static(Text("Audio  (clic para aplicar · Esc para cerrar)", style="bold"))
            yield OptionList(id="audio-list")

    def on_mount(self) -> None:
        self._build()
        self.query_one(OptionList).focus()

    def _build(self) -> None:
        app, ol = self.app, self.query_one(OptionList)
        keep = ol.highlighted
        ol.clear_options()
        mark = lambda on: "● " if on else "○ "  # noqa: E731
        ol.add_option(Option(Text("Ecualizador", style="bold dim"), disabled=True))
        for key, (label, _g) in EQ_PRESETS.items():
            ol.add_option(Option(Text(mark(app.cfg["eq"] == key) + label), id=f"eq:{key}"))
        ol.add_option(Option(Text("Velocidad", style="bold dim"), disabled=True))
        for sp in SPEEDS:
            ol.add_option(Option(Text(mark(abs(app.cfg["speed"] - sp) < 0.01) + f"{sp:g}×"), id=f"sp:{sp}"))
        ol.add_option(Option(Text("Volumen", style="bold dim"), disabled=True))
        ol.add_option(Option(Text(("☑ " if app.cfg["normalize"] else "☐ ") + "Normalizar volumen entre canciones"), id="norm"))
        if keep is not None:
            ol.highlighted = keep

    @on(OptionList.OptionSelected)
    def _selected(self, event: OptionList.OptionSelected) -> None:
        kind, _, val = (event.option_id or "").partition(":")
        if kind == "eq":
            self.app.set_eq(val)
        elif kind == "sp":
            self.app.set_speed(float(val))
        elif event.option_id == "norm":
            self.app.toggle_normalize()
        self._build()

    def action_close(self) -> None:
        self.dismiss(None)


# (clave, etiqueta, tipo, opciones)  — tipo: bool | choice | int | str | action | None (encabezado)
SETTINGS: list[tuple] = [
    (None, "Interfaz", None, None),
    ("icons", "Iconos", "choice", ["unicode", "ascii"]),
    (None, "Búsqueda", None, None),
    ("search_source", "Fuente de búsqueda", "choice", ["youtube", "music"]),
    ("songs_only", "Solo canciones (oculta videos largos)", "bool", None),
    ("search_results", "Resultados por búsqueda", "int", None),
    (None, "Reproducción", None, None),
    ("autoplay_radio", "Radio automática al acabar la cola", "bool", None),
    ("lyrics", "Buscar letra", "bool", None),
    ("sponsorblock", "Saltar partes sin música (SponsorBlock)", "bool", None),
    ("notifications", "Notificaciones de escritorio", "bool", None),
    ("media_keys", "Teclas multimedia (reiniciar)", "bool", None),
    (None, "Descargas", None, None),
    ("download_dir", "Carpeta de descargas", "str", None),
    ("audio_format", "Formato de audio", "choice", ["mp3", "m4a", "opus", "flac"]),
    ("audio_quality", "Calidad MP3 (kbps)", "choice", ["128", "192", "256", "320"]),
    ("embed_thumbnail", "Incrustar portada", "bool", None),
    ("save_lrc", "Guardar letra (.lrc) junto al archivo", "bool", None),
    ("sponsorblock_download", "Cortar partes sin música al descargar", "bool", None),
    ("auto_download_favorites", "Descargar favoritos automáticamente", "bool", None),
    ("concurrent_downloads", "Descargas simultáneas (reiniciar)", "int", None),
    (None, "Conexiones", None, None),
    ("remote_enabled", "Control remoto web (celular)", "bool", None),
    ("remote_port", "Puerto del control remoto", "int", None),
    ("__lastfm", "Last.fm: conectar cuenta…", "action", None),
    ("discord_client_id", "Discord: ID de la aplicación", "str", None),
    ("cookies_file", "Archivo cookies.txt", "str", None),
]


def _fmt(cfg, key: str, kind: str) -> str:
    v = cfg.get(key)
    if kind == "bool":
        return "☑ sí" if v else "☐ no"
    if kind == "action":
        return "conectada ✔" if cfg.get("lastfm_session") else ""
    return str(v) if v not in ("", None) else "—"


class SettingsScreen(ModalScreen[None]):
    BINDINGS = [Binding("escape,o", "close", "Cerrar")]
    DEFAULT_CSS = """
    SettingsScreen { align: center middle; }
    SettingsScreen > Vertical { width: 78; height: 90%; border: round $accent; background: $surface; padding: 1 2; }
    SettingsScreen OptionList { height: 1fr; }
    """

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Static(Text("Ajustes  (clic o Enter para cambiar · Esc para cerrar)", style="bold"))
            yield OptionList(id="settings-list")

    def on_mount(self) -> None:
        self._build()
        self.query_one(OptionList).focus()

    def _build(self) -> None:
        ol = self.query_one(OptionList)
        keep = ol.highlighted
        ol.clear_options()
        for i, (key, label, kind, _choices) in enumerate(SETTINGS):
            if kind is None:
                ol.add_option(Option(Text(f"── {label} ──", style="bold dim"), disabled=True))
            else:
                ol.add_option(Option(Text(f"{label}: {_fmt(self.app.cfg, key, kind)}"), id=str(i)))
        if keep is not None:
            ol.highlighted = keep

    @on(OptionList.OptionSelected)
    def _selected(self, event: OptionList.OptionSelected) -> None:
        key, label, kind, choices = SETTINGS[int(event.option_id)]
        cfg = self.app.cfg
        if kind == "bool":
            cfg[key] = not cfg[key]
            self._applied(key)
        elif kind == "choice":
            cfg[key] = choices[(choices.index(str(cfg[key])) + 1) % len(choices)] if str(cfg[key]) in choices else choices[0]
            self._applied(key)
        elif kind in ("int", "str"):
            def done(value: str | None, key=key, kind=kind) -> None:
                if value is None:
                    return
                if kind == "int":
                    try:
                        cfg[key] = max(1, int(value))
                    except ValueError:
                        self.app.say("Escribe un número", "warning")
                        return
                else:
                    cfg[key] = value
                self._applied(key)

            self.app.push_screen(PromptScreen(label, value=str(cfg[key] or "")), done)
        elif kind == "action":
            self.app.connect_lastfm()

    def _applied(self, key: str) -> None:
        self.app.cfg.save()
        self.app.apply_setting(key)
        self._build()

    def action_close(self) -> None:
        self.dismiss(None)
