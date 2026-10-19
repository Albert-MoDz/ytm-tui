from __future__ import annotations

import bisect
import json
import re
import urllib.parse
import urllib.request
from dataclasses import dataclass, field

from .models import Track

_NOISE = re.compile(
    r"[\(\[][^\)\]]*(official|video|audio|lyrics?|letra|visuali[sz]er|hd|4k|remaster\w*|live|en vivo|clip)[^\)\]]*[\)\]]",
    re.I,
)
_LRC = re.compile(r"\[(\d+):(\d+(?:\.\d+)?)\]")


@dataclass
class Lyrics:
    synced: list[tuple[float, str]] = field(default_factory=list)
    plain: list[str] = field(default_factory=list)

    @property
    def times(self) -> list[float]:
        return [t for t, _ in self.synced]

    def index_at(self, position: float) -> int:
        return bisect.bisect_right(self.times, position + 0.15) - 1


def parse_lrc(text: str) -> list[tuple[float, str]]:
    out = []
    for line in text.splitlines():
        stamps = _LRC.findall(line)
        if not stamps:
            continue
        content = _LRC.sub("", line).strip()
        for m, s in stamps:
            out.append((int(m) * 60 + float(s), content))
    out.sort(key=lambda x: x[0])
    return out


def guess_names(track: Track) -> tuple[str, str]:
    """Devuelve (artista, título) limpios para buscar la letra."""
    title = _NOISE.sub("", track.title).strip()
    artist = re.sub(r"\s*-\s*Topic$", "", track.artist).strip()
    if " - " in title:
        left, right = title.split(" - ", 1)
        if not artist or artist.lower() in left.lower() or left.lower() in artist.lower():
            artist, title = left.strip(), right.strip()
        else:
            title = right.strip()
    title = re.sub(r"\s*(ft\.?|feat\.?)\s.*$", "", title, flags=re.I).strip()
    return artist, title


def _get(url: str):
    req = urllib.request.Request(url, headers={"User-Agent": "ytm-tui/1.0"})
    with urllib.request.urlopen(req, timeout=8) as r:  # noqa: S310
        return json.loads(r.read().decode("utf-8"))


def _from_item(item: dict) -> Lyrics | None:
    if item.get("syncedLyrics"):
        return Lyrics(synced=parse_lrc(item["syncedLyrics"]))
    if item.get("plainLyrics"):
        return Lyrics(plain=item["plainLyrics"].splitlines())
    return None


def fetch_lyrics(track: Track) -> Lyrics | None:
    """Busca la letra en lrclib.net (gratis, sin API key). Devuelve None si no hay."""
    artist, title = guess_names(track)
    if not title:
        return None
    try:
        params = {"track_name": title, "artist_name": artist}
        if track.duration:
            params["duration"] = str(int(track.duration))
        try:
            found = _from_item(_get("https://lrclib.net/api/get?" + urllib.parse.urlencode(params)))
            if found:
                return found
        except Exception:  # noqa: BLE001  (404 = no existe)
            pass
        results = _get("https://lrclib.net/api/search?" + urllib.parse.urlencode({"q": f"{artist} {title}".strip()}))
        results.sort(key=lambda r: not r.get("syncedLyrics"))
        for item in results[:5]:
            found = _from_item(item)
            if found:
                return found
    except Exception:  # noqa: BLE001
        return None
    return None
