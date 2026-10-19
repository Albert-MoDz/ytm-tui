from __future__ import annotations

import json
import os
import threading
from pathlib import Path

from .config import Config, atomic_write_json, config_dir
from .models import Track

AUDIO_EXTS = (".mp3", ".m4a", ".opus", ".ogg", ".flac", ".wav", ".aac")
HISTORY_MAX = 300


class Library:
    """Favoritos, playlists, historial e índice de descargas (todo en library.json)."""

    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.path = config_dir() / "library.json"
        self.lock = threading.RLock()
        self.favorites: list[Track] = []
        self.playlists: dict[str, list[Track]] = {}
        self.history: list[Track] = []
        self.downloads: dict[str, dict] = {}  # id -> {"track": {...}, "path": str}
        self.searches: list[str] = []
        self.autodl_playlists: list[str] = []
        self.subscriptions: list[Track] = []  # playlists/canales de YouTube que se sincronizan
        self._load()

    # ---------- persistencia ----------
    def _load(self) -> None:
        if not self.path.exists():
            return
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        self.favorites = [Track.from_dict(t) for t in data.get("favorites", [])]
        self.playlists = {n: [Track.from_dict(t) for t in ts] for n, ts in data.get("playlists", {}).items()}
        self.history = [Track.from_dict(t) for t in data.get("history", [])]
        self.downloads = data.get("downloads", {})
        self.searches = data.get("searches", [])
        self.autodl_playlists = data.get("autodl_playlists", [])
        self.subscriptions = [Track.from_dict(t) for t in data.get("subscriptions", [])]

    def save(self) -> None:
        with self.lock:
            data = {
                "favorites": [t.to_dict() for t in self.favorites],
                "playlists": {n: [t.to_dict() for t in ts] for n, ts in self.playlists.items()},
                "history": [t.to_dict() for t in self.history],
                "downloads": self.downloads,
                "searches": self.searches,
                "autodl_playlists": self.autodl_playlists,
                "subscriptions": [t.to_dict() for t in self.subscriptions],
            }
            try:
                atomic_write_json(self.path, data)
            except OSError:
                pass

    # ---------- favoritos ----------
    def is_favorite(self, track: Track) -> bool:
        return any(t.key == track.key for t in self.favorites)

    def toggle_favorite(self, track: Track) -> bool:
        with self.lock:
            if self.is_favorite(track):
                self.favorites = [t for t in self.favorites if t.key != track.key]
                added = False
            else:
                self.favorites.insert(0, track)
                added = True
            self.save()
            return added

    # ---------- playlists ----------
    def create_playlist(self, name: str) -> bool:
        name = name.strip()
        with self.lock:
            if not name or name in self.playlists:
                return False
            self.playlists[name] = []
            self.save()
            return True

    def delete_playlist(self, name: str) -> None:
        with self.lock:
            self.playlists.pop(name, None)
            self.save()

    def add_to_playlist(self, name: str, track: Track) -> bool:
        with self.lock:
            items = self.playlists.setdefault(name, [])
            if any(t.key == track.key for t in items):
                return False
            items.append(track)
            self.save()
            return True

    def remove_from_playlist(self, name: str, index: int) -> None:
        with self.lock:
            items = self.playlists.get(name, [])
            if 0 <= index < len(items):
                items.pop(index)
                self.save()

    def move_in_playlist(self, name: str, i: int, delta: int) -> int:
        with self.lock:
            items = self.playlists.get(name, [])
            j = max(0, min(len(items) - 1, i + delta))
            if items and j != i:
                items.insert(j, items.pop(i))
                self.save()
            return j if items else i

    def toggle_autodl(self, name: str) -> bool:
        with self.lock:
            if name in self.autodl_playlists:
                self.autodl_playlists.remove(name)
                on = False
            else:
                self.autodl_playlists.append(name)
                on = True
            self.save()
            return on

    # ---------- suscripciones ----------
    def toggle_subscription(self, item: Track) -> bool:
        with self.lock:
            if any(s.url == item.url for s in self.subscriptions):
                self.subscriptions = [s for s in self.subscriptions if s.url != item.url]
                on = False
            else:
                self.subscriptions.append(item)
                on = True
            self.save()
            return on

    def is_subscribed(self, url: str) -> bool:
        return any(s.url == url for s in self.subscriptions)

    # ---------- búsquedas recientes ----------
    def add_search(self, query: str) -> None:
        with self.lock:
            self.searches = [q for q in self.searches if q.lower() != query.lower()]
            self.searches.insert(0, query)
            del self.searches[50:]
            self.save()

    # ---------- historial ----------
    def add_history(self, track: Track) -> None:
        with self.lock:
            self.history = [t for t in self.history if t.key != track.key]
            self.history.insert(0, track)
            del self.history[HISTORY_MAX:]
            self.save()

    def remove_history(self, index: int) -> None:
        with self.lock:
            if 0 <= index < len(self.history):
                self.history.pop(index)
                self.save()

    # ---------- descargas ----------
    def record_download(self, track: Track, path: str) -> None:
        with self.lock:
            self.downloads[track.key] = {"track": track.to_dict(), "path": path}
            self.save()

    def local_path(self, track: Track) -> str:
        """Ruta del archivo local de esta canción (o '' si no está descargada)."""
        if track.path and os.path.isfile(track.path):
            return track.path
        entry = self.downloads.get(track.key)
        if entry and os.path.isfile(entry["path"]):
            return entry["path"]
        return ""

    def local_tracks(self) -> list[Track]:
        """Canciones descargadas (indexadas) + cualquier audio suelto en la carpeta de descargas."""
        tracks: list[Track] = []
        known: set[str] = set()
        with self.lock:
            for tid, entry in list(self.downloads.items()):
                if os.path.isfile(entry["path"]):
                    t = Track.from_dict(entry["track"])
                    t.path = entry["path"]
                    tracks.append(t)
                    known.add(os.path.abspath(entry["path"]))
        root = self.cfg.download_dir
        if root.is_dir():
            for p in root.rglob("*"):
                if p.suffix.lower() in AUDIO_EXTS and str(p.absolute()) not in known and p.is_file():
                    title = p.stem
                    artist = ""
                    if " - " in title:
                        artist, title = title.split(" - ", 1)
                    tracks.append(Track(id="", title=title, artist=artist, path=str(p)))
        tracks.sort(key=lambda t: os.path.getmtime(t.path) if t.path and os.path.exists(t.path) else 0, reverse=True)
        return tracks

    def downloaded_count(self) -> int:
        return sum(1 for e in self.downloads.values() if os.path.isfile(e["path"]))
