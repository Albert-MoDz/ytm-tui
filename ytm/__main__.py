from __future__ import annotations

import argparse
import logging
import sys
import time

from . import __version__


def _setup_logging() -> None:
    from .config import config_dir

    logging.basicConfig(
        filename=str(config_dir() / "ytm.log"), level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )


MPV_HELP = """\
No se pudo cargar libmpv ({err}).

Instálalo según tu sistema y vuelve a intentarlo:
  Debian/Ubuntu : sudo apt install mpv libmpv2 ffmpeg nodejs
  Fedora        : sudo dnf install mpv mpv-libs ffmpeg nodejs
  Arch          : sudo pacman -S mpv ffmpeg nodejs
  macOS         : brew install mpv ffmpeg node
  Termux        : pkg install mpv ffmpeg nodejs python
  Windows       : descarga libmpv (mpv-2.dll / libmpv-2.dll) y ponlo junto a este
                  proyecto o en el PATH; instala ffmpeg (winget install ffmpeg).
"""


def _cmd_search(query: str) -> int:
    from . import ytdl
    from .config import Config
    from .models import fmt_time

    cfg = Config()
    try:
        tracks = ytdl.tracks_from_url(query, cfg) if ytdl.is_url(query) else ytdl.search_tracks(query, cfg)
    except Exception as e:  # noqa: BLE001
        print(f"Búsqueda fallida: {ytdl.friendly_error(e)}", file=sys.stderr)
        return 1
    for i, t in enumerate(tracks, 1):
        print(f"{i:2d}. {t.title} — {t.artist}  [{fmt_time(t.duration)}]  {t.watch_url}")
    return 0 if tracks else 1


def _cmd_download(query: str) -> int:
    from . import ytdl
    from .config import Config
    from .library import Library

    cfg = Config()
    lib = Library(cfg)
    try:
        tracks = ytdl.tracks_from_url(query, cfg) if ytdl.is_url(query) else ytdl.search_tracks(query, cfg)[:1]
    except Exception as e:  # noqa: BLE001
        print(f"Búsqueda fallida: {ytdl.friendly_error(e)}", file=sys.stderr)
        return 1
    if not tracks:
        print("Sin resultados", file=sys.stderr)
        return 1
    if not ytdl.HAS_FFMPEG:
        print("Aviso: ffmpeg no encontrado; se guardará el audio original sin convertir.", file=sys.stderr)
    dm = ytdl.DownloadManager(cfg, lib)
    jobs = [j for t in tracks if (j := dm.add(t)[0])]
    if not jobs:
        print("Todo lo pedido ya estaba descargado.")
        return 0
    while any(j.status in ytdl.ACTIVE for j in jobs):
        done = sum(j.status not in ytdl.ACTIVE for j in jobs)
        cur = next((j for j in jobs if j.status in ("downloading", "converting")), None)
        if cur:
            print(f"\r[{done}/{len(jobs)}] {cur.track.title[:50]:50s} {cur.progress:5.1f}% {cur.message:15s}", end="", flush=True)
        time.sleep(0.3)
    print()
    ok = 0
    for j in jobs:
        if j.status == "done":
            ok += 1
            print(f"✔ {j.path}")
        else:
            print(f"✘ {j.track.title}: {j.message}", file=sys.stderr)
    return 0 if ok == len(jobs) else 2


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="ytm", description="Reproductor y descargador de música de YouTube para la terminal.")
    p.add_argument("query", nargs="*", help="Búsqueda inicial o enlace de YouTube")
    p.add_argument("--search", metavar="TEXTO", help="Solo buscar e imprimir resultados (sin interfaz)")
    p.add_argument("--download", metavar="TEXTO_O_URL", help="Descargar sin interfaz (primer resultado, o toda la playlist si es un enlace)")
    p.add_argument("--version", action="version", version=f"ytm-tui {__version__}")
    args = p.parse_args(argv)

    _setup_logging()
    if args.search:
        return _cmd_search(args.search)
    if args.download:
        return _cmd_download(args.download)

    from .config import Config
    from .library import Library
    from .player import Player

    cfg = Config()
    try:
        player = Player(ao=cfg["ao"])
    except (ImportError, OSError) as e:
        print(MPV_HELP.format(err=e), file=sys.stderr)
        return 1

    from .ui import YTMApp

    YTMApp(cfg, Library(cfg), player, query=" ".join(args.query) or None).run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
