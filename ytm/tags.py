"""Etiquetas ID3/MP4/Vorbis correctas (título, artista, álbum) y lectura de portadas con mutagen."""
from __future__ import annotations

import os

from .lyrics import guess_names
from .models import Track


def clean_names(track: Track) -> tuple[str, str]:
    artist, title = guess_names(track)
    return (artist or track.artist or "").strip(), (title or track.title or "").strip()


def write_tags(path: str, track: Track) -> bool:
    """Escribe título/artista/álbum. Devuelve False si mutagen no está o el formato no se soporta."""
    try:
        import mutagen
    except ImportError:
        return False
    artist, title = clean_names(track)
    album = track.album or ""
    ext = os.path.splitext(path)[1].lower()
    try:
        if ext == ".mp3":
            from mutagen.easyid3 import EasyID3
            from mutagen.id3 import ID3NoHeaderError

            try:
                tags = EasyID3(path)
            except ID3NoHeaderError:
                tags = mutagen.File(path, easy=True)
                tags.add_tags()
            tags["title"], tags["artist"] = title, artist
            if album:
                tags["album"] = album
            tags.save()
        elif ext in (".m4a", ".mp4"):
            from mutagen.mp4 import MP4

            f = MP4(path)
            f["\xa9nam"], f["\xa9ART"] = [title], [artist]
            if album:
                f["\xa9alb"] = [album]
            f.save()
        elif ext in (".opus", ".ogg", ".flac"):
            f = mutagen.File(path)
            if f is None:
                return False
            f["title"], f["artist"] = [title], [artist]
            if album:
                f["album"] = [album]
            f.save()
        else:
            return False
        return True
    except Exception:  # noqa: BLE001
        return False


def read_cover(path: str) -> bytes | None:
    """Portada incrustada (mp3/m4a/flac) o None."""
    try:
        import mutagen
        from mutagen.flac import FLAC
        from mutagen.mp3 import MP3
        from mutagen.mp4 import MP4

        ext = os.path.splitext(path)[1].lower()
        if ext == ".mp3":
            for key, frame in MP3(path).tags.items():
                if key.startswith("APIC"):
                    return frame.data
        elif ext in (".m4a", ".mp4"):
            covr = MP4(path).tags.get("covr")
            if covr:
                return bytes(covr[0])
        elif ext == ".flac":
            pics = FLAC(path).pictures
            if pics:
                return pics[0].data
        _ = mutagen
    except Exception:  # noqa: BLE001
        pass
    return None
