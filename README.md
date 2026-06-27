# DJI Air 3S — SRT Flight Log Viewer

A desktop application for visualising flight telemetry from DJI Air 3S `.SRT` subtitle files. Load a flight log to see the GPS track overlaid on a satellite map, with altitude and speed charts, a full flight summary, and one-click export to CSV, KML, or a HUD-overlaid video.

---

## Features

- **Satellite map** — GPS track coloured by altitude, plotted over live Esri World Imagery satellite tiles
- **Altitude chart** — time-series plot, switchable between relative (above home point) and absolute (above sea level); overlays a **HAG (Height Above Ground)** line fetched from the Open-Meteo terrain API
- **Speed chart** — time-series plot in km/h, with guards against DJI timestamp glitches and frozen-GPS artefacts
- **Flight summary** — date/time, duration, distance, max and average speed, max altitude, home point coordinates
- **Export CSV** — all telemetry fields at 1 sample/second (frame, timestamp, lat/lon, altitude, speed, ISO, shutter, f-number, focal length, colour temperature)
- **Export KML** — GPS track and home point marker for use in any GIS tool
- **View in Google Maps** — opens a local map in your browser showing the full flight path and markers
- **View in Google Earth** — exports a styled KML and opens it directly in Google Earth
- **Export HUD Video** — burns a fixed-width telemetry overlay (altitude, speed, heading, HAG) directly onto the MP4 using Apple VideoToolbox hardware encoding

---

## Requirements

- macOS (Apple Silicon or Intel)
- Python 3.9+
- [ffmpeg](https://ffmpeg.org/) — required for HUD video export (`brew install ffmpeg`)
- Dependencies listed in `.venv` (see [Installation](#installation))

---

## Installation

```bash
# Clone the repo
git clone https://github.com/mpinnuck/srt_viewer.git
cd srt_viewer

# Create and activate virtual environment
python3 -m venv .venv
source .venv/bin/activate

# Install dependencies
pip install matplotlib numpy mercantile requests pillow
```

---

## Running from source

```bash
.venv/bin/python srt_viewer.py
```

---

## Building a distributable app

```bash
.venv/bin/pip install pyinstaller
.venv/bin/pyinstaller --clean --noconfirm "DJI SRT Viewer.spec"
```

The app bundle is written to `dist/DJI SRT Viewer/DJI SRT Viewer.app`.

**Install to Applications:**

```bash
cp -r "dist/DJI SRT Viewer/DJI SRT Viewer.app" /Applications/
```

---

## User Guide

### Opening a flight log

1. Click **📂 Open SRT** in the toolbar.
2. Navigate to the `.SRT` file saved alongside your DJI video and select it.
3. A progress bar appears while the file is parsed (large files take a few seconds). When complete, the map, charts, and summary update automatically.

> The app remembers the last directory you opened so you can find your files quickly next time.

### Reading the map

The **FLIGHT PATH** panel shows the GPS track drawn as a coloured line over a satellite background:

| Element | Meaning |
|---|---|
| Green circle | Home / takeoff point |
| Red ✕ | Landing point |
| Line colour | Altitude — purple (low) → yellow (high), scale shown in the colourbar |

### Reading the charts

- **ALTITUDE** — shows relative altitude (metres above the home point) by default. Switch to absolute altitude (metres above sea level) using the **Relative / Absolute** toggle in the toolbar. A dashed **HAG** line (Height Above Ground) is overlaid on the same scale once terrain data has been fetched from the Open-Meteo API (usually within a second of loading). Both lines share the same left-axis scale so the gap between them directly shows terrain rise below the drone.
- **SPEED** — ground speed in km/h derived from consecutive GPS positions. Samples where the GPS position has not changed are skipped to avoid frozen-coordinate artefacts. Timestamp glitches in the SRT file (a DJI firmware quirk where the clock resets mid-recording) are detected and suppressed; affected frames show as a brief zero rather than an impossible speed spike.

Both charts share the same time axis (minutes from takeoff).

### HAG (Height Above Ground)

HAG is computed datum-independently using only relative values:

```
HAG = rel_alt + (terrain_elevation_at_home − terrain_elevation_at_current_position)
```

`rel_alt` is a pure barometric difference, and the terrain term is a relative difference, so any offset between the drone's barometric reference and the Open-Meteo MSL datum cancels out. For a constant-HAG waypoint mission the HAG line is flat regardless of the terrain profile below it.

### Flight summary

The **FLIGHT SUMMARY** panel shows:

| Field | Description |
|---|---|
| File | Source filename |
| Date/Time | UTC timestamp of the first frame |
| Duration | Total flight time |
| Total frames | Raw frame count in the SRT file |
| Sampled | Number of 1-per-second data points used |
| Distance | Total ground distance (km) |
| Max altitude | Peak relative altitude (m) |
| Max / Avg speed | Speed statistics (km/h) |
| Home point | Takeoff latitude and longitude |

### Exporting data

| Button | Output |
|---|---|
| **💾 Export CSV** | Saves a `.csv` with every telemetry field at 1 sample/second |
| **🌍 Export KML** | Saves a `.kml` with the flight path and home marker (compatible with Google Earth, QGIS, etc.) |

Both export dialogs let you choose the save location and filename.

### Viewing in Google Maps / Google Earth

| Button | What happens |
|---|---|
| **🗺️ Google Maps** | Opens a local HTML page in your browser showing the flight path on Google Satellite imagery, with a green home marker and red landing marker |
| **🌐 Google Earth** | Writes a temporary KML file and opens it in Google Earth (desktop app must be installed). The flight path is shown as an orange line clamped to the terrain |

> Both buttons require a loaded SRT file. If no file is loaded, a reminder dialog is shown.

---

## Export HUD Video

Clicking **🎬 Export HUD Video** opens a dialog that burns a telemetry overlay directly onto the original MP4 file, producing a new video with the HUD permanently embedded.

### Workflow

1. Load an `.SRT` file as normal.
2. Click **🎬 Export HUD Video**.
3. Click **Browse…** and select the matching `.MP4` file. The SRT is re-parsed at full frame rate in the background (progress bar animates 0 → 50%).
4. Optionally click **🔍 Refresh Preview** to see a mid-flight frame with the overlay applied.
5. Adjust settings as needed (see below).
6. Click **🎬 Export Video**, choose an output filename, and wait. The progress bar animates 50 → 100% as ffmpeg encodes.
7. A completion dialog offers to reveal the output file in Finder.

### HUD fields

Each field is rendered in a fixed-width format so the text never shifts horizontally as values change during playback.

| Field | Format | Example |
|---|---|---|
| **ALT** — Altitude | 4 digits, no decimal, leading zeros blank | `ALT   82m` |
| **SPD** — Speed | 2 digits, no decimal, capped at 99 | `SPD 30km/h` |
| **HDG** — Heading | 3 digits + 8-point compass, GPS course | `HDG 247° SW` |
| **V/S** — Vertical speed | 2 digits with sign | `V/S +2m/s` |
| **HAG** — Height Above Ground | 4 digits, no decimal | `HAG   45m` |

> **Heading** is the GPS course bearing (direction of travel), not a compass/magnetometer reading. It shows `---` when the drone is stationary.

> **HAG** requires terrain data to have been fetched from Open-Meteo before starting the export. If terrain data is not available, the field shows `---m`.

A typical HUD line looks like:

```
ALT   82m  SPD 30km/h  HDG 247° SW  HAG   45m
```

### Settings

| Setting | Options | Description |
|---|---|---|
| **Fields** | Checkboxes | Choose which telemetry values to show |
| **Speed unit** | km/h / m/s | Unit for the SPD field |
| **Altitude** | Relative / Absolute | Relative = above home point; Absolute = above sea level |
| **Corner** | Top-left / Top-right / Bottom-left / Bottom-right | Screen position of the overlay |
| **Margin X / Y** | Pixels | Offset from the chosen corner |
| **Font size** | Points | Text size in the output video |
| **Colour** | Hex RGB | Text colour (e.g. `FFFFFF` for white) |
| **BG opacity** | 0–255 | Transparency of the background box behind the text |
| **Bold** | Checkbox | Bold text |

All settings are persisted between sessions.

### Encoding

The exporter uses **Apple VideoToolbox** (`h264_videotoolbox`) for hardware-accelerated H.264 encoding at 60 Mbps. On an M2 Pro this runs at approximately 0.85× realtime — a 13-minute 4K 60 fps video encodes in around 15 minutes.

> ffmpeg must be installed and on your `PATH`. Install via Homebrew: `brew install ffmpeg`

### Cancelling

Click **✖ Cancel** during an export to stop encoding immediately. The partial output file is deleted automatically.

---

## File structure

| File | Purpose |
|---|---|
| `srt_viewer.py` | Entry point |
| `gui.py` | Tkinter UI — layout, charts, toolbar, export actions, HUD dialog |
| `controller.py` | Data layer — parsing, stats, CSV/KML export, HUD frame building |
| `srt_parser.py` | SRT file parser and `FlightFrame` data model |
| `hud_exporter.py` | HUD overlay logic — frame derivation, ASS subtitle generation, ffmpeg encoding |
| `config.py` | Persistent user preferences (`~/.dji_srt_viewer/config.json`) |
| `DJI SRT Viewer.spec` | PyInstaller build specification (arm64) |
| `zip_source.sh` | Script to zip source files for distribution |

---

## Licence

MIT
