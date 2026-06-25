"""
gui.py  –  Main Tkinter GUI for DJI SRT Viewer.

Layout:
  ┌────────────────────────────────────────────────┐
  │  Toolbar: [Open SRT] [Export CSV] [Export KML] │
  ├───────────────────────┬────────────────────────┤
  │                       │  Summary               │
  │    Map (matplotlib    ├────────────────────────┤
  │    static plot)       │  Altitude chart        │
  │                       ├────────────────────────┤
  │                       │  Speed chart           │
  ├───────────────────────┴────────────────────────┤
  │  Status bar                                    │
  └────────────────────────────────────────────────┘
"""

import tkinter as tk
from tkinter import ttk, filedialog, messagebox
import os
import math
import webbrowser
import tempfile
import subprocess
import platform
import threading
from typing import Optional

from config import Config
from controller import Controller

_mpl_ready = False

def _ensure_matplotlib():
    global _mpl_ready
    if _mpl_ready:
        return
    import os
    mpl_cache = os.path.join(os.path.expanduser('~'), '.dji_srt_viewer', 'matplotlib')
    os.makedirs(mpl_cache, exist_ok=True)
    os.environ.setdefault('MPLCONFIGDIR', mpl_cache)
    import importlib
    import matplotlib
    matplotlib.use('TkAgg')
    for _m in ('matplotlib.pyplot', 'matplotlib.colors', 'matplotlib.figure',
               'matplotlib.backends.backend_tkagg', 'numpy'):
        importlib.import_module(_m)
    _mpl_ready = True


APP_VERSION = '1.4.0'

# ---------------------------------------------------------------------------
# Colour palette
# ---------------------------------------------------------------------------

BG       = '#1e1e2e'   # dark panel background
BG2      = '#2a2a3e'   # slightly lighter panel
ACCENT   = '#7eb8f7'   # blue accent
GREEN    = '#a6e3a1'
YELLOW   = '#f9e2af'
RED      = '#f38ba8'
TEXT     = '#cdd6f4'
SUBTEXT  = '#6c7086'
GRID     = '#313244'


class GUI:
    """Main application window."""

    def __init__(self, root: tk.Tk, config: Config, controller: Controller):
        self.root       = root
        self.config     = config
        self.controller = controller

        # Wire controller callbacks
        controller.on_load_progress = self._on_progress
        controller.on_load_complete  = self._on_load_complete
        controller.on_load_error     = self._on_load_error

        self._pending_progress: Optional[float] = None
        self._progress_polling = False
        self._tile_generation  = 0
        self._temp_files: list  = []

        self._setup_window()
        self._build_toolbar()
        self._build_main_area()
        self._build_statusbar()

        # Import matplotlib in background immediately so it's ready by first file open
        threading.Thread(target=_ensure_matplotlib, daemon=True).start()

        # Try reopen last file
        last = config.get('last_srt_dir', '')
        if last:
            self._status(f"Last directory: {last}  –  Open an SRT file to begin.")
        else:
            self._status("Open a DJI .SRT file to begin.")

    # ------------------------------------------------------------------
    # Window setup
    # ------------------------------------------------------------------

    def _setup_window(self):
        w = self.config.get('window_width',  1400)
        h = self.config.get('window_height', 860)
        self.root.title("DJI Air 3S  –  SRT Flight Log Viewer")
        sw = self.root.winfo_screenwidth()
        sh = self.root.winfo_screenheight()
        x  = (sw - w) // 2
        y  = (sh - h) // 2
        self.root.geometry(f"{w}x{h}+{x}+{y}")
        self.root.configure(bg=BG)
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

        # Style
        style = ttk.Style(self.root)
        style.theme_use('clam')
        style.configure('.',           background=BG,  foreground=TEXT,  font=('SF Pro Display', 11))
        style.configure('TFrame',      background=BG)
        style.configure('TLabel',      background=BG,  foreground=TEXT)
        style.configure('TButton',     background=BG2, foreground=TEXT,  relief='flat', padding=6)
        style.map('TButton', background=[('active', ACCENT), ('pressed', ACCENT)],
                              foreground=[('active', BG)])
        style.configure('Toolbar.TFrame', background=BG2)
        style.configure('Status.TLabel', background='#11111b', foreground=SUBTEXT, font=('SF Pro Display', 10))
        style.configure('Summary.TLabel', background=BG2, foreground=TEXT, font=('SF Mono', 10), justify='left')
        style.configure('Treeview', background=BG2, foreground=TEXT, fieldbackground=BG2,
                        rowheight=22, font=('SF Mono', 9))
        style.configure('Treeview.Heading', background=GRID, foreground=ACCENT, font=('SF Pro Display', 10, 'bold'))
        style.map('Treeview', background=[('selected', ACCENT)], foreground=[('selected', BG)])
        style.configure('TProgressbar', troughcolor=BG2, background=ACCENT)
        style.configure('Vertical.TScrollbar', background=BG2, troughcolor=BG)

    # ------------------------------------------------------------------
    # Toolbar
    # ------------------------------------------------------------------

    def _build_toolbar(self):
        bar = ttk.Frame(self.root, style='Toolbar.TFrame', padding=(8, 6))
        bar.pack(side=tk.TOP, fill=tk.X)

        ttk.Button(bar, text="📂  Open SRT",    command=self._open_file).pack(side=tk.LEFT, padx=4)
        ttk.Button(bar, text="💾  Export CSV",  command=self._export_csv).pack(side=tk.LEFT, padx=4)
        ttk.Button(bar, text="🌍  Export KML",  command=self._export_kml).pack(side=tk.LEFT, padx=4)
        ttk.Button(bar, text="🗺️  Google Maps",  command=self._open_google_maps).pack(side=tk.LEFT, padx=4)
        ttk.Button(bar, text="🌐  Google Earth", command=self._open_google_earth).pack(side=tk.LEFT, padx=4)

        # Progress bar (hidden until loading)
        self._progress_var = tk.DoubleVar(value=0)
        self._progress_bar = ttk.Progressbar(bar, variable=self._progress_var,
                                              maximum=100, length=200,
                                              style='TProgressbar')
        self._progress_bar.pack(side=tk.LEFT, padx=16)
        self._progress_label = ttk.Label(bar, text='', style='TLabel')
        self._progress_label.pack(side=tk.LEFT)
        self._progress_bar.pack_forget()   # hidden initially
        self._progress_label.pack_forget()

        # Right-side: version label (far right) then altitude toggle
        ttk.Label(bar, text=f'v{APP_VERSION}',
                  foreground=SUBTEXT, font=('SF Pro Display', 9)
                  ).pack(side=tk.RIGHT, padx=(0, 10))
        ttk.Label(bar, text='Altitude:').pack(side=tk.RIGHT, padx=(4, 0))
        self._alt_var = tk.StringVar(value=self.config.get('alt_type', 'rel'))
        for val, label in [('abs', 'Absolute'), ('rel', 'Relative')]:
            rb = ttk.Radiobutton(bar, text=label, variable=self._alt_var,
                                 value=val, command=self._redraw_charts)
            rb.pack(side=tk.RIGHT, padx=2)

    # ------------------------------------------------------------------
    # Main layout
    # ------------------------------------------------------------------

    def _build_main_area(self):
        paned = tk.PanedWindow(self.root, orient=tk.HORIZONTAL,
                               bg=BG, sashwidth=6, sashrelief='flat',
                               sashpad=2)
        paned.pack(fill=tk.BOTH, expand=True, padx=4, pady=4)

        # --- Left: Map ---
        left = ttk.Frame(paned)
        paned.add(left, minsize=600)

        map_label = ttk.Label(left, text='FLIGHT PATH', foreground=ACCENT,
                              font=('SF Pro Display', 9, 'bold'))
        map_label.pack(side=tk.TOP, anchor=tk.W, padx=8, pady=(4, 0))

        self._map_parent = left
        self._map_placeholder = tk.Frame(left, bg=BG2)
        self._map_placeholder.pack(fill=tk.BOTH, expand=True, padx=4, pady=4)

        # --- Right: Summary + charts ---
        right = tk.PanedWindow(paned, orient=tk.VERTICAL,
                               bg=BG, sashwidth=6, sashrelief='flat')
        paned.add(right, minsize=380)

        # Summary panel
        summary_frame = ttk.Frame(right, style='TFrame')
        right.add(summary_frame, minsize=160)

        ttk.Label(summary_frame, text='FLIGHT SUMMARY',
                  foreground=ACCENT, font=('SF Pro Display', 9, 'bold')
                  ).pack(anchor=tk.W, padx=8, pady=(4, 2))

        self._summary_text = tk.Text(
            summary_frame, bg=BG2, fg=TEXT,
            font=('SF Mono', 10), relief='flat',
            state='disabled', wrap='none',
            height=10, padx=8, pady=4
        )
        self._summary_text.pack(fill=tk.BOTH, expand=True, padx=4, pady=(0, 4))

        # Altitude chart
        alt_frame = ttk.Frame(right)
        right.add(alt_frame, minsize=160)

        ttk.Label(alt_frame, text='ALTITUDE  (m)',
                  foreground=GREEN, font=('SF Pro Display', 9, 'bold')
                  ).pack(anchor=tk.W, padx=8, pady=(4, 0))

        self._alt_parent = alt_frame
        self._alt_placeholder = tk.Frame(alt_frame, bg=BG2)
        self._alt_placeholder.pack(fill=tk.BOTH, expand=True, padx=4)

        # Speed chart
        spd_frame = ttk.Frame(right)
        right.add(spd_frame, minsize=160)

        ttk.Label(spd_frame, text='SPEED  (km/h)',
                  foreground=YELLOW, font=('SF Pro Display', 9, 'bold')
                  ).pack(anchor=tk.W, padx=8, pady=(4, 0))

        self._spd_parent = spd_frame
        self._spd_placeholder = tk.Frame(spd_frame, bg=BG2)
        self._spd_placeholder.pack(fill=tk.BOTH, expand=True, padx=4)

        self._mpl_ready = False

    def _init_mpl_async(self):
        """Show toast, import matplotlib on a background thread, then build canvases."""
        toast = tk.Label(self._map_placeholder, text='Please wait — initialising charts…',
                         bg=BG2, fg=SUBTEXT, font=('SF Pro Display', 12))
        toast.place(relx=0.5, rely=0.5, anchor='center')

        def _bg():
            _ensure_matplotlib()
            self.root.after(0, self._create_matplotlib_canvases)

        threading.Thread(target=_bg, daemon=True).start()

    def _create_matplotlib_canvases(self):
        """Runs on main thread after matplotlib is imported. Creates figures and canvases."""
        from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
        from matplotlib.figure import Figure

        self._map_placeholder.destroy()
        self._map_fig  = Figure(figsize=(7, 6), facecolor=BG)
        self._map_ax   = self._map_fig.add_subplot(111)
        self._map_cbar = None
        self._style_ax(self._map_ax)
        self._map_canvas = FigureCanvasTkAgg(self._map_fig, master=self._map_parent)
        self._map_canvas.get_tk_widget().pack(fill=tk.BOTH, expand=True, padx=4, pady=4)

        self._alt_placeholder.destroy()
        self._alt_fig = Figure(figsize=(5, 2), facecolor=BG)
        self._alt_ax  = self._alt_fig.add_subplot(111)
        self._style_ax(self._alt_ax)
        self._alt_canvas = FigureCanvasTkAgg(self._alt_fig, master=self._alt_parent)
        self._alt_canvas.get_tk_widget().pack(fill=tk.BOTH, expand=True, padx=4)

        self._spd_placeholder.destroy()
        self._spd_fig = Figure(figsize=(5, 2), facecolor=BG)
        self._spd_ax  = self._spd_fig.add_subplot(111)
        self._style_ax(self._spd_ax)
        self._spd_canvas = FigureCanvasTkAgg(self._spd_fig, master=self._spd_parent)
        self._spd_canvas.get_tk_widget().pack(fill=tk.BOTH, expand=True, padx=4)

        self._mpl_ready = True
        self._redraw_all()

    def _style_ax(self, ax):
        ax.set_facecolor(BG2)
        for spine in ax.spines.values():
            spine.set_color(GRID)
        ax.tick_params(colors=SUBTEXT, labelsize=8)
        ax.grid(color=GRID, linewidth=0.5, linestyle='--')

    # ------------------------------------------------------------------
    # Status bar
    # ------------------------------------------------------------------

    def _build_statusbar(self):
        self._status_var = tk.StringVar(value='Ready')
        bar = ttk.Label(self.root, textvariable=self._status_var,
                        style='Status.TLabel', padding=(8, 3))
        bar.pack(side=tk.BOTTOM, fill=tk.X)

    def _status(self, msg: str):
        self.root.after(0, lambda: self._status_var.set(msg))

    # ------------------------------------------------------------------
    # File open
    # ------------------------------------------------------------------

    def _open_file(self):
        init_dir = self.config.get('last_srt_dir', os.path.expanduser('~'))
        path = filedialog.askopenfilename(
            title='Open DJI SRT File',
            initialdir=init_dir,
            filetypes=[('SRT files', '*.srt *.SRT'), ('All files', '*.*')]
        )
        if not path:
            return
        self._show_progress(True)
        self._status(f'Loading {os.path.basename(path)} …')
        self.controller.load_file(path)

    # ------------------------------------------------------------------
    # Progress callbacks (called from background thread → marshal to UI)
    # ------------------------------------------------------------------

    def _show_progress(self, show: bool):
        def _do():
            if show:
                self._progress_bar.pack(side=tk.LEFT, padx=16)
                self._progress_label.pack(side=tk.LEFT)
                self._pending_progress = None
                if not self._progress_polling:
                    self._progress_polling = True
                    self._poll_progress()
            else:
                self._progress_polling = False
                self._progress_bar.pack_forget()
                self._progress_label.pack_forget()
        self.root.after(0, _do)

    def _on_progress(self, pct: float):
        self._pending_progress = pct  # written by background thread; GIL makes float assignment atomic

    def _poll_progress(self):
        if not self._progress_polling:
            return
        pct = self._pending_progress
        if pct is not None:
            self._progress_var.set(pct)
            self._progress_label.config(text=f'{pct:.0f}%')
            self._pending_progress = None
        self.root.after(100, self._poll_progress)

    def _on_load_complete(self):
        def _do():
            self._show_progress(False)
            self._status(f"Loaded  {len(self.controller.frames):,} samples  "
                         f"from  {os.path.basename(self.controller.filepath)}")
            if self._mpl_ready:
                self._redraw_all()
            elif _mpl_ready:
                # prefetch finished — canvases just need creating (fast, no toast needed)
                self._create_matplotlib_canvases()
            else:
                self._init_mpl_async()
        self.root.after(0, _do)

    def _on_load_error(self, msg: str):
        def _do():
            self._show_progress(False)
            self._status(f'Error: {msg}')
            messagebox.showerror('Load Error', msg)
        self.root.after(0, _do)

    # ------------------------------------------------------------------
    # Drawing
    # ------------------------------------------------------------------

    def _redraw_all(self):
        self._draw_map()
        self._draw_altitude()
        self._draw_speed()
        self._draw_summary()

    def _redraw_charts(self):
        if not self.controller.loaded:
            return
        self.config.set('alt_type', self._alt_var.get())
        self._draw_altitude()

    # ---- Map ----------------------------------------------------------

    def _draw_map(self):
        import matplotlib.pyplot as plt
        ax = self._map_ax
        ax.clear()
        self._style_ax(ax)

        frames = self.controller.frames
        lats = [f.latitude  for f in frames]
        lons = [f.longitude for f in frames]
        alts = [f.rel_alt   for f in frames]

        # Colour track by altitude
        max_alt = max(alts) if alts else 1
        norm    = plt.Normalize(0, max_alt)
        cmap    = plt.cm.plasma

        for i in range(1, len(lats)):
            c = cmap(norm((alts[i-1] + alts[i]) / 2))
            ax.plot([lons[i-1], lons[i]], [lats[i-1], lats[i]],
                    color=c, linewidth=1.5, solid_capstyle='round')

        # Home + end markers
        ax.scatter([lons[0]],  [lats[0]],  c=GREEN,  s=80,  zorder=5,
                   marker='o', label='Home')
        ax.scatter([lons[-1]], [lats[-1]], c=RED,    s=80,  zorder=5,
                   marker='X', label='End')

        # Colourbar — remove stale instance before creating a new one
        if self._map_cbar is not None:
            self._map_cbar.remove()
        sm = plt.cm.ScalarMappable(cmap=cmap, norm=norm)
        sm.set_array([])
        self._map_cbar = self._map_fig.colorbar(sm, ax=ax, fraction=0.03, pad=0.02)
        self._map_cbar.set_label('Altitude (m)', color=TEXT, fontsize=8)
        self._map_cbar.ax.yaxis.set_tick_params(color=SUBTEXT, labelsize=7)
        plt.setp(plt.getp(self._map_cbar.ax.axes, 'yticklabels'), color=SUBTEXT)

        ax.set_xlabel('Longitude', color=SUBTEXT, fontsize=8)
        ax.set_ylabel('Latitude',  color=SUBTEXT, fontsize=8)
        ax.set_title(os.path.basename(self.controller.filepath),
                     color=TEXT, fontsize=9, pad=6)
        ax.legend(loc='lower left', fontsize=7,
                  facecolor=BG2, edgecolor=GRID, labelcolor=TEXT)

        # Equal-ish aspect for map
        ax.set_aspect('equal', adjustable='datalim')

        self._map_fig.tight_layout(pad=0.5)
        self._map_canvas.draw()

        # Fetch satellite tiles in background; composite when ready
        self._tile_generation += 1
        generation = self._tile_generation
        threading.Thread(
            target=self._fetch_tile_mosaic,
            args=(lats, lons, generation),
            daemon=True,
        ).start()

    # ---- Satellite basemap (background thread + main-thread composite) ----

    def _fetch_tile_mosaic(self, lats, lons, generation):
        """Runs on a background thread. Fetches tiles and schedules composite."""
        try:
            import mercantile, requests, io, numpy as np
            from PIL import Image

            lat_span = max(lats) - min(lats) or 0.01
            lon_span = max(lons) - min(lons) or 0.01
            pad = 0.15
            lat_min = min(lats) - lat_span * pad
            lat_max = max(lats) + lat_span * pad
            lon_min = min(lons) - lon_span * pad
            lon_max = max(lons) + lon_span * pad

            max_span = max(lat_max - lat_min, lon_max - lon_min)
            zoom = max(12, min(17, round(math.log2(360 / max_span)) + 3))

            tiles = list(mercantile.tiles(lon_min, lat_min, lon_max, lat_max, zooms=zoom))
            if len(tiles) > 36:
                zoom -= 1
                tiles = list(mercantile.tiles(lon_min, lat_min, lon_max, lat_max, zooms=zoom))

            headers = {'User-Agent': 'DJI-SRT-Viewer/1.0'}
            fetched = {}
            for t in tiles:
                if generation != self._tile_generation:
                    return  # map redrawn — abandon this fetch
                url = (f"https://server.arcgisonline.com/ArcGIS/rest/services/"
                       f"World_Imagery/MapServer/tile/{t.z}/{t.y}/{t.x}")
                r = requests.get(url, timeout=8, headers=headers)
                if r.status_code == 200:
                    fetched[t] = Image.open(io.BytesIO(r.content)).convert('RGB')

            if not fetched:
                return

            xs = sorted({t.x for t in fetched})
            ys = sorted({t.y for t in fetched})
            x_min, x_max = xs[0], xs[-1]
            y_min, y_max = ys[0], ys[-1]

            tile_px = 256
            mosaic = Image.new('RGB', ((x_max - x_min + 1) * tile_px,
                                       (y_max - y_min + 1) * tile_px))
            for t, img in fetched.items():
                mosaic.paste(img, ((t.x - x_min) * tile_px, (t.y - y_min) * tile_px))

            nw = mercantile.bounds(mercantile.Tile(x_min, y_min, zoom))
            se = mercantile.bounds(mercantile.Tile(x_max, y_max, zoom))
            extent = [nw.west, se.east, se.south, nw.north]
            mosaic_arr = np.array(mosaic)

            self.root.after(0, lambda: self._apply_basemap(mosaic_arr, extent, generation))
        except Exception:
            pass  # no internet or missing library — plain background kept

    def _apply_basemap(self, mosaic_arr, extent, generation):
        """Runs on the main thread. Composites tiles behind the flight path."""
        if generation != self._tile_generation:
            return  # stale — a newer map has since been drawn
        ax = self._map_ax
        xlim, ylim = ax.get_xlim(), ax.get_ylim()
        ax.imshow(mosaic_arr, extent=extent, aspect='auto',
                  interpolation='bilinear', zorder=0)
        ax.set_xlim(xlim)
        ax.set_ylim(ylim)
        self._map_canvas.draw()

    # ---- Altitude chart -----------------------------------------------

    def _draw_altitude(self):
        ax = self._alt_ax
        ax.clear()
        self._style_ax(ax)

        times, alts = self.controller.altitude_series()
        times_min = [t / 60 for t in times]

        ax.fill_between(times_min, alts, alpha=0.3, color=GREEN)
        ax.plot(times_min, alts, color=GREEN, linewidth=1.2)

        alt_type = self.config.get('alt_type', 'rel')
        label = 'Relative alt (m)' if alt_type == 'rel' else 'Absolute alt (m)'
        ax.set_ylabel(label, color=SUBTEXT, fontsize=7)
        ax.set_xlabel('Time (min)', color=SUBTEXT, fontsize=7)

        self._alt_fig.tight_layout(pad=0.5)
        self._alt_canvas.draw()

    # ---- Speed chart --------------------------------------------------

    def _draw_speed(self):
        ax = self._spd_ax
        ax.clear()
        self._style_ax(ax)

        times, speeds = self.controller.speed_series()
        times_min = [t / 60 for t in times]

        ax.fill_between(times_min, speeds, alpha=0.3, color=YELLOW)
        ax.plot(times_min, speeds, color=YELLOW, linewidth=1.2)

        unit  = self.config.get('speed_unit', 'kmh')
        label = 'Speed (km/h)' if unit == 'kmh' else 'Speed (m/s)'
        ax.set_ylabel(label, color=SUBTEXT, fontsize=7)
        ax.set_xlabel('Time (min)', color=SUBTEXT, fontsize=7)

        self._spd_fig.tight_layout(pad=0.5)
        self._spd_canvas.draw()

    # ---- Summary text -------------------------------------------------

    def _draw_summary(self):
        text = self.controller.summary_text()
        self._summary_text.config(state='normal')
        self._summary_text.delete('1.0', tk.END)
        self._summary_text.insert(tk.END, text)
        self._summary_text.config(state='disabled')

    # ------------------------------------------------------------------
    # Export
    # ------------------------------------------------------------------

    def _export_csv(self):
        if not self.controller.loaded:
            messagebox.showinfo('Export', 'Load an SRT file first.')
            return
        base = os.path.splitext(self.controller.filepath)[0]
        path = filedialog.asksaveasfilename(
            title='Export CSV',
            initialfile=os.path.basename(base) + '_telemetry.csv',
            defaultextension='.csv',
            filetypes=[('CSV files', '*.csv')]
        )
        if path:
            try:
                self.controller.export_csv(path)
                self._status(f'Exported CSV → {os.path.basename(path)}')
            except Exception as e:
                messagebox.showerror('Export Error', str(e))

    def _export_kml(self):
        if not self.controller.loaded:
            messagebox.showinfo('Export', 'Load an SRT file first.')
            return
        base = os.path.splitext(self.controller.filepath)[0]
        path = filedialog.asksaveasfilename(
            title='Export KML',
            initialfile=os.path.basename(base) + '_track.kml',
            defaultextension='.kml',
            filetypes=[('KML files', '*.kml')]
        )
        if path:
            try:
                self.controller.export_kml(path)
                self._status(f'Exported KML → {os.path.basename(path)}')
            except Exception as e:
                messagebox.showerror('Export Error', str(e))

    def _open_google_maps(self):
        if not self.controller.loaded:
            messagebox.showinfo('Google Maps', 'Load an SRT file first.')
            return
        frames = self.controller.frames
        s = self.controller.stats
        center_lat = (s['lat_min'] + s['lat_max']) / 2
        center_lon = (s['lon_min'] + s['lon_max']) / 2
        max_span = max(s['lat_max'] - s['lat_min'], s['lon_max'] - s['lon_min'])
        zoom = max(1, min(20, round(math.log2(360 / max_span)) - 1)) if max_span > 0 else 15

        coords_js = ','.join(f'[{f.latitude:.6f},{f.longitude:.6f}]' for f in frames)
        html = f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>DJI Flight Path</title>
<link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css"/>
<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
<style>body{{margin:0}}#map{{height:100vh}}</style></head>
<body><div id="map"></div><script>
var coords=[{coords_js}];
var map=L.map('map').setView([{center_lat:.6f},{center_lon:.6f}],{zoom});
L.tileLayer('https://{{s}}.google.com/vt/lyrs=s&x={{x}}&y={{y}}&z={{z}}',
  {{subdomains:['mt0','mt1','mt2','mt3'],attribution:'Google Satellite'}}).addTo(map);
L.polyline(coords,{{color:'#f9e2af',weight:3,opacity:0.9}}).addTo(map);
L.circleMarker(coords[0],{{radius:8,color:'#a6e3a1',fillColor:'#a6e3a1',fillOpacity:1}})
  .addTo(map).bindPopup('Home').openPopup();
L.circleMarker(coords[coords.length-1],{{radius:8,color:'#f38ba8',fillColor:'#f38ba8',fillOpacity:1}})
  .addTo(map).bindPopup('End');
</script></body></html>"""

        try:
            fd, html_path = tempfile.mkstemp(suffix='.html', prefix='dji_srt_maps_')
            with os.fdopen(fd, 'w') as f:
                f.write(html)
            self._temp_files.append(html_path)
            webbrowser.open(f'file://{html_path}')
        except Exception as e:
            messagebox.showerror('Google Maps', str(e))

    def _open_google_earth(self):
        if not self.controller.loaded:
            messagebox.showinfo('Google Earth', 'Load an SRT file first.')
            return
        try:
            fd, kml_path = tempfile.mkstemp(suffix='.kml', prefix='dji_srt_earth_')
            os.close(fd)
            self.controller.export_kml(kml_path)
            self._temp_files.append(kml_path)
            sys = platform.system()
            if sys == 'Darwin':
                subprocess.Popen(['open', kml_path])
            elif sys == 'Windows':
                os.startfile(kml_path)  # os.startfile is Windows-only; safe here as branch is Windows-gated
            else:
                subprocess.Popen(['xdg-open', kml_path])
        except Exception as e:
            messagebox.showerror('Google Earth', str(e))

    # ------------------------------------------------------------------
    # Close
    # ------------------------------------------------------------------

    def _on_close(self):
        for path in self._temp_files:
            try:
                os.remove(path)
            except OSError:
                pass

        w = self.root.winfo_width()
        h = self.root.winfo_height()
        self.config.set('window_width',  w)
        self.config.set('window_height', h)
        self.config.set('alt_type', self._alt_var.get())
        self.config.save()
        self.root.destroy()
