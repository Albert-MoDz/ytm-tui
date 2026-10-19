from __future__ import annotations

import json
import os
import sys
from pathlib import Path

DEFAULTS = {
    "download_dir": str(Path.home() / "Music" / "YTM"),
    "audio_format": "mp3",          # mp3 | m4a | opus | flac
    "audio_quality": "192",         # kbps (solo mp3)
    "embed_thumbnail": True,
    "concurrent_downloads": 2,
    "search_results": 25,
    "search_source": "youtube",     # youtube | music
    "songs_only": True,             # oculta videos largos (mixes, directos...)
    "max_duration": 900,            # segundos, usado si songs_only
    "autoplay_radio": True,         # al terminar la cola, seguir con canciones similares
    "lyrics": True,
    "volume": 80,
    "shuffle": False,
    "repeat": "off",                # off | all | one
    "ao": "",                       # salida de audio de mpv ('' = automática)
    "cookies_file": "",
    "icons": "unicode",             # unicode | ascii
    "eq": "flat",
    "speed": 1.0,
    "normalize": False,             # normalizar volumen entre canciones
    "save_lrc": True,               # guardar la letra (.lrc) junto a cada descarga
    "sponsorblock": False,          # saltar partes sin música al reproducir
    "sponsorblock_download": False, # cortarlas también al descargar
    "sponsorblock_categories": ["music_offtopic", "sponsor", "selfpromo", "intro", "outro"],
    "notifications": False,         # notificación de escritorio al cambiar de canción
    "media_keys": True,             # teclas multimedia (requiere pynput)
    "auto_download_favorites": False,
    "subscription_check_minutes": 30,
    "show_lyrics": False,
    "show_cover": False,
    "show_viz": False,
    "remote_enabled": False,        # control remoto web (para el celular)
    "remote_port": 8787,
    "remote_token": "",
    "lastfm_api_key": "",
    "lastfm_api_secret": "",
    "lastfm_user": "",
    "lastfm_session": "",
    "discord_client_id": "",
}


def config_dir() -> Path:
    override = os.environ.get("YTM_HOME")
    if override:
        base = Path(override)
    elif sys.platform == "win32":
        base = Path(os.environ.get("APPDATA", Path.home())) / "ytm-tui"
    else:
        base = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "ytm-tui"
    base.mkdir(parents=True, exist_ok=True)
    return base


def atomic_write_json(path: Path, data) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)


class Config(dict):
    def __init__(self):
        super().__init__(DEFAULTS)
        self.path = config_dir() / "config.json"
        if self.path.exists():
            try:
                loaded = json.loads(self.path.read_text(encoding="utf-8"))
                self.update({k: v for k, v in loaded.items() if k in DEFAULTS})
            except (OSError, ValueError):
                pass
        else:
            self.save()  # crea el archivo para que el usuario pueda editarlo
        Path(self["download_dir"]).expanduser().mkdir(parents=True, exist_ok=True)

    @property
    def download_dir(self) -> Path:
        return Path(self["download_dir"]).expanduser()

    def save(self) -> None:
        try:
            atomic_write_json(self.path, dict(self))
        except OSError:
            pass
