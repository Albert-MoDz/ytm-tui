"""Importar / exportar playlists: M3U/M3U8, CSV (Spotify/Exportify), TXT (una canción o enlace por línea)."""
from __future__ import annotations

import csv
import os
import re
from pathlib import Path

from .models import Track, is_video_id

_YT_ID = re.compile(r"(?:v=|youtu\.be/|/shorts/|/embed/)([\w-]{11})")


def export_m3u(tracks: list[Track], path: str) -> int:
    lines = ["#EXTM3U"]
    n = 0
    for t in tracks:
        if not t.is_track:
            continue
        dur = int(t.duration) if t.duration else -1
        name = f"{t.artist} - {t.title}" if t.artist else t.title
        lines.append(f"#EXTINF:{dur},{name}")
        lines.append(t.path if t.path and os.path.isfile(t.path) else t.watch_url)
        n += 1
    Path(path).write_text("\n".join(lines) + "\n", encoding="utf-8")
    return n


def _entry_from_line(line: str, extinf: str = "") -> dict:
    title, artist = extinf, ""
    if " - " in extinf:
        artist, title = extinf.split(" - ", 1)
    entry = {"title": title.strip(), "artist": artist.strip(), "url": "", "path": ""}
    m = _YT_ID.search(line)
    if m:
        entry["url"] = f"https://www.youtube.com/watch?v={m.group(1)}"
        entry["id"] = m.group(1)
    elif os.path.isfile(line):
        entry["path"] = line
        if not entry["title"]:
            entry["title"] = Path(line).stem
    elif re.match(r"^https?://", line):
        entry["url"] = line
    else:
        entry["title"] = entry["title"] or line  # texto libre: se buscará
    return entry


def parse_file(path: str) -> list[dict]:
    p = Path(path).expanduser()
    text = p.read_text(encoding="utf-8-sig", errors="replace")
    ext = p.suffix.lower()
    entries: list[dict] = []

    if ext in (".m3u", ".m3u8"):
        extinf = ""
        for raw in text.splitlines():
            line = raw.strip()
            if not line:
                continue
            if line.startswith("#EXTINF"):
                extinf = line.split(",", 1)[1] if "," in line else ""
            elif not line.startswith("#"):
                entries.append(_entry_from_line(line, extinf))
                extinf = ""
    elif ext == ".csv":
        rows = list(csv.reader(text.splitlines()))
        if rows:
            header = [h.strip().lower() for h in rows[0]]
            def col(*names):
                for n in names:
                    for i, h in enumerate(header):
                        if h == n:
                            return i
                return None
            ti = col("track name", "track", "title", "name", "song", "canción", "cancion")
            ai = col("artist name(s)", "artist name", "artist", "artists", "artista")
            body = rows[1:] if ti is not None else rows
            ti = 0 if ti is None else ti
            ai = (1 if len(rows[0]) > 1 else None) if ai is None else ai
            for r in body:
                if len(r) > ti and r[ti].strip():
                    artist = r[ai].split(",")[0].split(";")[0].strip() if ai is not None and len(r) > ai else ""
                    entries.append({"title": r[ti].strip(), "artist": artist, "url": "", "path": ""})
    else:  # txt / cualquier otro: una entrada por línea
        for raw in text.splitlines():
            line = raw.strip()
            if line and not line.startswith("#"):
                entries.append(_entry_from_line(line))
    return entries


def resolve_entries(entries: list[dict], search_one, progress=None) -> tuple[list[Track], list[str]]:
    """Convierte entradas en Tracks. `search_one(texto) -> Track | None` busca en YouTube las que no traen enlace."""
    tracks, failed = [], []
    for i, e in enumerate(entries, 1):
        t = None
        if e.get("path"):
            t = Track(title=e["title"], artist=e.get("artist", ""), path=e["path"])
        elif e.get("id") and is_video_id(e["id"]):
            t = Track(id=e["id"], title=e.get("title") or e["id"], artist=e.get("artist", ""), url=e["url"])
        else:
            query = f'{e.get("artist", "")} {e.get("title", "")}'.strip()
            try:
                t = search_one(query) if query else None
            except Exception:  # noqa: BLE001
                t = None
        if t:
            tracks.append(t)
        else:
            failed.append(e.get("title") or e.get("url") or "?")
        if progress:
            progress(i, len(entries))
    return tracks, failed
