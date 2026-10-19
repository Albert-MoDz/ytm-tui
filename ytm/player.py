from __future__ import annotations

import random
import threading

from .models import Track


class PlayQueue:
    """Cola de reproducción con shuffle, repetición e historial para 'anterior'."""

    def __init__(self):
        self.items: list[Track] = []
        self.index = -1
        self.shuffle = False
        self.repeat = "off"  # off | all | one
        self._trail: list[int] = []
        self._played: set[int] = set()
        self._pick: int | None = None

    # ---- estado ----
    @property
    def current(self) -> Track | None:
        return self.items[self.index] if 0 <= self.index < len(self.items) else None

    def _reset_play_state(self) -> None:
        self._trail.clear()
        self._played = {self.index} if self.index >= 0 else set()
        self._pick = None

    # ---- edición ----
    def set(self, tracks: list[Track], start: int = 0) -> None:
        self.items = list(tracks)
        self.index = start if self.items else -1
        self._reset_play_state()

    def jump(self, index: int) -> Track | None:
        if 0 <= index < len(self.items):
            if self.index >= 0:
                self._trail.append(self.index)
            self.index = index
            self._played.add(index)
            self._pick = None
            return self.current
        return None

    def add(self, track: Track) -> None:
        self.items.append(track)
        self._pick = None

    def extend(self, tracks: list[Track]) -> None:
        self.items.extend(tracks)
        self._pick = None

    def add_next(self, track: Track) -> None:
        self.items.insert(self.index + 1, track)
        self._played = {i if i <= self.index else i + 1 for i in self._played}
        self._trail = [i if i <= self.index else i + 1 for i in self._trail]
        self._pick = None

    def remove(self, i: int) -> None:
        if not 0 <= i < len(self.items):
            return
        self.items.pop(i)
        fix = lambda idx: idx - 1 if idx > i else idx  # noqa: E731
        self._played = {fix(x) for x in self._played if x != i}
        self._trail = [fix(x) for x in self._trail if x != i]
        if i < self.index:
            self.index -= 1
        elif i == self.index:
            self.index = min(self.index, len(self.items) - 1) if self.items else -1
        self._pick = None

    def move(self, i: int, delta: int) -> int:
        """Mueve el elemento i (delta posiciones). Devuelve su nueva posición."""
        j = max(0, min(len(self.items) - 1, i + delta))
        if not self.items or j == i:
            return i

        def fix(idx: int) -> int:
            if idx == i:
                return j
            if i < idx <= j:
                return idx - 1
            if j <= idx < i:
                return idx + 1
            return idx

        self.items.insert(j, self.items.pop(i))
        self.index = fix(self.index) if self.index >= 0 else self.index
        self._played = {fix(x) for x in self._played}
        self._trail = [fix(x) for x in self._trail]
        self._pick = None
        return j

    def clear_upcoming(self) -> None:
        """Quita todo salvo la pista actual."""
        cur = self.current
        self.items = [cur] if cur else []
        self.index = 0 if cur else -1
        self._reset_play_state()

    def set_shuffle(self, value: bool) -> None:
        self.shuffle = value
        self._pick = None

    # ---- navegación ----
    def _random_candidate(self) -> int | None:
        left = [i for i in range(len(self.items)) if i not in self._played]
        if not left and self.repeat == "all" and len(self.items) > 1:
            left = [i for i in range(len(self.items)) if i != self.index]
        return random.choice(left) if left else None

    def peek_next(self) -> Track | None:
        """Siguiente pista sin avanzar (sirve para precargar el stream)."""
        if self.repeat == "one" and self.current:
            return self.current
        if self.shuffle:
            if self._pick is None or self._pick >= len(self.items):
                self._pick = self._random_candidate()
            return self.items[self._pick] if self._pick is not None else None
        nxt = self.index + 1
        if nxt < len(self.items):
            return self.items[nxt]
        return self.items[0] if self.repeat == "all" and self.items else None

    def next(self, auto: bool = False) -> Track | None:
        if not self.items:
            return None
        if auto and self.repeat == "one":
            return self.current
        if self.shuffle:
            if self._pick is None or self._pick >= len(self.items):
                self._pick = self._random_candidate()
            idx = self._pick
            if idx is None:
                return None
            if not [i for i in range(len(self.items)) if i not in self._played]:
                self._played = {self.index}  # nueva vuelta (repeat all)
        else:
            idx = self.index + 1
            if idx >= len(self.items):
                if self.repeat == "all" or (not auto and self.repeat == "one"):
                    idx = 0
                else:
                    return None
        return self.jump(idx)

    def prev(self) -> Track | None:
        if not self.items:
            return None
        if self._trail:
            self.index = self._trail.pop()
        elif self.index > 0:
            self.index -= 1
        elif self.repeat == "all":
            self.index = len(self.items) - 1
        self._pick = None
        return self.current


EQ_BANDS = [60, 230, 910, 3600, 14000]
EQ_PRESETS = {
    "flat": ("Plano", [0, 0, 0, 0, 0]),
    "bass": ("Más graves", [7, 4, 0, 0, 0]),
    "treble": ("Más agudos", [0, 0, 0, 4, 7]),
    "voice": ("Voz", [-3, 0, 4, 4, 0]),
    "rock": ("Rock", [5, 3, -2, 3, 5]),
    "pop": ("Pop", [-1, 3, 5, 3, -1]),
    "electronic": ("Electrónica", [6, 3, 0, 3, 5]),
    "classical": ("Clásica", [3, 2, -1, 2, 4]),
}
SPEEDS = [0.75, 0.9, 1.0, 1.1, 1.25, 1.5, 2.0]


def build_audio_filter(eq: str, normalize: bool) -> str:
    """Cadena de filtros para mpv (`af`): ecualizador de 5 bandas + normalización de volumen."""
    parts = []
    gains = EQ_PRESETS.get(eq, EQ_PRESETS["flat"])[1]
    for freq, gain in zip(EQ_BANDS, gains):
        if gain:
            parts.append(f"equalizer=f={freq}:width_type=o:w=2:g={gain}")
    if normalize:
        parts.append("dynaudnorm=f=250:g=15")
    return "lavfi=[" + ",".join(parts) + "]" if parts else ""


class Player:
    """Envoltorio de libmpv (python-mpv). Solo audio."""

    def __init__(self, on_end=None, ao: str = ""):
        import mpv  # puede lanzar ImportError / OSError si falta libmpv

        kwargs = dict(video=False, ytdl=False, input_default_bindings=False, input_vo_keyboard=False,
                      osc=False, idle=True, cache=True, demuxer_max_bytes="64MiB")
        if ao:
            kwargs["ao"] = ao
        self._mpv = mpv.MPV(**kwargs)
        self.on_end = on_end or (lambda reason: None)
        self._lock = threading.Lock()

        @self._mpv.event_callback("end-file")
        def _end(event):
            try:
                reason = event.as_dict().get("reason", b"")
            except Exception:  # noqa: BLE001
                return
            if reason == b"eof":
                self.on_end("eof")
            elif reason == b"error":
                self.on_end("error")
            # 'stop' (al reemplazar la pista) se ignora a propósito

    # ---- control ----
    def play(self, source: str, headers: dict | None = None, start: float = 0.0) -> None:
        try:
            self._mpv["start"] = f"{start:.2f}" if start and start > 1 else "none"
        except Exception:  # noqa: BLE001
            pass
        ua = (headers or {}).get("User-Agent")
        if ua:
            try:
                self._mpv["user-agent"] = ua
            except Exception:  # noqa: BLE001
                pass
        self._mpv.pause = False
        self._mpv.play(source)

    def set_audio(self, eq: str = "flat", normalize: bool = False) -> None:
        self._mpv.af = build_audio_filter(eq, normalize)

    @property
    def audio_filter(self) -> str:
        try:
            return str(self._mpv.af)
        except Exception:  # noqa: BLE001
            return ""

    @property
    def speed(self) -> float:
        try:
            return float(self._mpv.speed)
        except Exception:  # noqa: BLE001
            return 1.0

    @speed.setter
    def speed(self, v: float) -> None:
        self._mpv.speed = max(0.25, min(4.0, float(v)))

    def stop(self) -> None:
        self._mpv.stop()

    def toggle_pause(self) -> bool:
        self._mpv.pause = not self._mpv.pause
        return bool(self._mpv.pause)

    def seek(self, seconds: float) -> None:
        try:
            self._mpv.seek(seconds, reference="relative")
        except Exception:  # noqa: BLE001
            pass

    def seek_to(self, seconds: float) -> None:
        try:
            self._mpv.seek(max(0, seconds), reference="absolute")
        except Exception:  # noqa: BLE001
            pass

    # ---- propiedades ----
    @property
    def position(self) -> float | None:
        try:
            return self._mpv.time_pos
        except Exception:  # noqa: BLE001
            return None

    @property
    def duration(self) -> float | None:
        try:
            return self._mpv.duration
        except Exception:  # noqa: BLE001
            return None

    @property
    def paused(self) -> bool:
        try:
            return bool(self._mpv.pause)
        except Exception:  # noqa: BLE001
            return False

    @property
    def volume(self) -> int:
        try:
            return int(self._mpv.volume)
        except Exception:  # noqa: BLE001
            return 0

    @volume.setter
    def volume(self, v: int) -> None:
        self._mpv.volume = max(0, min(130, int(v)))

    @property
    def muted(self) -> bool:
        try:
            return bool(self._mpv.mute)
        except Exception:  # noqa: BLE001
            return False

    def toggle_mute(self) -> bool:
        self._mpv.mute = not self._mpv.mute
        return bool(self._mpv.mute)

    def terminate(self) -> None:
        try:
            self._mpv.terminate()
        except Exception:  # noqa: BLE001
            pass
