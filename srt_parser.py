"""
srt_parser.py  –  DJI Air 3S SRT telemetry file parser.

Sampling strategy
-----------------
The SRT file contains one block per video frame (60 fps → ~57 000 blocks for a
15-minute flight).  The GPS receiver inside the drone updates at 10 Hz, so
consecutive frames often carry identical coordinates.

Rather than sampling every Nth *frame* (which risks landing on a frozen-GPS
frame and producing a false zero/double-speed spike pair), the parser counts
GPS *change* events and keeps every stride-th change.  At 10 Hz GPS and
stride=10 this yields ~1 genuine position update per second — the same
effective rate as before, but every sample is guaranteed to have a real
position delta.

Speed calculation
-----------------
Ground speed is derived from consecutive (lat, lon) pairs and the wall-clock
timestamp delta.  Two guards are applied to handle DJI firmware quirks:

* dt ≤ 0.1 s — DJI's SRT writer occasionally resets its timestamp counter at
  a buffer boundary, causing the timestamp to jump backwards by several
  seconds mid-flight.  Any sample pair with a non-positive or implausibly
  small dt is skipped (speed left at 0.0).

* speed > MAX_SPEED_MS — residual outliers from any other cause are clamped to
  0.0 rather than propagated as spikes.  Glitch frames appear as brief dips on
  the speed chart rather than impossible 140 km/h spikes.
"""

import re
import math
from dataclasses import dataclass
from datetime import datetime
from typing import List, Optional


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclass
class FlightFrame:
    frame_num:  int
    timestamp:  datetime
    video_time: float          # seconds from start of video
    latitude:   float
    longitude:  float
    rel_alt:    float          # metres above home point
    abs_alt:    float          # metres ASL
    iso:        int
    shutter:    str            # e.g. "1/640.0"
    fnum:       float
    focal_len:  float
    color_temp: int
    speed_ms:   float = 0.0   # computed after parsing (m/s)
    speed_kmh:  float = 0.0


# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------

_BLOCK_PATTERN = re.compile(
    r'FrameCnt:\s*(\d+),\s*DiffTime:\s*(\d+)ms\s*'
    r'(\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2}\.\d+)\s*'
    r'\[iso:\s*(\d+)\]\s*'
    r'\[shutter:\s*([^\]]+)\]\s*'
    r'\[fnum:\s*([\d.]+)\]\s*'
    r'\[ev:\s*[^\]]+\]\s*'
    r'\[color_md:\s*[^\]]+\]\s*'
    r'\[focal_len:\s*([\d.]+)\]\s*'
    r'\[latitude:\s*([-\d.]+)\]\s*'
    r'\[longitude:\s*([-\d.]+)\]\s*'
    r'\[rel_alt:\s*([-\d.]+)\s+abs_alt:\s*([-\d.]+)\]\s*'
    r'\[ct:\s*(\d+)\]',
    re.DOTALL
)


def _parse_video_time(vtt_start: str) -> float:
    """Convert SRT timestamp '00:01:23,456' to seconds."""
    m = re.match(r'(\d{2}):(\d{2}):(\d{2}),(\d{3})', vtt_start)
    if not m:
        return 0.0
    h, mi, s, ms = int(m.group(1)), int(m.group(2)), int(m.group(3)), int(m.group(4))
    return h * 3600 + mi * 60 + s + ms / 1000.0


def _haversine(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Distance in metres between two GPS points."""
    R = 6_371_000.0
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi   = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = (math.sin(dphi / 2) ** 2
         + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2)
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


class SRTParser:
    """Parse a DJI Air 3S .SRT telemetry file.

    Parameters
    ----------
    stride : int
        Parse every Nth frame.  stride=60 yields ~1 sample/second at 60 fps.
        stride=1 parses every frame (slow for 57 k-frame files).
    """

    def __init__(self, stride: int = 60):
        self.stride = stride

    def parse(self, filepath: str,
              progress_callback=None) -> List[FlightFrame]:
        """Parse the SRT file and return a list of FlightFrame objects.

        progress_callback(pct: float) is called periodically with 0–100.
        """
        with open(filepath, 'r', encoding='utf-8', errors='replace') as fh:
            content = fh.read()

        blocks = re.split(r'\n\n+', content.strip())
        total  = len(blocks)
        frames: List[FlightFrame] = []

        # Count GPS-change events so we subsample on real position updates,
        # not fixed frame indices — avoids frozen-GPS speed spikes.
        last_lat: Optional[float] = None
        last_lon: Optional[float] = None
        gps_change_count = 0

        for i, block in enumerate(blocks):
            m = _BLOCK_PATTERN.search(block)
            if not m:
                continue

            lat = float(m.group(8))
            lon = float(m.group(9))

            # Skip frames where GPS hasn't moved since the last fix
            if lat == last_lat and lon == last_lon:
                continue

            last_lat, last_lon = lat, lon
            gps_change_count += 1

            # Subsample: keep 1 in every stride GPS-change events (~1 Hz)
            if gps_change_count % self.stride != 0:
                continue

            # Parse SRT timestamp from the first line of the block
            vtt_start = ''
            for line in block.splitlines():
                if '-->' in line:
                    vtt_start = line.split('-->')[0].strip()
                    break

            try:
                ts = datetime.strptime(m.group(3), '%Y-%m-%d %H:%M:%S.%f')
            except ValueError:
                ts = datetime.strptime(m.group(3), '%Y-%m-%d %H:%M:%S')

            frame = FlightFrame(
                frame_num  = int(m.group(1)),
                timestamp  = ts,
                video_time = _parse_video_time(vtt_start),
                iso        = int(m.group(4)),
                shutter    = m.group(5).strip(),
                fnum       = float(m.group(6)),
                focal_len  = float(m.group(7)),
                latitude   = lat,
                longitude  = lon,
                rel_alt    = float(m.group(10)),
                abs_alt    = float(m.group(11)),
                color_temp = int(m.group(12)),
            )
            frames.append(frame)

            if progress_callback and i % max(1, total // 100) == 0:
                progress_callback(i / total * 100)

        # Compute speed from successive GPS positions and time deltas.
        # Guard against SRT timestamp glitches (negative or near-zero dt) and
        # physically impossible speeds caused by DJI firmware timestamp resets.
        MAX_SPEED_MS = 25.0   # ~90 km/h — well above Air 3S max of ~19 m/s
        for idx in range(1, len(frames)):
            prev, curr = frames[idx - 1], frames[idx]
            dt = (curr.timestamp - prev.timestamp).total_seconds()
            if dt > 0.1:
                dist = _haversine(prev.latitude, prev.longitude,
                                  curr.latitude, curr.longitude)
                speed_ms = dist / dt
                if speed_ms <= MAX_SPEED_MS:
                    curr.speed_ms  = speed_ms
                    curr.speed_kmh = speed_ms * 3.6
                # else: leave at 0.0 — glitch frame, shows as dip not spike
        if frames:
            frames[0].speed_ms  = 0.0
            frames[0].speed_kmh = 0.0

        if progress_callback:
            progress_callback(100.0)

        return frames


    # ------------------------------------------------------------------
    # Convenience statistics helpers
    # ------------------------------------------------------------------

    @staticmethod
    def stats(frames: List[FlightFrame]) -> dict:
        if not frames:
            return {}
        lats    = [f.latitude  for f in frames]
        lons    = [f.longitude for f in frames]
        alts     = [f.rel_alt for f in frames]
        abs_alts = [f.abs_alt for f in frames]
        speeds  = [f.speed_kmh for f in frames]
        duration = (frames[-1].timestamp - frames[0].timestamp).total_seconds()

        # Total distance (sum of hop distances)
        total_dist = sum(
            _haversine(frames[i-1].latitude, frames[i-1].longitude,
                       frames[i].latitude,   frames[i].longitude)
            for i in range(1, len(frames))
        )

        return {
            'duration_s':    duration,
            'frame_count':   frames[-1].frame_num,
            'sample_count':  len(frames),
            'start_time':    frames[0].timestamp,
            'end_time':      frames[-1].timestamp,
            'max_alt_m':     max(alts),
            'max_abs_alt_m': max(abs_alts),
            'max_speed_kmh': max(speeds),
            'avg_speed_kmh': sum(speeds) / len(speeds),
            'total_dist_m':  total_dist,
            'lat_min':       min(lats),
            'lat_max':       max(lats),
            'lon_min':       min(lons),
            'lon_max':       max(lons),
            'home_lat':      frames[0].latitude,
            'home_lon':      frames[0].longitude,
        }
