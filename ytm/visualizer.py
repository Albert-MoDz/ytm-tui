"""Visualizador de espectro. Decodifica el audio con ffmpeg (mono, 11 kHz) en segundo plano y calcula
las barras con una FFT en la posición actual de reproducción. Requiere numpy y ffmpeg (opcional)."""
from __future__ import annotations

import shutil
import subprocess
import threading

try:
    import numpy as np
except ImportError:  # pragma: no cover
    np = None

SR = 11025
WIN = 1024
MAX_SECONDS = 20 * 60


def available() -> bool:
    return np is not None and shutil.which("ffmpeg") is not None


class Spectrum:
    def __init__(self, bars: int = 32):
        self.bars = bars
        self._buf = bytearray()
        self._lock = threading.Lock()
        self._proc: subprocess.Popen | None = None
        self._gen = 0
        self._smooth = [0.0] * bars
        if np is not None:
            self._window = np.hanning(WIN).astype("float32")
            edges = np.geomspace(50, SR / 2 - 200, bars + 1)
            self._edges = (edges / (SR / WIN)).astype(int)

    def load(self, source: str, user_agent: str = "") -> None:
        self.stop()
        self._gen += 1
        gen = self._gen
        with self._lock:
            self._buf = bytearray()
        self._smooth = [0.0] * self.bars
        if not available():
            return
        threading.Thread(target=self._decode, args=(source, user_agent, gen), daemon=True).start()

    def _decode(self, source: str, ua: str, gen: int) -> None:
        cmd = ["ffmpeg", "-v", "quiet", "-nostdin"]
        if ua and source.startswith("http"):
            cmd += ["-user_agent", ua]
        cmd += ["-i", source, "-vn", "-ac", "1", "-ar", str(SR), "-t", str(MAX_SECONDS), "-f", "s16le", "-"]
        try:
            self._proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
            while gen == self._gen:
                chunk = self._proc.stdout.read(32768)
                if not chunk:
                    break
                with self._lock:
                    if gen != self._gen:
                        break
                    self._buf.extend(chunk)
        except Exception:  # noqa: BLE001
            pass

    def stop(self) -> None:
        self._gen += 1
        proc, self._proc = self._proc, None
        if proc and proc.poll() is None:
            try:
                proc.kill()
            except Exception:  # noqa: BLE001
                pass

    def levels(self, position: float | None) -> list[float]:
        """Altura (0..1) de cada barra en `position` segundos, con caída suave."""
        if np is None or position is None:
            return self._decay()
        start = int(position * SR) * 2
        with self._lock:
            chunk = bytes(self._buf[start:start + WIN * 2])
        if len(chunk) < WIN * 2:
            return self._decay()
        x = np.frombuffer(chunk, dtype=np.int16).astype("float32") / 32768.0
        mag = np.abs(np.fft.rfft(x * self._window)) / (WIN / 4)
        out = []
        for i in range(self.bars):
            lo, hi = self._edges[i], max(self._edges[i + 1], self._edges[i] + 1)
            m = float(mag[lo:hi].max()) if hi <= len(mag) else 0.0
            db = 20 * np.log10(m + 1e-6)
            out.append(float(min(max((db + 55) / 55, 0.0), 1.0)))
        self._smooth = [max(n, o * 0.82) for n, o in zip(out, self._smooth)]
        return list(self._smooth)

    def _decay(self) -> list[float]:
        self._smooth = [v * 0.8 for v in self._smooth]
        return list(self._smooth)


BLOCKS = " ▁▂▃▄▅▆▇█"


def render_rows(levels: list[float], height: int):
    """Devuelve `height` cadenas (arriba→abajo) dibujando las barras con bloques Unicode."""
    rows = []
    for r in range(height - 1, -1, -1):
        line = []
        for v in levels:
            filled = v * height - r
            line.append(BLOCKS[8] if filled >= 1 else BLOCKS[int(max(filled, 0) * 8)])
        rows.append("".join(c * 2 for c in line))
    return rows
