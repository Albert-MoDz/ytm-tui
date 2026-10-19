from __future__ import annotations

import re
from dataclasses import dataclass, asdict, field


@dataclass
class Track:
    id: str = ""
    title: str = ""
    artist: str = ""
    duration: float | None = None
    url: str = ""
    path: str = ""  # ruta local si es un archivo descargado
    album: str = ""
    kind: str = "track"  # track | playlist | channel

    @property
    def key(self) -> str:
        return self.id or self.path or self.url

    @property
    def is_track(self) -> bool:
        return self.kind == "track"

    @property
    def watch_url(self) -> str:
        return self.url or (f"https://www.youtube.com/watch?v={self.id}" if self.id else "")

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "Track":
        fields = cls.__dataclass_fields__
        return cls(**{k: v for k, v in d.items() if k in fields})


def fmt_time(seconds: float | None) -> str:
    if seconds is None:
        return "--:--"
    seconds = max(0, int(seconds))
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"


def truncate(text: str, width: int) -> str:
    return text if len(text) <= width else text[: max(width - 1, 1)] + "…"


_VIDEO_ID = re.compile(r"^[\w-]{11}$")


def is_video_id(value: str) -> bool:
    return bool(value and _VIDEO_ID.match(value))
