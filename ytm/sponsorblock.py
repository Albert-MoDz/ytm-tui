"""Segmentos de SponsorBlock (intros hablados, patrocinios, partes sin música...)."""
from __future__ import annotations

import hashlib
import json
import os
import urllib.parse
import urllib.request

API = os.environ.get("YTM_SPONSORBLOCK_URL", "https://sponsor.ajay.app")
DEFAULT_CATEGORIES = ["music_offtopic", "sponsor", "selfpromo", "intro", "outro"]


def fetch_segments(video_id: str, categories=None) -> list[tuple[float, float]]:
    """Devuelve [(inicio, fin), ...]. Usa el prefijo del hash (no revela qué video escuchas)."""
    cats = categories or DEFAULT_CATEGORIES
    prefix = hashlib.sha256(video_id.encode()).hexdigest()[:4]
    cats_json = urllib.parse.quote(json.dumps(cats, separators=(",", ":")))
    url = f"{API}/api/skipSegments/{prefix}?categories={cats_json}"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "ytm-tui/1.0"})
        with urllib.request.urlopen(req, timeout=6) as r:  # noqa: S310
            data = json.loads(r.read().decode())
    except Exception:  # noqa: BLE001  (404 = sin segmentos)
        return []
    out = []
    for video in data:
        if video.get("videoID") == video_id:
            for seg in video.get("segments", []):
                s, e = seg["segment"]
                if e - s > 1:
                    out.append((float(s), float(e)))
    return sorted(out)


def skip_target(segments: list[tuple[float, float]], pos: float) -> float | None:
    """Si `pos` cae dentro de un segmento, devuelve el instante al que saltar."""
    for s, e in segments:
        if s <= pos < e - 0.3:
            return e
    return None
