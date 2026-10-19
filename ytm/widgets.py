"""Widgets propios para usar el ratón: botones con icono, barra de progreso, volumen y tabla con
doble clic / clic derecho."""
from __future__ import annotations

import time

from rich.text import Text
from textual import events
from textual.binding import Binding
from textual.reactive import reactive
from textual.widget import Widget
from textual.widgets import DataTable, Static

from .models import fmt_time


class IconButton(Static):
    """Botón de una línea: ejecuta una acción de la app al hacer clic. `.on` = activo."""

    DEFAULT_CSS = """
    IconButton { width: auto; height: 1; padding: 0 1; margin: 0 1 0 0; background: $boost; color: $text; }
    IconButton:hover { background: $accent; color: $text; text-style: bold; }
    IconButton.on { background: $primary; text-style: bold; }
    IconButton.on:hover { background: $accent; }
    """

    def __init__(self, label: str, action: str, tip: str = "", **kwargs):
        super().__init__(Text(label, no_wrap=True), **kwargs)
        self.act = action
        self.label = label
        if tip:
            self.tooltip = tip

    def set_label(self, label: str) -> None:
        if label != self.label:
            self.label = label
            self.update(Text(label, no_wrap=True))

    async def on_click(self, event: events.Click) -> None:
        event.stop()
        await self.app.run_action(self.act)


class SeekBar(Widget):
    """Barra de progreso: clic o arrastrar para saltar a ese punto; rueda = ±5 s."""

    DEFAULT_CSS = "SeekBar { height: 1; width: 1fr; }"

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.pos: float | None = None
        self.dur: float | None = None
        self.marks: list[tuple[float, float]] = []  # segmentos que se saltarán (SponsorBlock)
        self._x0, self._w = 0, 1
        self._drag = False
        self._last_seek = 0.0

    def set_state(self, pos: float | None, dur: float | None, marks=None) -> None:
        if (pos, dur) != (self.pos, self.dur) or marks is not None:
            self.pos, self.dur = pos, dur
            if marks is not None:
                self.marks = marks
            self.refresh()

    def render(self) -> Text:
        left = fmt_time(self.pos if self.pos is not None else 0) + " "
        right = " " + fmt_time(self.dur)
        width = max(self.size.width - len(left) - len(right), 4)
        self._x0, self._w = len(left), width
        frac = min(max((self.pos or 0) / self.dur, 0), 1) if self.dur else 0
        filled = int(frac * (width - 1))
        cells = ["─"] * width
        styles = ["dim"] * width
        for s, e in self.marks:
            if self.dur:
                for i in range(int(s / self.dur * width), min(int(e / self.dur * width) + 1, width)):
                    cells[i], styles[i] = "▒", "yellow"
        for i in range(filled):
            cells[i], styles[i] = "━", "red"
        cells[filled], styles[filled] = "●", "bold red"
        out = Text(left, style="bold", no_wrap=True)
        for c, st in zip(cells, styles):
            out.append(c, style=st)
        out.append(right, style="bold")
        return out

    def _seek_x(self, x: float) -> None:
        if not self.dur:
            return
        frac = min(max((x - self._x0) / max(self._w - 1, 1), 0.0), 1.0)
        self.app.seek_fraction(frac)

    def on_mouse_down(self, event: events.MouseDown) -> None:
        if event.button == 1:
            self._drag = True
            self.capture_mouse()
            self._seek_x(event.x)
            self._last_seek = time.time()

    def on_mouse_move(self, event: events.MouseMove) -> None:
        if self._drag and time.time() - self._last_seek > 0.08:
            self._last_seek = time.time()
            self._seek_x(event.x)

    def on_mouse_up(self, event: events.MouseUp) -> None:
        if self._drag:
            self._drag = False
            self.release_mouse()
            self._seek_x(event.x)

    def on_mouse_scroll_up(self, event: events.MouseScrollUp) -> None:
        event.stop()
        self.app.action_seek(5)

    def on_mouse_scroll_down(self, event: events.MouseScrollDown) -> None:
        event.stop()
        self.app.action_seek(-5)


class VolumeBar(Widget):
    """Control de volumen: clic o arrastrar; rueda = ±5."""

    DEFAULT_CSS = "VolumeBar { height: 1; width: 22; }"
    volume = reactive(80)
    muted = reactive(False)

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._drag = False
        self._bar_w = 14

    def render(self) -> Text:
        v = min(max(self.volume, 0), 100)
        filled = int(v / 100 * (self._bar_w - 1))
        out = Text(no_wrap=True)
        style = "dim" if self.muted else "red"
        out.append("━" * filled, style=style)
        out.append("●", style="bold " + style)
        out.append("─" * (self._bar_w - 1 - filled), style="dim")
        out.append(f" {self.volume:3d}%" if not self.muted else "  mute", style="bold")
        return out

    def _set_x(self, x: float) -> None:
        self.app.set_volume(round(min(max(x / max(self._bar_w - 1, 1), 0.0), 1.0) * 100))

    def on_mouse_down(self, event: events.MouseDown) -> None:
        if event.button == 1:
            self._drag = True
            self.capture_mouse()
            self._set_x(event.x)

    def on_mouse_move(self, event: events.MouseMove) -> None:
        if self._drag:
            self._set_x(event.x)

    def on_mouse_up(self, event: events.MouseUp) -> None:
        if self._drag:
            self._drag = False
            self.release_mouse()

    def on_mouse_scroll_up(self, event: events.MouseScrollUp) -> None:
        event.stop()
        self.app.action_volume(5)

    def on_mouse_scroll_down(self, event: events.MouseScrollDown) -> None:
        event.stop()
        self.app.action_volume(-5)


class TrackTable(DataTable):
    """Tabla de canciones. Doble clic = reproducir, clic derecho = menú contextual.
    ← → buscan dentro de la pista en lugar de mover el cursor."""

    BINDINGS = [
        Binding("left", "app.seek(-5)", "-5s", show=False),
        Binding("right", "app.seek(5)", "+5s", show=False),
        Binding("shift+left", "app.seek(-30)", "-30s", show=False),
        Binding("shift+right", "app.seek(30)", "+30s", show=False),
    ]

    def on_click(self, event: events.Click) -> None:
        if event.button == 3:
            event.stop()
            self.call_after_refresh(self.app.open_context_menu)
        elif event.button == 1 and event.chain == 2:
            self.call_after_refresh(self.app.activate_selected)
