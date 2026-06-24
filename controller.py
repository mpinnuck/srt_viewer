"""
controller.py  –  ViewModel / Controller for DJI SRT Viewer.

Sits between the GUI and the SRTParser; drives all data operations.
"""

from __future__ import annotations

import os
import csv
import threading
from typing import List, Optional, Callable

from srt_parser import SRTParser, FlightFrame
from config import Config


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

        # Callbacks registered by the GUI
        self.on_load_progress:  Optional[Callable[[float], None]] = None
        self.on_load_complete:  Optional[Callable[[], None]]      = None
        self.on_load_error:     Optional[Callable[[str], None]]   = None

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
            f"Max altitude:  {s['max_alt_m']:.1f} m (rel)",
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
        with open(out_path, 'w') as f:
            f.write('<?xml version="1.0" encoding="UTF-8"?>\n')
            f.write('<kml xmlns="http://www.opengis.net/kml/2.2">\n')
            f.write('<Document>\n')
            f.write(f'  <name>{os.path.basename(self.filepath)}</name>\n')
            f.write('  <Placemark>\n')
            f.write('    <name>Flight Path</name>\n')
            f.write('    <LineString>\n')
            f.write('      <altitudeMode>absolute</altitudeMode>\n')
            f.write('      <coordinates>\n')
            for fr in self.frames:
                f.write(f'        {fr.longitude:.6f},{fr.latitude:.6f},{fr.abs_alt:.2f}\n')
            f.write('      </coordinates>\n')
            f.write('    </LineString>\n')
            f.write('  </Placemark>\n')
            # Home point marker
            home = self.frames[0]
            f.write('  <Placemark>\n')
            f.write('    <name>Home</name>\n')
            f.write(f'    <Point><coordinates>{home.longitude:.6f},{home.latitude:.6f},0</coordinates></Point>\n')
            f.write('  </Placemark>\n')
            f.write('</Document>\n</kml>\n')
