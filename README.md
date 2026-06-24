# DJI Air 3S — SRT Flight Log Viewer

A desktop application for visualising flight telemetry from DJI Air 3S `.SRT` subtitle files. Load a flight log to see the GPS track overlaid on a satellite map, with altitude and speed charts, a full flight summary, and one-click export to CSV or KML.

---

## Features

- **Satellite map** — GPS track coloured by altitude, plotted over live Esri World Imagery satellite tiles
- **Altitude chart** — time-series plot, switchable between relative (above home point) and absolute (above sea level)
- **Speed chart** — time-series plot in km/h
- **Flight summary** — date/time, duration, distance, max and average speed, max altitude, home point coordinates
- **Export CSV** — all telemetry fields at 1 sample/second (frame, timestamp, lat/lon, altitude, speed, ISO, shutter, f-number, focal length, colour temperature)
- **Export KML** — GPS track and home point marker for use in any GIS tool
- **View in Google Maps** — opens a local map in your browser showing the full flight path and markers
- **View in Google Earth** — exports a styled KML and opens it directly in Google Earth

---

## Requirements

- macOS (Apple Silicon or Intel)
- Python 3.9+
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
pip install matplotlib mercantile requests pillow
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

- **ALTITUDE** — shows relative altitude (metres above the home point) by default. Switch to absolute altitude (metres above sea level) using the **Relative / Absolute** toggle in the toolbar.
- **SPEED** — ground speed in km/h derived from consecutive GPS positions.

Both charts share the same time axis (minutes from takeoff).

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

## File structure

| File | Purpose |
|---|---|
| `srt_viewer.py` | Entry point |
| `gui.py` | Tkinter UI — layout, charts, toolbar, export actions |
| `controller.py` | Data layer — parsing, stats, CSV/KML export |
| `srt_parser.py` | SRT file parser and `FlightFrame` data model |
| `config.py` | Persistent user preferences (`~/.dji_srt_viewer/config.json`) |
| `DJI SRT Viewer.spec` | PyInstaller build specification (arm64) |
| `zip_source.sh` | Script to zip source files for distribution |

---

## Licence

MIT
