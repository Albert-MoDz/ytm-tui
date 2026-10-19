# YTM-TUI

Reproductor y descargador de música de YouTube para la terminal, estilo YouTube Music.
**mpv** (audio) + **Textual** (interfaz) + **yt-dlp** (búsqueda y descargas). Se maneja con ratón o con teclado.

## Instalación

```bash
# 1) Sistema: mpv/libmpv, ffmpeg y nodejs (o deno)
sudo apt install mpv libmpv2 ffmpeg nodejs        # Debian/Ubuntu
# brew install mpv ffmpeg node                    # macOS
# pkg install mpv ffmpeg nodejs python            # Termux

# 2) Python
pip install -r requirements.txt                   # o: pip install -e .   (crea el comando `ytm`)
pip install -r requirements-optional.txt          # teclas multimedia, Discord, tests (opcional)
```
Windows: `libmpv-2.dll` en el PATH o junto al proyecto, y ffmpeg.

```bash
python -m ytm                      # abrir
python -m ytm "bad bunny"          # abrir y buscar
python -m ytm --search "queen"     # solo imprimir resultados
python -m ytm --download <texto o enlace de playlist>   # descargar sin interfaz
```

## Con el ratón (todo es clicable)
| Dónde | Qué hace |
|---|---|
| Botones `\|◀ « ‖ » ▶\|` | anterior · −10 s · pausa/play · +10 s · siguiente |
| Barra de progreso | clic o **arrastrar** para saltar; rueda = ±5 s |
| Barra de volumen | clic o arrastrar; rueda = ±5; botones `♪−` `♪+` `✕` (silencio) |
| `⇄` `↻` `♥` | aleatorio · repetir (no/todo/una) · favorito de la canción que suena (se iluminan al activarse) |
| Fila de búsqueda | `Buscar`, `Canciones / Listas / Artistas`, `YT Music`, `Solo canciones`, `↺` recientes |
| Barra de herramientas | actúa sobre la fila seleccionada: Reproducir, Todo, Mezclar, Cola, Siguiente, Favorito, Bajar, Lista, Playlist, Radio, ▲ ▼, Quitar, Filtrar, `⋯` |
| Lista | **doble clic** = reproducir · **clic derecho** = menú contextual |
| Menú lateral | clic para cambiar de vista; `+ Nueva playlist` |
| Panel derecho | `Letra`, `▣` portada, `▂▅▇` visualizador, `≋` ecualizador, `☾` temporizador, `⇆` control remoto, `⚙` ajustes, `?`, `■` salir |

Pasa el ratón sobre un botón para ver su descripción y su atajo. Si tu terminal no muestra bien los iconos:
Ajustes → Iconos → `ascii`.

## Atajos de teclado
`?` dentro de la app muestra la lista completa. Los principales: `Espacio` pausa · `n/b` siguiente/anterior · `← →` ±5 s ·
`= -` volumen · `/` buscar · `a/A` cola · `f` favorito · `d/D` descargar · `p` playlist · `R` radio · `x` quitar ·
`Ctrl+↑/↓` mover · `Ctrl+F` filtrar · `y` letra · `i` portada · `v` visualizador · `e` ecualizador · `o` ajustes · `q` salir.

## Funciones
- **Búsqueda**: canciones, playlists/álbumes y artistas/canales; también pegando enlaces. Autocompleta con tu historial y con YouTube.
- **Reproducción**: cola reordenable, aleatorio, repetir, radio automática, precarga, **reanuda la cola y la posición al abrir**.
- **Audio**: ecualizador de 5 bandas (8 presets), velocidad 0.75×–2×, normalización de volumen.
- **Biblioteca**: favoritos, playlists, historial, suscripciones; importar/exportar M3U, CSV (Spotify/Exportify) y TXT.
- **Descargas**: MP3/M4A/Opus/FLAC en paralelo, con portada, **etiquetas correctas** (título/artista/álbum) y letra `.lrc` junto al archivo.
  Auto-descarga de favoritos o de playlists concretas; las suscripciones a playlists/canales bajan lo nuevo solas.
- **SponsorBlock**: salta (o corta al descargar) partes sin música, intros hablados y patrocinios.
- **Extras**: letra sincronizada, portada, visualizador de espectro, notificaciones de escritorio, teclas multimedia, Last.fm, Discord,
  control remoto web para el celular (`⇆`: abre la URL que muestra la app, en la misma wifi).

## Configuración
`~/.config/ytm-tui/config.json` (Windows: `%APPDATA%\ytm-tui`), o desde la app con `⚙`. Si YouTube pide "confirmar que no eres un bot",
pon tu `cookies.txt` en esa carpeta. Si falla algo al buscar o reproducir: `pip install -U yt-dlp`. Registro: `ytm.log`.

## Tests
`pytest` (28 pruebas: lógica, mpv real, descargas reales con ffmpeg, interfaz usada con el ratón, control remoto).
Usan un YouTube simulado, así que no necesitan internet.
