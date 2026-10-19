from __future__ import annotations

import json

from .config import atomic_write_json, config_dir
from .models import Track


def session_path():
    return config_dir() / "session.json"


def save_session(queue, position: float | None) -> None:
    data = {
        "queue": [t.to_dict() for t in queue.items],
        "index": queue.index,
        "position": float(position or 0.0),
    }
    try:
        atomic_write_json(session_path(), data)
    except OSError:
        pass


def load_session() -> tuple[list[Track], int, float]:
    try:
        data = json.loads(session_path().read_text(encoding="utf-8"))
        items = [Track.from_dict(t) for t in data.get("queue", [])]
        index = int(data.get("index", -1))
        if not 0 <= index < len(items):
            index = 0 if items else -1
        return items, index, float(data.get("position", 0.0))
    except (OSError, ValueError, TypeError):
        return [], -1, 0.0
