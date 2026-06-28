"""
controller.py  –  ViewModel / Controller for DJI SRT Viewer.

Sits between the GUI and the SRTParser; drives all data operations.

Terrain / HAG
-------------
After an SRT file is loaded, fetch_terrain() fires a background thread that
calls the Open-Meteo elevation API (free, no key required) to obtain terrain
elevation for a 1-in-10 subsample of the flight's GPS track, then
linearly interpolates back to every frame.  This keeps the request well within
the API's rate limit (≤100 coordinates per call).

HAG (Height Above Ground) is calculated datum-independently:

    HAG[i] = rel_alt[i] + (terrain_elevation[home] − terrain_elevation[i])

rel_alt is a pure barometric difference (abs_alt_current − abs_alt_takeoff),
so no geodetic datum is involved.  The terrain term is also a relative
difference, so any fixed offset between the barometric reference and
Open-Meteo's MSL datum cancels out.  For a constant-HAG waypoint mission the
resulting HAG series is flat regardless of terrain.
"""

from __future__ import annotations

import os
import csv
import threading
from typing import List, Optional, Callable

from srt_parser import SRTParser, FlightFrame
from config import Config
from hud_exporter import (
    HudConfig,
    build_hud_frames, write_ass, burn_hud, probe_video, render_preview_frame,
)


class Controller:
    """Mediates between GUI and data layer.

    The GUI calls public methods; the controller fires callbacks back
    to the GUI for async operations.
    """

    def __init__(self, config: Config):
        self.config  = config
        self._parser = SRTParser(stride=config.get('stride', 60))
        self.frames:   List[FlightFrame] = []
        self.stats:    dict = {}
        self.filepath: str = ''
        self.terrain_elevations: Optional[List[float]] = None
        self.terrain_state: str = 'idle'   # 'idle' | 'fetching' | 'ready' | 'error'

        # Callbacks registered by the GUI
        self.on_load_progress:  Optional[Callable[[float], None]] = None
        self.on_load_complete:  Optional[Callable[[], None]]      = None
        self.on_load_error:     Optional[Callable[[str], None]]   = None
        self.on_terrain_ready:  Optional[Callable[[], None]]      = None
        self.on_terrain_error:  Optional[Callable[[str], None]]   = None

        # HUD export state
        self.hud_frames: list = []
        self.hud_cancel = threading.Event()
        self.on_hud_parse_progress:  Optional[Callable[[float], None]] = None
        self.on_hud_parse_complete:  Optional[Callable[[], None]]      = None
        self.on_hud_export_progress: Optional[Callable[[float], None]] = None
        self.on_hud_export_complete: Optional[Callable[[str],  None]]  = None
        self.on_hud_export_error:    Optional[Callable[[str],  None]]  = None

    # ------------------------------------------------------------------
    # File loading (async)
    # ------------------------------------------------------------------

    def load_file(self, filepath: str):
        """Load an SRT file in a background thread."""
        self.filepath = filepath
        self.config.set('last_srt_dir', os.path.dirname(filepath))
        self.config.save()

        thread = threading.Thread(target=self._load_worker,
                                  args=(filepath,), daemon=True)
        thread.start()

    def _load_worker(self, filepath: str):
        try:
            self.terrain_elevations = None   # clear stale terrain on new file
            self.terrain_state      = 'idle'
            self._parser.stride = self.config.get('stride', 60)
            self.frames = self._parser.parse(
                filepath,
                progress_callback=self._progress_cb
            )
            self.stats = SRTParser.stats(self.frames)
            if self.on_load_complete:
                self.on_load_complete()
        except Exception as e:
            if self.on_load_error:
                self.on_load_error(str(e))

    def _progress_cb(self, pct: float):
        if self.on_load_progress:
            self.on_load_progress(pct)

    # ------------------------------------------------------------------
    # Data access helpers
    # ------------------------------------------------------------------

    @property
    def loaded(self) -> bool:
        return bool(self.frames)

    def gps_track(self):
        """Return list of (lat, lon) tuples for the flight path."""
        return [(f.latitude, f.longitude) for f in self.frames]

    def altitude_series(self):
        """Return (times_s, altitudes_m) using configured alt type."""
        alt_type = self.config.get('alt_type', 'rel')
        times = [(f.timestamp - self.frames[0].timestamp).total_seconds()
                 for f in self.frames]
        alts  = [f.rel_alt if alt_type == 'rel' else f.abs_alt
                 for f in self.frames]
        return times, alts

    def fetch_terrain(self):
        """Start a background thread to fetch terrain elevation for all GPS points."""
        self.terrain_state = 'fetching'
        threading.Thread(target=self._terrain_worker, daemon=True).start()

    def _terrain_worker(self):
        try:
            import requests
            import numpy as np

            # Sample every 10th frame — terrain changes slowly (1 batch, no rate limiting)
            STRIDE = 10
            sampled_indices = list(range(0, len(self.frames), STRIDE))
            if sampled_indices[-1] != len(self.frames) - 1:
                sampled_indices.append(len(self.frames) - 1)
            sampled = [self.frames[i] for i in sampled_indices]

            resp = requests.get(
                'https://api.open-meteo.com/v1/elevation',
                params={
                    'latitude':  ','.join(f'{f.latitude:.6f}'  for f in sampled),
                    'longitude': ','.join(f'{f.longitude:.6f}' for f in sampled),
                },
                timeout=20,
            )
            resp.raise_for_status()
            sampled_elevs = resp.json()['elevation']

            # Interpolate back to full frame count
            all_indices = list(range(len(self.frames)))
            self.terrain_elevations = list(
                np.interp(all_indices, sampled_indices, sampled_elevs)
            )
            self.terrain_state = 'ready'
            if self.on_terrain_ready:
                self.on_terrain_ready()
        except Exception as e:
            self.terrain_state = 'error'
            if self.on_terrain_error:
                self.on_terrain_error(str(e))

    def hag_series(self):
        """Return (times_s, hag_m).

        Returns an empty hag list if terrain data has not yet been fetched.
        """
        times = [(f.timestamp - self.frames[0].timestamp).total_seconds()
                 for f in self.frames]
        if not self.terrain_elevations or len(self.terrain_elevations) != len(self.frames):
            return times, []
        terrain_home = self.terrain_elevations[0]
        hags = [f.rel_alt - (elev - terrain_home)
                for f, elev in zip(self.frames, self.terrain_elevations)]
        return times, hags

    def speed_series(self):
        """Return (times_s, speeds) in configured unit."""
        unit  = self.config.get('speed_unit', 'kmh')
        times = [(f.timestamp - self.frames[0].timestamp).total_seconds()
                 for f in self.frames]
        speeds = [f.speed_kmh if unit == 'kmh' else f.speed_ms
                  for f in self.frames]
        return times, speeds

    def summary_text(self) -> str:
        """Human-readable flight summary."""
        if not self.stats:
            return 'No data loaded.'
        s = self.stats
        dur_m = int(s['duration_s'] // 60)
        dur_s = int(s['duration_s'] % 60)
        dist_km = s['total_dist_m'] / 1000

        lines = [
            f"File:          {os.path.basename(self.filepath)}",
            f"Date/Time:     {s['start_time'].strftime('%Y-%m-%d  %H:%M:%S')}",
            f"Duration:      {dur_m}m {dur_s}s",
            f"Total frames:  {s['frame_count']:,}",
            f"Sampled:       {s['sample_count']:,} points (1/sec)",
            f"Distance:      {dist_km:.2f} km",
            f"Max altitude:  {s['max_abs_alt_m']:.1f} m (abs)" if self.config.get('alt_type', 'rel') == 'abs'
            else f"Max altitude:  {s['max_alt_m']:.1f} m (rel)",
            f"Max speed:     {s['max_speed_kmh']:.1f} km/h",
            f"Avg speed:     {s['avg_speed_kmh']:.1f} km/h",
            f"Home point:    {s['home_lat']:.6f}, {s['home_lon']:.6f}",
        ]
        return '\n'.join(lines)

    def export_csv(self, out_path: str):
        """Export parsed frames to CSV."""
        if not self.frames:
            raise RuntimeError('No data to export.')
        with open(out_path, 'w', newline='') as f:
            writer = csv.writer(f)
            writer.writerow([
                'frame', 'timestamp', 'video_time_s',
                'latitude', 'longitude', 'rel_alt_m', 'abs_alt_m',
                'speed_ms', 'speed_kmh',
                'iso', 'shutter', 'fnum', 'focal_len', 'color_temp'
            ])
            for fr in self.frames:
                writer.writerow([
                    fr.frame_num,
                    fr.timestamp.strftime('%Y-%m-%d %H:%M:%S.%f'),
                    f'{fr.video_time:.3f}',
                    f'{fr.latitude:.6f}',
                    f'{fr.longitude:.6f}',
                    f'{fr.rel_alt:.2f}',
                    f'{fr.abs_alt:.2f}',
                    f'{fr.speed_ms:.2f}',
                    f'{fr.speed_kmh:.2f}',
                    fr.iso,
                    fr.shutter,
                    f'{fr.fnum:.1f}',
                    f'{fr.focal_len:.1f}',
                    fr.color_temp,
                ])

    def export_kml(self, out_path: str):
        """Export GPS track to KML for Google Earth."""
        if not self.frames:
            raise RuntimeError('No data to export.')
        home = self.frames[0]
        with open(out_path, 'w') as f:
            f.write('<?xml version="1.0" encoding="UTF-8"?>\n')
            f.write('<kml xmlns="http://www.opengis.net/kml/2.2">\n<Document>\n')
            f.write(f'  <name>{os.path.basename(self.filepath)}</name>\n')
            f.write('  <Style id="track"><LineStyle><color>ff00aaff</color><width>4</width></LineStyle></Style>\n')
            f.write('  <Style id="home"><IconStyle><color>ff00ff00</color><scale>1.2</scale></IconStyle></Style>\n')
            f.write('  <Placemark><name>Flight Path</name><styleUrl>#track</styleUrl>\n')
            f.write('    <LineString><tessellate>1</tessellate><altitudeMode>clampToGround</altitudeMode>\n')
            f.write('      <coordinates>\n')
            for fr in self.frames:
                f.write(f'        {fr.longitude:.6f},{fr.latitude:.6f},0\n')
            f.write('      </coordinates></LineString></Placemark>\n')
            f.write('  <Placemark><name>Home</name><styleUrl>#home</styleUrl>\n')
            f.write(f'    <Point><coordinates>{home.longitude:.6f},{home.latitude:.6f},0</coordinates></Point>\n')
            f.write('  </Placemark>\n</Document>\n</kml>\n')


    # ------------------------------------------------------------------
    # HUD export
    # ------------------------------------------------------------------

    def build_hud_frames_async(self):
        """Re-parse SRT at stride=1 in background to get full-res HUD data."""
        if not self.filepath:
            return
        self.hud_frames = []
        thread = threading.Thread(target=self._hud_parse_worker, daemon=True)
        thread.start()

    def _hud_parse_worker(self):
        try:
            self.hud_frames = build_hud_frames(
                self.filepath,
                progress_cb=self.on_hud_parse_progress,
                terrain_elevations=self.terrain_elevations,
            )
            if self.on_hud_parse_complete:
                self.on_hud_parse_complete()
        except Exception as e:
            if self.on_hud_export_error:
                import subprocess as _sp
                if isinstance(e, _sp.CalledProcessError) and e.stderr:
                    self.on_hud_export_error(f'{e}\n\nffmpeg output:\n{e.stderr[-2000:]}')
                else:
                    self.on_hud_export_error(str(e))

    def export_hud_video(self, mp4_path: str, out_path: str, hud_cfg: HudConfig, quality: str = 'hq'):
        """Burn HUD overlay into mp4_path → out_path in a background thread."""
        self.hud_cancel.clear()
        thread = threading.Thread(
            target=self._hud_export_worker,
            args=(mp4_path, out_path, hud_cfg, quality),
            daemon=True,
        )
        thread.start()

    def _hud_export_worker(self, mp4_path: str, out_path: str, hud_cfg: HudConfig, quality: str = 'hq'):
        try:
            # Probe video dimensions for correct ASS PlayResX/Y
            vid_w, vid_h, _ = probe_video(mp4_path)

            # Write temporary ASS file
            fd, ass_path = __import__('tempfile').mkstemp(suffix='.ass',
                                                           prefix='dji_hud_')
            __import__('os').close(fd)
            try:
                write_ass(self.hud_frames, hud_cfg, ass_path, vid_w, vid_h)
                burn_hud(
                    mp4_path, ass_path, out_path,
                    progress_cb=self.on_hud_export_progress,
                    cancel_event=self.hud_cancel,
                    total_frames=len(self.hud_frames) or None,
                    quality=quality,
                )
            finally:
                try:
                    __import__('os').unlink(ass_path)
                except OSError:
                    pass

            if self.on_hud_export_complete:
                self.on_hud_export_complete(out_path)
        except Exception as e:
            import sys, subprocess as _sp
            # Suppress noisy print for normal cancellation
            if not isinstance(e, RuntimeError) or 'cancelled' not in str(e).lower():
                stderr_detail = ''
                if isinstance(e, _sp.CalledProcessError) and e.stderr:
                    stderr_detail = e.stderr[-3000:]
                print(f'[hud export] EXCEPTION: {type(e).__name__}: {e}', file=sys.stderr)
                if stderr_detail:
                    print(f'[hud export] ffmpeg stderr:\n{stderr_detail}', file=sys.stderr)
            if self.on_hud_export_error:
                self.on_hud_export_error(str(e))

    def cancel_hud_export(self):
        """Signal the running ffmpeg process to stop."""
        self.hud_cancel.set()

    def get_preview_frame(self, mp4_path: str, hud_cfg: HudConfig,
                           target_w: int = 800):
        """Return a numpy image array for the HUD preview (midpoint frame)."""
        if not self.hud_frames:
            return None
        mid = self.hud_frames[len(self.hud_frames) // 2]
        return render_preview_frame(mp4_path, mid, hud_cfg, target_w)
