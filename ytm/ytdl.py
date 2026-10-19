from __future__ import annotations

import itertools
import json
import os
import queue
import re
import shutil
import sys
import threading
import time
import urllib.request
from urllib.parse import quote_plus

import yt_dlp

from .config import Config
from .library import Library
from . import tags
from .lyrics import fetch_lyrics
from .models import Track, is_video_id

HAS_FFMPEG = shutil.which("ffmpeg") is not None
_DENO, _NODE = shutil.which("deno"), shutil.which("node")
JS_RUNTIMES = {"deno": {"path": _DENO}} if _DENO else ({"node": {"path": _NODE}} if _NODE else None)
ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")

ERROR_HINTS = [
    ("sign in to confirm", 'YouTube pide verificar que no eres un bot. Pon tu "cookies.txt" en la carpeta de config.'),
    ("confirm your age", "Video con restricción de edad (necesita cookies.txt)."),
    ("private video", "Video privado"),
    ("video unavailable", "Video no disponible"),
    ("not available in your country", "No disponible en tu país"),
    ("members-only", "Solo para miembros"),
    ("requested format is not available", "Formato no disponible. Actualiza yt-dlp: pip install -U yt-dlp"),
    ("http error 403", "YouTube rechazó la petición (403). Actualiza yt-dlp: pip install -U yt-dlp"),
    ("http error 429", "Demasiadas solicitudes. Espera unos minutos."),
    ("ffmpeg", "ffmpeg no está instalado o no se encontró."),
    ("http error 404", "Recurso no encontrado (404)."),
    ("unable to download webpage", "Sin conexión con YouTube."),
    ("name resolution", "Sin conexión a internet."),
    ("getaddrinfo", "Sin conexión a internet."),
    ("failed to resolve", "Sin conexión a internet."),
    ("name or service not known", "Sin conexión a internet."),
    ("unable to download api page", "No se pudo conectar con YouTube. Revisa tu conexión."),
]


def friendly_error(exc: BaseException) -> str:
    msg = ANSI_RE.sub("", str(exc)).strip()
    low = msg.lower()
    for needle, text in ERROR_HINTS:
        if needle in low:
            return text
    return re.sub(r"^ERROR:\s*", "", msg)[:200] or "Error desconocido"


_BAD_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def safe_filename(name: str, limit: int = 150) -> str:
    """Nombre de archivo válido en Linux/macOS/Windows, conservando acentos y espacios."""
    name = _BAD_CHARS.sub("-", name)
    name = re.sub(r"\s+", " ", name).strip(" .-")
    return name[:limit].rstrip(" .-")


class _QuietLogger:
    def debug(self, msg): pass
    def info(self, msg): pass
    def warning(self, msg): pass
    def error(self, msg): pass


def base_opts(cfg: Config) -> dict:
    opts = {
        "quiet": True,
        "no_warnings": True,
        "noprogress": True,
        "logger": _QuietLogger(),
        "socket_timeout": 20,
        "retries": 3,
        "noplaylist": True,
    }
    if JS_RUNTIMES:
        opts["js_runtimes"] = JS_RUNTIMES
        opts["remote_components"] = ["ejs:github"]
    cookies = cfg.get("cookies_file") or str(cfg.path.parent / "cookies.txt")
    if os.path.isfile(cookies):
        opts["cookiefile"] = cookies
    return opts


# ---------------------------------------------------------------------------
# Búsqueda
# ---------------------------------------------------------------------------
def is_url(text: str) -> bool:
    return bool(re.match(r"^(https?://)?([\w-]+\.)?(youtube\.com|youtu\.be)/", text.strip(), re.I))


def entry_to_track(e: dict | None) -> Track | None:
    if not e:
        return None
    vid = e.get("id") or ""
    if not is_video_id(vid):
        return None
    artist = ", ".join(e["artists"]) if e.get("artists") else (e.get("artist") or e.get("uploader") or e.get("channel") or "")
    artist = re.sub(r"\s*-\s*Topic$", "", artist)
    return Track(
        id=vid,
        title=e.get("track") or e.get("title") or "(sin título)",
        artist=artist,
        duration=e.get("duration"),
        url=f"https://www.youtube.com/watch?v={vid}",
        album=e.get("album") or "",
    )


def entry_to_collection(e: dict | None) -> Track | None:
    """Playlist/álbum o canal/artista devuelto por una búsqueda."""
    if not e:
        return None
    url = e.get("url") or e.get("webpage_url") or ""
    title = e.get("title") or e.get("channel") or ""
    if not url or not title:
        return None
    if "list=" in url or "/playlist" in url:
        kind, who = "playlist", e.get("uploader") or e.get("channel") or "Playlist"
        n = e.get("playlist_count")
        if n:
            who += f" · {n} canciones"
    elif re.search(r"/(channel|c|user)/|/@", url):
        kind, who = "channel", "Artista / canal"
    else:
        return None
    return Track(title=title, artist=who, url=url, kind=kind)


SEARCH_FILTERS = {"playlists": "EgIQAw%3D%3D", "artists": "EgIQAg%3D%3D"}


def search_collections(query: str, kind: str, cfg: Config) -> list[Track]:
    """Busca playlists/álbumes (kind='playlists') o artistas/canales (kind='artists')."""
    n = int(cfg["search_results"])
    url = f"https://www.youtube.com/results?search_query={quote_plus(query)}&sp={SEARCH_FILTERS[kind]}"
    out, seen = [], set()
    for e in _extract_flat(url, cfg, limit=n * 2):
        t = entry_to_collection(e)
        if t and t.url not in seen:
            seen.add(t.url)
            out.append(t)
        if len(out) >= n:
            break
    return out


def open_collection(item: Track, cfg: Config) -> list[Track]:
    """Canciones de una playlist/álbum o de un canal/artista."""
    url = item.url
    if item.kind == "channel" and not re.search(r"/(videos|releases|playlists|streams)$", url):
        url = url.rstrip("/") + "/videos"
    return _to_tracks(_extract_flat(url, cfg, limit=200, playlist=True), 200)


_suggest_cache: dict[str, list[str]] = {}


def suggest(query: str, timeout: float = 3.0) -> list[str]:
    """Sugerencias de autocompletado de YouTube (sin API key)."""
    q = query.strip().lower()
    if q in _suggest_cache:
        return _suggest_cache[q]
    base = os.environ.get("YTM_SUGGEST_URL", "https://suggestqueries.google.com/complete/search")
    try:
        req = urllib.request.Request(f"{base}?client=firefox&ds=yt&q={quote_plus(query)}",
                                     headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=timeout) as r:  # noqa: S310
            data = json.loads(r.read().decode("utf-8", "replace"))
        out = [s for s in data[1] if isinstance(s, str)][:8]
    except Exception:  # noqa: BLE001
        out = []
    _suggest_cache[q] = out
    return out


def search_one(query: str, cfg: Config) -> Track | None:
    entries = _extract_flat(f"ytsearch1:{query}", cfg)
    tracks = _to_tracks(entries, 1)
    return tracks[0] if tracks else None


def _extract_flat(url: str, cfg: Config, limit: int | None = None, playlist: bool = False) -> list[dict]:
    opts = base_opts(cfg)
    opts.update(extract_flat="in_playlist", skip_download=True, noplaylist=not playlist)
    if limit:
        opts["playlistend"] = limit
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=False)
    if not info:
        return []
    if info.get("entries") is not None:
        return [e for e in info["entries"] if e]
    return [info]


def _to_tracks(entries, limit: int, max_duration: float | None = None) -> list[Track]:
    out, seen = [], set()
    for e in entries:
        t = entry_to_track(e)
        if not t or t.id in seen:
            continue
        if max_duration and (t.duration is None or t.duration > max_duration):
            continue
        seen.add(t.id)
        out.append(t)
        if len(out) >= limit:
            break
    return out


def search_tracks(query: str, cfg: Config) -> list[Track]:
    n = int(cfg["search_results"])
    max_dur = cfg["max_duration"] if cfg["songs_only"] else None
    entries: list[dict] = []
    if cfg["search_source"] == "music":
        try:
            entries = _extract_flat(f"https://music.youtube.com/search?q={quote_plus(query)}#songs", cfg, limit=n * 2)
        except Exception:
            entries = []  # cae a la búsqueda normal de YouTube
    tracks = _to_tracks(entries, n, max_dur)
    if not tracks:
        entries = _extract_flat(f"ytsearch{n * 2 if max_dur else n}:{query}", cfg)
        tracks = _to_tracks(entries, n, max_dur)
    return tracks


def tracks_from_url(url: str, cfg: Config) -> list[Track]:
    if not re.match(r"^https?://", url, re.I):
        url = "https://" + url
    return _to_tracks(_extract_flat(url, cfg, limit=200, playlist=True), 200)


def radio(track: Track, cfg: Config, n: int = 25) -> list[Track]:
    """Canciones similares (el 'mix' automático de YouTube para esa canción)."""
    if not track.id:
        return []
    url = f"https://www.youtube.com/watch?v={track.id}&list=RD{track.id}"
    entries = _extract_flat(url, cfg, limit=n + 1, playlist=True)
    return [t for t in _to_tracks(entries, n + 1) if t.id != track.id][:n]


# ---------------------------------------------------------------------------
# Resolución de stream (para mpv)
# ---------------------------------------------------------------------------
_stream_cache: dict[str, tuple[float, dict]] = {}
_cache_lock = threading.Lock()
STREAM_TTL = 4 * 3600


def resolve_stream(track: Track, cfg: Config) -> dict:
    with _cache_lock:
        hit = _stream_cache.get(track.id)
        if hit and time.time() - hit[0] < STREAM_TTL:
            return hit[1]
    opts = base_opts(cfg)
    opts["format"] = "bestaudio/best"
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(track.watch_url, download=False)
    url = info.get("url") or (info.get("requested_formats") or [{}])[0].get("url")
    if not url:
        raise RuntimeError("No se encontró un stream de audio")
    result = {"url": url, "headers": info.get("http_headers") or {}, "duration": info.get("duration")}
    with _cache_lock:
        _stream_cache[track.id] = (time.time(), result)
    return result


# ---------------------------------------------------------------------------
# Descargas
# ---------------------------------------------------------------------------
class _Cancelled(Exception):
    pass


ACTIVE = ("queued", "downloading", "converting")


class Job:
    _ids = itertools.count(1)

    def __init__(self, track: Track):
        self.id = next(Job._ids)
        self.track = track
        self.status = "queued"  # queued, downloading, converting, done, error, cancelled
        self.progress = 0.0
        self.message = ""
        self.path = ""
        self.cancelled = False


class DownloadManager:
    def __init__(self, cfg: Config, library: Library, on_change=None):
        self.cfg, self.library = cfg, library
        self.on_change = on_change or (lambda job: None)
        self.jobs: list[Job] = []
        self._q: queue.Queue[Job] = queue.Queue()
        self._lock = threading.Lock()
        for _ in range(max(1, int(cfg["concurrent_downloads"]))):
            threading.Thread(target=self._worker, daemon=True).start()

    def add(self, track: Track) -> tuple[Job | None, str]:
        if not track.id:
            return None, "Esta pista ya es un archivo local"
        if self.library.local_path(track):
            return None, "Ya está descargada"
        with self._lock:
            if any(j.track.key == track.key and j.status in ACTIVE for j in self.jobs):
                return None, "Ya está en la cola de descargas"
            job = Job(track)
            self.jobs.append(job)
        self._q.put(job)
        self.on_change(job)
        return job, "Añadida a descargas"

    def cancel(self, job: Job) -> None:
        if job.status in ACTIVE:
            job.cancelled = True
            if job.status == "queued":
                job.status, job.message = "cancelled", "Cancelada"
                self.on_change(job)

    def clear_finished(self) -> None:
        with self._lock:
            self.jobs = [j for j in self.jobs if j.status in ACTIVE]

    def active_count(self) -> int:
        return sum(1 for j in self.jobs if j.status in ACTIVE)

    # ---- internals ----
    def _worker(self) -> None:
        while True:
            job = self._q.get()
            try:
                if job.cancelled:
                    job.status, job.message = "cancelled", "Cancelada"
                else:
                    self._run(job)
            except _Cancelled:
                job.status, job.message = "cancelled", "Cancelada"
            except Exception as e:  # noqa: BLE001
                job.status, job.message = "error", friendly_error(e)
            finally:
                self.on_change(job)
                self._q.task_done()

    def _opts(self, job: Job) -> dict:
        t, cfg = job.track, self.cfg
        name = safe_filename(f"{t.artist} - {t.title}" if t.artist else t.title) or t.id
        opts = base_opts(cfg)
        opts["outtmpl"] = str(cfg.download_dir / (name.replace("%", "%%") + ".%(ext)s"))
        opts["format"] = "bestaudio/best"
        opts["windowsfilenames"] = sys.platform == "win32"

        def progress(d):
            if job.cancelled:
                raise _Cancelled()
            if d.get("status") == "downloading":
                total = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
                if total:
                    job.progress = min(d.get("downloaded_bytes", 0) / total * 100, 99)
                job.status, job.message = "downloading", ""

        def post(d):
            if d.get("status") == "started":
                job.status, job.message = "converting", "Convirtiendo..."

        opts["progress_hooks"] = [progress]
        opts["postprocessor_hooks"] = [post]
        if HAS_FFMPEG:
            fmt = cfg["audio_format"]
            pps = [{"key": "FFmpegExtractAudio", "preferredcodec": fmt,
                    "preferredquality": str(cfg["audio_quality"]) if fmt == "mp3" else None}]
            if cfg["sponsorblock_download"] and "youtube" in job.track.watch_url:
                cats = list(cfg["sponsorblock_categories"])
                pps.insert(0, {"key": "SponsorBlock", "categories": cats, "when": "after_filter"})
                pps.insert(2, {"key": "ModifyChapters", "remove_sponsor_segments": cats,
                               "remove_chapters_patterns": [], "remove_ranges": [], "force_keyframes": False})
            pps.append({"key": "FFmpegMetadata", "add_metadata": True})
            if cfg["embed_thumbnail"] and fmt in ("mp3", "m4a"):
                opts["writethumbnail"] = True
                pps.insert(0, {"key": "FFmpegThumbnailsConvertor", "format": "jpg", "when": "before_dl"})
                pps.append({"key": "EmbedThumbnail"})
            opts["postprocessors"] = pps
        else:
            opts["format"] = "bestaudio[ext=m4a]/bestaudio"
        return opts

    def _run(self, job: Job) -> None:
        job.status = "downloading"
        self.on_change(job)
        self.cfg.download_dir.mkdir(parents=True, exist_ok=True)
        with yt_dlp.YoutubeDL(self._opts(job)) as ydl:
            info = ydl.extract_info(job.track.watch_url, download=True)
        if job.cancelled:
            raise _Cancelled()
        path = ""
        for rd in (info or {}).get("requested_downloads") or []:
            path = rd.get("filepath") or path
        if not path or not os.path.isfile(path):
            raise RuntimeError("La descarga terminó pero no se encontró el archivo")
        self._finish_files(job, path)
        job.path, job.progress, job.status, job.message = path, 100, "done", "Completada"
        self.library.record_download(job.track, path)

    def _finish_files(self, job: Job, path: str) -> None:
        """Etiquetas correctas y letra (.lrc) junto al archivo. Los fallos aquí nunca invalidan la descarga."""
        try:
            tags.write_tags(path, job.track)
        except Exception:  # noqa: BLE001
            pass
        if self.cfg["save_lrc"]:
            try:
                found = fetch_lyrics(job.track)
                if found and found.synced:
                    lines = [f"[{int(t // 60):02d}:{t % 60:05.2f}]{txt}" for t, txt in found.synced]
                    artist, title = tags.clean_names(job.track)
                    header = [f"[ar:{artist}]", f"[ti:{title}]"]
                    with open(os.path.splitext(path)[0] + ".lrc", "w", encoding="utf-8") as f:
                        f.write("\n".join(header + lines) + "\n")
            except Exception:  # noqa: BLE001
                pass
