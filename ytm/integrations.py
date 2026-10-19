"""Integraciones opcionales: Last.fm, Discord, notificaciones de escritorio y teclas multimedia."""
from __future__ import annotations

import hashlib
import json
import logging
import os
import shutil
import subprocess
import sys
import threading
import time
import urllib.parse
import urllib.request

from .models import Track

log = logging.getLogger("ytm")


# --------------------------------------------------------------------------- Last.fm
class LastFM:
    def __init__(self, api_key: str, secret: str, session: str = ""):
        self.key, self.secret, self.session = api_key, secret, session
        self.url = os.environ.get("YTM_LASTFM_URL", "https://ws.audioscrobbler.com/2.0/")

    @property
    def ready(self) -> bool:
        return bool(self.key and self.secret and self.session)

    def _sign(self, params: dict) -> str:
        raw = "".join(f"{k}{params[k]}" for k in sorted(params) if k not in ("format", "callback"))
        return hashlib.md5((raw + self.secret).encode("utf-8")).hexdigest()  # noqa: S324 (lo exige Last.fm)

    def _call(self, params: dict) -> dict:
        params = {**params, "api_key": self.key}
        params["api_sig"] = self._sign(params)
        params["format"] = "json"
        data = urllib.parse.urlencode(params).encode()
        req = urllib.request.Request(self.url, data=data, headers={"User-Agent": "ytm-tui/1.0"})
        with urllib.request.urlopen(req, timeout=8) as r:  # noqa: S310
            res = json.loads(r.read().decode())
        if "error" in res:
            raise RuntimeError(res.get("message", f"Last.fm error {res['error']}"))
        return res

    def connect(self, user: str, password: str) -> str:
        """Obtiene (y devuelve) la clave de sesión. La contraseña no se guarda."""
        res = self._call({"method": "auth.getMobileSession", "username": user, "password": password})
        if "session" not in res:
            raise RuntimeError("Last.fm rechazó las credenciales")
        self.session = res["session"]["key"]
        return self.session

    def now_playing(self, artist: str, title: str, album: str = "", duration: float | None = None) -> None:
        if not self.ready:
            return
        p = {"method": "track.updateNowPlaying", "artist": artist, "track": title, "sk": self.session}
        if album:
            p["album"] = album
        if duration:
            p["duration"] = int(duration)
        self._call(p)

    def scrobble(self, artist: str, title: str, started_at: int, album: str = "", duration: float | None = None) -> None:
        if not self.ready:
            return
        p = {"method": "track.scrobble", "artist": artist, "track": title, "timestamp": int(started_at), "sk": self.session}
        if album:
            p["album"] = album
        if duration:
            p["duration"] = int(duration)
        self._call(p)


def should_scrobble(duration: float | None, played: float) -> bool:
    """Regla de Last.fm: pista de más de 30 s, escuchada la mitad o 4 minutos."""
    if not duration or duration <= 30:
        return False
    return played >= min(duration / 2, 240)


# --------------------------------------------------------------------------- Discord
class DiscordPresence:
    def __init__(self, client_id: str):
        self.client_id = client_id
        self._rpc = None
        self._failed = False

    def _connect(self) -> bool:
        if self._rpc or self._failed or not self.client_id:
            return self._rpc is not None
        try:
            from pypresence import Presence

            self._rpc = Presence(self.client_id)
            self._rpc.connect()
        except Exception:  # noqa: BLE001  (Discord cerrado, sin pypresence...)
            self._failed = True
            self._rpc = None
        return self._rpc is not None

    def update(self, track: Track, duration: float | None, position: float) -> None:
        if not self._connect():
            return
        try:
            now = time.time()
            kwargs = dict(details=track.title[:120], state=(track.artist or "YTM-TUI")[:120], large_text="YTM-TUI")
            if duration:
                kwargs.update(start=int(now - position), end=int(now - position + duration))
            self._rpc.update(**kwargs)
        except Exception:  # noqa: BLE001
            self._rpc, self._failed = None, True

    def clear(self) -> None:
        if self._rpc:
            try:
                self._rpc.clear()
            except Exception:  # noqa: BLE001
                pass


# --------------------------------------------------------------------------- notificaciones
def desktop_notify(title: str, body: str) -> bool:
    try:
        if sys.platform.startswith("linux") and shutil.which("notify-send"):
            subprocess.Popen(["notify-send", "-a", "YTM-TUI", "-t", "3000", title, body],
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return True
        if sys.platform == "darwin" and shutil.which("osascript"):
            script = f'display notification {json.dumps(body)} with title {json.dumps(title)}'
            subprocess.Popen(["osascript", "-e", script], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return True
        if shutil.which("termux-notification"):
            subprocess.Popen(["termux-notification", "--title", title, "--content", body, "--id", "ytm"],
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return True
    except Exception:  # noqa: BLE001
        pass
    return False


# --------------------------------------------------------------------------- teclas multimedia
class MediaKeys:
    """Teclas multimedia globales con pynput (Windows, macOS y Linux/X11; no funciona en Wayland)."""

    def __init__(self, handler):
        self.handler = handler  # handler(nombre_de_accion)
        self._listener = None

    def start(self) -> bool:
        try:
            from pynput import keyboard
        except Exception:  # noqa: BLE001  (sin pynput o sin entorno gráfico)
            return False
        mapping = {
            keyboard.Key.media_play_pause: "toggle_pause",
            keyboard.Key.media_next: "next_track",
            keyboard.Key.media_previous: "prev_track",
            keyboard.Key.media_volume_up: "volume(5)",
            keyboard.Key.media_volume_down: "volume(-5)",
            keyboard.Key.media_volume_mute: "mute",
        }

        def on_press(key):
            action = mapping.get(key)
            if action:
                try:
                    self.handler(action)
                except Exception:  # noqa: BLE001
                    log.exception("tecla multimedia")

        try:
            self._listener = keyboard.Listener(on_press=on_press)
            self._listener.daemon = True
            self._listener.start()
            return True
        except Exception:  # noqa: BLE001
            return False

    def stop(self) -> None:
        if self._listener:
            self._listener.stop()
