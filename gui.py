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
import queue
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


APP_VERSION = '2.2'

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
        controller.on_terrain_ready  = self._on_terrain_ready
        controller.on_terrain_error  = self._on_terrain_error

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
        ttk.Button(bar, text="🎬  Export HUD Video", command=self._open_hud_dialog).pack(side=tk.LEFT, padx=4)

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
        self._draw_gen  = 0     # guards stale BG renders

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
                         f"from  {os.path.basename(self.controller.filepath)}"
                         f"  —  fetching terrain elevation…")
            self.controller.fetch_terrain()
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

    def _on_terrain_ready(self):
        def _do():
            self._status(f"Loaded  {len(self.controller.frames):,} samples  "
                         f"from  {os.path.basename(self.controller.filepath)}")
            if self._mpl_ready:
                self._draw_gen += 1
                threading.Thread(target=self._render_altitude,
                                 args=(self._draw_gen,), daemon=True).start()
            # if canvases not ready yet, _redraw_all will pick up terrain automatically
        self.root.after(0, _do)

    def _on_terrain_error(self, msg: str):
        def _do():
            self._status(
                f"Loaded  {len(self.controller.frames):,} samples  "
                f"from  {os.path.basename(self.controller.filepath)}"
                f"  —  terrain unavailable: {msg[:60]}")
            if self._mpl_ready:
                self._draw_gen += 1
                threading.Thread(target=self._render_altitude,
                                 args=(self._draw_gen,), daemon=True).start()
        self.root.after(0, _do)

    # ------------------------------------------------------------------
    # Drawing
    # ------------------------------------------------------------------

    def _redraw_all(self):
        """Dispatch chart renders to background threads.

        matplotlib axes operations run on daemon threads; only canvas.draw()
        (the Agg→Tk blit) is marshalled back to the UI thread via after(0).
        _draw_gen discards stale renders if a newer redraw has started.
        """
        self._draw_gen += 1
        gen = self._draw_gen
        threading.Thread(target=self._render_map,      args=(gen,), daemon=True).start()
        threading.Thread(target=self._render_altitude, args=(gen,), daemon=True).start()
        threading.Thread(target=self._render_speed,    args=(gen,), daemon=True).start()
        self._draw_summary()


    def _redraw_charts(self):
        if not self.controller.loaded:
            return
        self.config.set('alt_type', self._alt_var.get())
        self._draw_gen += 1
        gen = self._draw_gen
        threading.Thread(target=self._render_altitude, args=(gen,), daemon=True).start()

    # ---- Map ----------------------------------------------------------

    def _render_map(self, gen: int):
        """Render map on BG thread; schedule canvas.draw() on UI thread."""
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
        if gen == self._draw_gen:
            self.root.after(0, self._map_canvas.draw)

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

    def _render_altitude(self, gen: int):
        """Render altitude chart on BG thread."""
        ax = self._alt_ax
        ax.clear()
        self._style_ax(ax)

        # Remove any previous HAG twin axis so we start fresh
        for other in self._alt_fig.axes[1:]:
            other.remove()

        times, alts = self.controller.altitude_series()
        times_min = [t / 60 for t in times]

        ax.fill_between(times_min, alts, alpha=0.3, color=GREEN)
        ax.plot(times_min, alts, color=GREEN, linewidth=1.2)

        alt_type = self.config.get('alt_type', 'rel')
        label = 'Relative alt (m)' if alt_type == 'rel' else 'Absolute alt (m)'
        ax.set_ylabel(label, color=SUBTEXT, fontsize=7)
        ax.set_xlabel('Time (min)', color=SUBTEXT, fontsize=7)

        # HAG on a fresh right axis each redraw (avoids clear() resetting twinx positioning)
        times_h, hags = self.controller.hag_series()
        ax2 = ax.twinx()
        ax2.set_facecolor('none')
        ax2.tick_params(axis='y', colors=ACCENT, labelsize=8)
        for spine in ax2.spines.values():
            spine.set_color(GRID)
        ax2.spines['right'].set_color(ACCENT)
        if hags:
            times_h_min = [t / 60 for t in times_h]
            ax2.plot(times_h_min, hags, color=ACCENT, linewidth=1.2, linestyle='--', alpha=0.9)
            ax2.set_ylabel('HAG (m)', color=ACCENT, fontsize=7)
            ax2.set_yticks([])
        else:
            state = self.controller.terrain_state
            if state == 'error':
                lbl = 'HAG — unavailable'
            elif state == 'fetching':
                lbl = 'HAG — fetching…'
            else:
                lbl = 'HAG'
            ax2.set_ylabel(lbl, color=SUBTEXT, fontsize=7)
            ax2.set_yticks([])

        self._alt_fig.tight_layout(pad=0.5, rect=[0, 0, 0.95, 1])
        if hags:
            ax2.set_ylim(ax.get_ylim())   # sync AFTER layout is finalised
        if gen == self._draw_gen:
            self.root.after(0, self._alt_canvas.draw)

    # ---- Speed chart --------------------------------------------------

    def _render_speed(self, gen: int):
        """Render speed chart on BG thread."""
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
        if gen == self._draw_gen:
            self.root.after(0, self._spd_canvas.draw)

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


    # ------------------------------------------------------------------
    # HUD Export Dialog
    # ------------------------------------------------------------------

    def _open_hud_dialog(self):
        if not self.controller.loaded:
            messagebox.showinfo('HUD Export', 'Load an SRT file first.')
            return
        HudExportDialog(self.root, self.config, self.controller)


class HudExportDialog(tk.Toplevel):
    """Modal-style dialog for configuring and running HUD video export.

    Layout
    ------
    Left column : settings (fields, position, style)
    Right column: preview canvas  + progress / action buttons
    """

    _CORNERS = [('Top-left', 'tl'), ('Top-right', 'tr'),
                ('Bottom-left', 'bl'), ('Bottom-right', 'br')]

    def __init__(self, parent, config: 'Config', controller: 'Controller'):
        super().__init__(parent)
        self._root      = parent            # root Tk window
        self._ui_queue  = queue.SimpleQueue()  # background threads post UI events here
        self._polling   = True
        self.config     = config
        self.controller = controller

        self.title('Export HUD Video')
        self.configure(bg=BG)
        self.resizable(True, True)

        # Centre over parent
        pw = parent.winfo_width()
        ph = parent.winfo_height()
        px = parent.winfo_rootx()
        py = parent.winfo_rooty()
        dw, dh = 1020, 660
        self.geometry(f'{dw}x{dh}+{px + (pw - dw)//2}+{py + (ph - dh)//2}')

        # State
        self._mp4_path    = tk.StringVar()
        self._parse_done  = False
        self._exporting   = False
        self._preview_img = None   # numpy array

        # Tkinter vars for settings
        self._show_alt   = tk.BooleanVar(value=bool(config.get('hud_show_altitude', True)))
        self._show_spd   = tk.BooleanVar(value=bool(config.get('hud_show_speed', True)))
        self._show_hdg   = tk.BooleanVar(value=bool(config.get('hud_show_heading', True)))
        self._show_vs    = tk.BooleanVar(value=bool(config.get('hud_show_vspeed', False)))
        self._show_hag   = tk.BooleanVar(value=bool(config.get('hud_show_hag', False)))
        self._speed_unit = tk.StringVar(value=config.get('hud_speed_unit', 'kmh'))
        self._alt_type   = tk.StringVar(value=config.get('hud_alt_type', 'rel'))
        self._corner     = tk.StringVar(value=config.get('hud_corner', 'tl'))
        self._margin_x   = tk.IntVar(value=int(config.get('hud_margin_x', 30)))
        self._margin_y   = tk.IntVar(value=int(config.get('hud_margin_y', 30)))
        self._font_size  = tk.IntVar(value=int(config.get('hud_font_size', 22)))
        self._font_col   = tk.StringVar(value=config.get('hud_font_colour', 'FFFFFF'))
        self._bg_alpha   = tk.IntVar(value=int(config.get('hud_bg_alpha', 140)))
        self._bold       = tk.BooleanVar(value=bool(config.get('hud_bold', True)))

        self._progress_var = tk.DoubleVar(value=0)
        self._status_var   = tk.StringVar(value='Select the matching MP4 file to begin.')

        # Wire controller HUD callbacks
        controller.on_hud_parse_progress  = self._on_parse_progress
        controller.on_hud_parse_complete  = self._on_parse_complete
        controller.on_hud_export_progress = self._on_export_progress
        controller.on_hud_export_complete = self._on_export_complete
        controller.on_hud_export_error    = self._on_export_error

        self._build_ui()
        self.grab_set()   # make modal
        self.focus_set()
        self.protocol('WM_DELETE_WINDOW', self._on_close)
        self.after(100, self._poll_ui)   # start background-event polling

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def _build_ui(self):
        # Pack bottom FIRST so it gets space before outer expands
        bottom = tk.Frame(self, bg=BG, padx=10, pady=4)
        bottom.pack(fill=tk.X, side=tk.BOTTOM)
        tk.Label(bottom, textvariable=self._status_var,
                 fg=SUBTEXT, bg=BG, font=('SF Mono', 9)
                 ).pack(anchor=tk.W)
        self._pbar = tk.Canvas(bottom, height=26, bg=BG2,
                               highlightthickness=1,
                               highlightbackground=GRID)
        self._pbar.pack(fill=tk.X, pady=(3, 0))
        self._pbar_pct = 0.0
        self._pbar.bind('<Configure>', lambda e: self._redraw_pbar())

        # ---- outer two-column layout (packed after bottom so it fills remaining space) ----
        outer = ttk.Frame(self, style='TFrame', padding=10)
        outer.pack(fill=tk.BOTH, expand=True)
        outer.columnconfigure(0, weight=0, minsize=310)
        outer.columnconfigure(1, weight=1)
        outer.rowconfigure(0, weight=1)

        left  = ttk.Frame(outer, style='TFrame')
        left.grid(row=0, column=0, sticky='nsew', padx=(0, 10))
        right = ttk.Frame(outer, style='TFrame')
        right.grid(row=0, column=1, sticky='nsew')
        right.rowconfigure(1, weight=1)

        self._build_left(left)
        self._build_right(right)

    def _redraw_pbar(self):
        c = self._pbar
        w = c.winfo_width()
        h = c.winfo_height()
        if w < 2:
            return
        c.delete('all')
        # trough
        c.create_rectangle(0, 0, w, h, fill=BG2, outline='')
        # fill
        fw = int(w * self._pbar_pct / 100)
        if fw > 0:
            c.create_rectangle(0, 0, fw, h, fill=ACCENT, outline='')
        # label
        if self._pbar_pct > 0:
            c.create_text(w // 2, h // 2,
                          text=f'{self._pbar_pct:.0f}%',
                          fill=TEXT, font=('SF Mono', 14))

    def _set_pbar(self, pct: float):
        self._pbar_pct = max(0.0, min(100.0, pct))
        self._redraw_pbar()

    def _section(self, parent, label: str) -> ttk.Frame:
        """Returns a labelled LabelFrame-style container."""
        lf = tk.LabelFrame(parent, text=label,
                           bg=BG, fg=ACCENT,
                           font=('SF Pro Display', 9, 'bold'),
                           relief='flat', bd=1,
                           highlightbackground=GRID,
                           highlightthickness=1,
                           padx=8, pady=6)
        lf.pack(fill=tk.X, pady=(0, 8))
        return lf

    def _row(self, parent, label: str, widget_factory):
        """Two-column label + widget row."""
        f = ttk.Frame(parent, style='TFrame')
        f.pack(fill=tk.X, pady=2)
        ttk.Label(f, text=label, width=16, anchor='w').pack(side=tk.LEFT)
        widget_factory(f).pack(side=tk.LEFT, fill=tk.X, expand=True)

    def _build_left(self, parent):
        # ---- MP4 source ----
        mp4_sec = self._section(parent, 'Source MP4')
        mp4_frame = ttk.Frame(mp4_sec, style='TFrame')
        mp4_frame.pack(fill=tk.X)
        self._mp4_entry = ttk.Entry(mp4_frame, textvariable=self._mp4_path,
                                    font=('SF Mono', 9))
        self._mp4_entry.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 4))
        ttk.Button(mp4_frame, text='Browse…',
                   command=self._browse_mp4).pack(side=tk.LEFT)

        # ---- Fields ----
        fld = self._section(parent, 'HUD Fields')
        ttk.Checkbutton(fld, text='Altitude', variable=self._show_alt,
                        command=self._settings_changed).pack(anchor='w')
        ttk.Checkbutton(fld, text='Speed', variable=self._show_spd,
                        command=self._settings_changed).pack(anchor='w')
        ttk.Checkbutton(fld, text='Heading (GPS course)', variable=self._show_hdg,
                        command=self._settings_changed).pack(anchor='w')
        ttk.Checkbutton(fld, text='Vertical speed', variable=self._show_vs,
                        command=self._settings_changed).pack(anchor='w')
        ttk.Checkbutton(fld, text='HAG (requires terrain data)',
                        variable=self._show_hag,
                        command=self._settings_changed).pack(anchor='w')

        # ---- Units ----
        units = self._section(parent, 'Units')
        uf = ttk.Frame(units, style='TFrame')
        uf.pack(fill=tk.X)
        ttk.Label(uf, text='Speed:').pack(side=tk.LEFT)
        ttk.Radiobutton(uf, text='km/h', variable=self._speed_unit,
                        value='kmh', command=self._settings_changed).pack(side=tk.LEFT, padx=4)
        ttk.Radiobutton(uf, text='m/s', variable=self._speed_unit,
                        value='ms', command=self._settings_changed).pack(side=tk.LEFT)
        af = ttk.Frame(units, style='TFrame')
        af.pack(fill=tk.X, pady=(4, 0))
        ttk.Label(af, text='Altitude:').pack(side=tk.LEFT)
        ttk.Radiobutton(af, text='Relative', variable=self._alt_type,
                        value='rel', command=self._settings_changed).pack(side=tk.LEFT, padx=4)
        ttk.Radiobutton(af, text='Absolute', variable=self._alt_type,
                        value='abs', command=self._settings_changed).pack(side=tk.LEFT)

        # ---- Position ----
        pos = self._section(parent, 'Position')
        cf = ttk.Frame(pos, style='TFrame')
        cf.pack(fill=tk.X, pady=(0, 4))
        ttk.Label(cf, text='Corner:', width=10, anchor='w').pack(side=tk.LEFT)
        for label, val in self._CORNERS:
            ttk.Radiobutton(cf, text=label, variable=self._corner,
                            value=val, command=self._settings_changed).pack(side=tk.LEFT, padx=3)

        def _spin_row(parent, label, var):
            f = ttk.Frame(parent, style='TFrame')
            f.pack(fill=tk.X, pady=2)
            ttk.Label(f, text=label, width=14, anchor='w').pack(side=tk.LEFT)
            sb = tk.Spinbox(f, from_=0, to=500, textvariable=var, width=6,
                            bg=BG2, fg=TEXT, insertbackground=TEXT,
                            buttonbackground=BG2, relief='flat',
                            command=self._settings_changed)
            sb.pack(side=tk.LEFT)
            sb.bind('<Return>', lambda _: self._settings_changed())

        _spin_row(pos, 'Margin X (px):', self._margin_x)
        _spin_row(pos, 'Margin Y (px):', self._margin_y)

        # ---- Style ----
        sty = self._section(parent, 'Style')
        _spin_row(sty, 'Font size (pt):', self._font_size)

        col_f = ttk.Frame(sty, style='TFrame')
        col_f.pack(fill=tk.X, pady=2)
        ttk.Label(col_f, text='Colour (hex):', width=14, anchor='w').pack(side=tk.LEFT)
        col_entry = ttk.Entry(col_f, textvariable=self._font_col, width=8,
                              font=('SF Mono', 10))
        col_entry.pack(side=tk.LEFT)
        col_entry.bind('<Return>', lambda _: self._settings_changed())

        _spin_row(sty, 'BG opacity:', self._bg_alpha)
        ttk.Checkbutton(sty, text='Bold', variable=self._bold,
                        command=self._settings_changed).pack(anchor='w', pady=2)

    def _build_right(self, parent):
        ttk.Label(parent, text='PREVIEW', foreground=ACCENT,
                  font=('SF Pro Display', 9, 'bold')
                  ).grid(row=0, column=0, sticky='w', pady=(0, 4))

        # Preview canvas
        self._preview_frame = tk.Frame(parent, bg=BG2, relief='flat')
        self._preview_frame.grid(row=1, column=0, sticky='nsew')
        parent.rowconfigure(1, weight=1)
        parent.columnconfigure(0, weight=1)

        self._preview_label = tk.Label(self._preview_frame,
                                       text='Preview will appear here\nafter selecting an MP4.',
                                       bg=BG2, fg=SUBTEXT,
                                       font=('SF Pro Display', 11))
        self._preview_label.place(relx=0.5, rely=0.5, anchor='center')

        # Buttons
        btn_frame = ttk.Frame(parent, style='TFrame')
        btn_frame.grid(row=2, column=0, pady=(8, 0), sticky='ew')
        btn_frame.columnconfigure(0, weight=1)
        btn_frame.columnconfigure(1, weight=1)
        btn_frame.columnconfigure(2, weight=1)

        self._btn_preview = ttk.Button(btn_frame, text='🔍  Refresh Preview',
                                        command=self._refresh_preview)
        self._btn_preview.grid(row=0, column=0, padx=4, sticky='ew')

        self._btn_export = ttk.Button(btn_frame, text='🎬  Export Video',
                                       command=self._start_export)
        self._btn_export.grid(row=0, column=1, padx=4, sticky='ew')

        self._btn_cancel = ttk.Button(btn_frame, text='✖  Cancel',
                                       command=self._on_close, state='normal')
        self._btn_cancel.grid(row=0, column=2, padx=4, sticky='ew')

    # ------------------------------------------------------------------
    # MP4 browse
    # ------------------------------------------------------------------

    def _browse_mp4(self):
        init = self.config.get('hud_last_mp4_dir', '') or \
               self.config.get('last_srt_dir', os.path.expanduser('~'))
        path = filedialog.askopenfilename(
            parent=self,
            title='Select matching MP4',
            initialdir=init,
            filetypes=[('MP4 files', '*.mp4 *.MP4'), ('All files', '*.*')]
        )
        if not path:
            return
        self._mp4_path.set(path)
        self.config.set('hud_last_mp4_dir', os.path.dirname(path))
        self._status('Parsing SRT at full resolution — this may take a moment…')
        self._btn_export.config(state='disabled')
        self._btn_preview.config(state='disabled')
        # Restart poll loop — native open dialog suspends Tk's after() chain
        self._polling = True
        self.after(100, self._poll_ui)
        self.controller.build_hud_frames_async()

    # ------------------------------------------------------------------
    # Settings changed → invalidate preview
    # ------------------------------------------------------------------

    def _settings_changed(self):
        if self._parse_done:
            self._preview_label.config(text='Settings changed.\nClick Refresh Preview.')
            if hasattr(self, '_prev_img_label'):
                self._prev_img_label.destroy()
                del self._prev_img_label

    # ------------------------------------------------------------------
    # Preview rendering
    # ------------------------------------------------------------------

    def _refresh_preview(self):
        mp4 = self._mp4_path.get()
        if not mp4 or not os.path.isfile(mp4):
            messagebox.showwarning('Preview', 'Select a valid MP4 file first.',
                                   parent=self)
            return
        if not self._parse_done:
            messagebox.showwarning('Preview', 'Wait for SRT parsing to finish.',
                                   parent=self)
            return
        self._status('Rendering preview…')
        cfg = self._make_hud_config()
        threading.Thread(target=self._preview_worker,
                         args=(mp4, cfg), daemon=True).start()

    def _preview_worker(self, mp4: str, cfg):
        arr = self.controller.get_preview_frame(mp4, cfg, target_w=640)
        self._ui_queue.put(('preview', arr))

    def _show_preview(self, arr):
        if arr is None:
            # arr is None — check hud_exporter for the real exception
            import traceback
            self._status(f'Preview failed — see terminal for details')
            return
        try:
            from PIL import Image, ImageTk
            img   = Image.fromarray(arr)
            ph_w  = max(200, self._preview_frame.winfo_width())
            ph_h  = max(150, self._preview_frame.winfo_height())
            scale = min(ph_w / img.width, ph_h / img.height, 1.0)
            nw    = max(1, int(img.width  * scale))
            nh    = max(1, int(img.height * scale))
            img   = img.resize((nw, nh), Image.LANCZOS)
            tk_img = ImageTk.PhotoImage(img)

            # Remove old label/image if present
            if hasattr(self, '_prev_img_label'):
                self._prev_img_label.destroy()
            self._preview_label.place_forget()

            self._prev_img_label = tk.Label(self._preview_frame,
                                            image=tk_img, bg=BG2)
            self._prev_img_label.image = tk_img   # keep reference
            self._prev_img_label.place(relx=0.5, rely=0.5, anchor='center')
            self._status('Preview updated.')
        except Exception as e:
            self._status(f'Preview error: {e}')

    # ------------------------------------------------------------------
    # Export
    # ------------------------------------------------------------------

    def _make_hud_config(self):
        from hud_exporter import HudConfig
        return HudConfig(
            show_altitude = self._show_alt.get(),
            show_speed    = self._show_spd.get(),
            show_heading  = self._show_hdg.get(),
            show_vspeed   = self._show_vs.get(),
            show_hag      = self._show_hag.get(),
            speed_unit    = self._speed_unit.get(),
            alt_type      = self._alt_type.get(),
            corner        = self._corner.get(),
            margin_x      = self._margin_x.get(),
            margin_y      = self._margin_y.get(),
            font_size     = self._font_size.get(),
            font_colour   = self._font_col.get().lstrip('#').upper() or 'FFFFFF',
            bg_alpha      = max(0, min(255, self._bg_alpha.get())),
            bold          = self._bold.get(),
        )

    def _start_export(self):
        mp4 = self._mp4_path.get()
        if not mp4 or not os.path.isfile(mp4):
            messagebox.showwarning('Export', 'Select a valid MP4 file first.',
                                   parent=self)
            return
        if not self._parse_done:
            messagebox.showwarning('Export',
                                   'Wait for SRT parsing to complete.',
                                   parent=self)
            return
        if self._exporting:
            return

        base = os.path.splitext(mp4)[0]
        out_path = filedialog.asksaveasfilename(
            parent=self,
            title='Save HUD Video',
            initialfile=os.path.basename(base) + '_hud.mp4',
            defaultextension='.mp4',
            filetypes=[('MP4 files', '*.mp4')]
        )
        if not out_path:
            return

        self._exporting = True
        self._btn_export.config(state='disabled')
        self._btn_preview.config(state='disabled')
        self._status('Exporting — ffmpeg encoding…')
        self._set_pbar(0)
        self._save_settings()
        cfg = self._make_hud_config()
        # Restart the poll loop — macOS native save panel suspends Tk's event
        # loop while open, so the after() chain started in __init__ stops ticking.
        self._polling = True
        self.after(100, self._poll_ui)
        self.controller.export_hud_video(mp4, out_path, cfg)

    # ------------------------------------------------------------------
    # Controller callbacks (fire on background thread → post to queue)
    # ------------------------------------------------------------------

    def _on_parse_progress(self, pct: float):
        self._ui_queue.put(('status', f'Parsing SRT… {pct:.0f}%'))

    def _on_parse_complete(self):
        self._ui_queue.put(('parse_complete', len(self.controller.hud_frames)))

    def _on_export_progress(self, pct: float):
        self._ui_queue.put(('progress', pct))

    def _on_export_complete(self, out_path: str):
        self._ui_queue.put(('export_complete', out_path))

    def _on_export_error(self, msg: str):
        self._ui_queue.put(('export_error', msg))

    # ------------------------------------------------------------------
    # Main-thread UI event pump (called every 100 ms via root.after)
    # ------------------------------------------------------------------

    def _poll_ui(self):
        try:
            while True:
                event, *args = self._ui_queue.get_nowait()
                if event == 'progress':
                    self._set_pbar(args[0])
                elif event == 'status':
                    self._status_var.set(args[0])
                elif event == 'parse_complete':
                    n = args[0]
                    self._parse_done = True
                    self._status_var.set(f'Parsed {n:,} frames.  '
                                         'Click Refresh Preview, then Export Video.')
                    self._btn_preview.config(state='normal')
                    self._btn_export.config(state='normal')
                    if self._mp4_path.get() and os.path.isfile(self._mp4_path.get()):
                        self._refresh_preview()
                elif event == 'export_complete':
                    out_path = args[0]
                    self._exporting = False
                    self._set_pbar(100)
                    self._btn_export.config(state='normal')
                    self._btn_preview.config(state='normal')
                    self._status_var.set(f'Done! → {os.path.basename(out_path)}')
                    if messagebox.askyesno(
                        'Export Complete',
                        f'HUD video saved:\n{out_path}\n\nOpen in Finder/Explorer?',
                        parent=self,
                    ):
                        _reveal_in_finder(out_path)
                elif event == 'export_error':
                    msg = args[0]
                    self._exporting = False
                    self._btn_export.config(state='normal')
                    self._btn_preview.config(state='normal')
                    self._btn_cancel.config(state='normal')
                    if 'cancelled' in msg.lower():
                        self._status_var.set(
                            'Export cancelled.  Close the dialog or start a new export.')
                    else:
                        self._status_var.set(f'Error: {msg[:100]}')
                        messagebox.showerror('Export Error', msg, parent=self)
                elif event == 'preview':
                    self._show_preview(args[0])
        except queue.Empty:
            pass
        if self._polling:
            self.after(100, self._poll_ui)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _status(self, msg: str):
        self._status_var.set(msg)

    def _save_settings(self):
        c = self.config
        c.set('hud_show_altitude', self._show_alt.get())
        c.set('hud_show_speed',    self._show_spd.get())
        c.set('hud_show_heading',  self._show_hdg.get())
        c.set('hud_show_vspeed',   self._show_vs.get())
        c.set('hud_show_hag',      self._show_hag.get())
        c.set('hud_speed_unit',    self._speed_unit.get())
        c.set('hud_alt_type',      self._alt_type.get())
        c.set('hud_corner',        self._corner.get())
        c.set('hud_margin_x',      self._margin_x.get())
        c.set('hud_margin_y',      self._margin_y.get())
        c.set('hud_font_size',     self._font_size.get())
        c.set('hud_font_colour',   self._font_col.get().lstrip('#').upper() or 'FFFFFF')
        c.set('hud_bg_alpha',      self._bg_alpha.get())
        c.set('hud_bold',          self._bold.get())
        c.save()

    def _on_close(self):
        if self._exporting:
            if not messagebox.askyesno(
                'Cancel Export?',
                'Export is in progress.  Cancel it?',
                parent=self,
            ):
                return
            # Signal ffmpeg to die, then update UI immediately so the user
            # sees feedback — the worker thread cleans up asynchronously.
            self._status('Cancelling…')
            self._btn_cancel.config(state='disabled')
            self._btn_export.config(state='disabled')
            self.controller.cancel_hud_export()
            # Do NOT destroy here — let _on_export_error fire and reset
            # state first, then the user can close normally.
            return

        self._polling = False   # stop the poll loop
        self._save_settings()
        self.controller.on_hud_parse_progress  = None
        self.controller.on_hud_parse_complete  = None
        self.controller.on_hud_export_progress = None
        self.controller.on_hud_export_complete = None
        self.controller.on_hud_export_error    = None
        self.destroy()


def _reveal_in_finder(path: str):
    """Open Finder/Explorer to show the exported file."""
    sys = platform.system()
    try:
        if sys == 'Darwin':
            subprocess.Popen(['open', '-R', path])
        elif sys == 'Windows':
            subprocess.Popen(['explorer', '/select,', path])
        else:
            subprocess.Popen(['xdg-open', os.path.dirname(path)])
    except Exception:
        pass
