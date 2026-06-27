"""
hud_exporter.py  –  HUD video overlay exporter for DJI SRT Viewer.

Workflow
--------
1.  Re-parse the SRT file at stride=1 (every GPS-change frame) so the
    telemetry matches the video's actual frame rate as closely as possible.
2.  Derive heading (GPS-course bearing) and vertical speed from successive
    frames.
3.  Write a temporary ASS subtitle file carrying one HUD line per block.
4.  Shell out to ffmpeg with the ASS subtitles filter to burn the overlay
    into a copy of the source MP4.

Heading notes
-------------
Heading is the GPS-course bearing (direction of travel) computed from
successive lat/lon pairs, NOT a compass/magnetometer value.  When the
drone is stationary the bearing is undefined; those frames show '---'.

ASS vs SRT
----------
We use ASS (Advanced SubStation Alpha) rather than plain SRT because ASS
supports exact pixel positioning, font size, colour, shadow, and a
semi-transparent background box — all controllable via the Style header.
ffmpeg's 'subtitles' filter renders ASS natively.
"""

from __future__ import annotations

import math
import os
import re
import subprocess
import tempfile
import threading
from dataclasses import dataclass
from datetime import datetime
from typing import Callable, List, Optional, Tuple

from srt_parser import SRTParser, FlightFrame, _haversine


# ---------------------------------------------------------------------------
# HUD configuration dataclass
# ---------------------------------------------------------------------------

@dataclass
class HudConfig:
    """All user-adjustable HUD parameters."""
    # Field selection
    show_altitude:  bool = True
    show_speed:     bool = True
    show_heading:   bool = True
    show_vspeed:    bool = False   # vertical speed — disabled by default
    show_hag:       bool = False   # height above ground — requires terrain data

    # Units
    speed_unit:     str  = 'kmh'   # 'kmh' or 'ms'
    alt_type:       str  = 'rel'   # 'rel' or 'abs'

    # Position — pixel offsets from the chosen corner
    corner:         str  = 'tl'    # 'tl' | 'tr' | 'bl' | 'br'
    margin_x:       int  = 30
    margin_y:       int  = 30

    # Appearance
    font_size:      int  = 22
    font_colour:    str  = 'FFFFFF'   # hex RGB (no #)
    bg_alpha:       int  = 100        # 0=transparent … 255=opaque  (ASS uses hex)
    bold:           bool = True

    # Label prefixes
    label_alt:      str  = 'ALT'
    label_hag:      str  = 'HAG'
    label_speed:    str  = 'SPD'
    label_heading:  str  = 'HDG'
    label_vspeed:   str  = 'V/S'


# ---------------------------------------------------------------------------
# Heading + vertical-speed derivation
# ---------------------------------------------------------------------------

def _bearing(lat1: float, lon1: float, lat2: float, lon2: float) -> Optional[float]:
    """True bearing in degrees [0, 360) from point 1 → point 2.
    Returns None if the points are too close to give a meaningful bearing.
    """
    MIN_DIST_M = 0.5   # ignore displacements smaller than 0.5 m
    if _haversine(lat1, lon1, lat2, lon2) < MIN_DIST_M:
        return None
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    dl   = math.radians(lon2 - lon1)
    x = math.sin(dl) * math.cos(phi2)
    y = math.cos(phi1) * math.sin(phi2) - math.sin(phi1) * math.cos(phi2) * math.cos(dl)
    return (math.degrees(math.atan2(x, y)) + 360) % 360


def _bearing_to_compass(deg: float) -> str:
    """Convert a bearing in degrees to an 8-point compass label."""
    pts = ['N', 'NE', 'E', 'SE', 'S', 'SW', 'W', 'NW']
    idx = int((deg + 22.5) / 45) % 8
    return pts[idx]


@dataclass
class HudFrame:
    """Per-frame data ready for HUD rendering."""
    video_time: float          # seconds from video start
    rel_alt:    float
    abs_alt:    float
    speed_ms:   float
    speed_kmh:  float
    heading:    Optional[float]   # None when stationary
    vspeed_ms:  float             # m/s positive=up
    hag_m:      Optional[float]   # height above ground (None if no terrain data)


def build_hud_frames(srt_path: str,
                     progress_cb: Optional[Callable[[float], None]] = None,
                     terrain_elevations: Optional[List[float]] = None
                     ) -> List[HudFrame]:
    """Re-parse the SRT at full GPS-change resolution and derive HUD fields.

    terrain_elevations: optional list of terrain elevation (m ASL) at each
    frame's lat/lon, as returned by the Open-Meteo API.  When provided, HAG
    is computed as: rel_alt - (terrain[i] - terrain[0]).
    """
    parser = SRTParser(stride=1)   # every GPS change event
    raw: List[FlightFrame] = parser.parse(srt_path, progress_callback=progress_cb)

    hud: List[HudFrame] = []
    for idx, fr in enumerate(raw):
        # Heading: forward-looking bearing (current→next) gives smoother result
        hdg: Optional[float] = None
        if idx + 1 < len(raw):
            hdg = _bearing(fr.latitude, fr.longitude,
                           raw[idx + 1].latitude, raw[idx + 1].longitude)
        elif idx > 0:
            hdg = _bearing(raw[idx - 1].latitude, raw[idx - 1].longitude,
                           fr.latitude, fr.longitude)

        # Vertical speed: Δalt / Δt
        vspeed = 0.0
        if idx > 0:
            dt = (fr.timestamp - raw[idx - 1].timestamp).total_seconds()
            if dt > 0.05:
                vspeed = (fr.rel_alt - raw[idx - 1].rel_alt) / dt

        # HAG: height above ground using terrain elevation at stride=1 index.
        # terrain_elevations is aligned to the main app's stride=60 frames,
        # not our stride=1 re-parse, so we interpolate by nearest index.
        hag: Optional[float] = None
        if terrain_elevations and len(terrain_elevations) > 1:
            # Map stride=1 index to nearest terrain index
            t_idx = min(int(idx * len(terrain_elevations) / max(len(raw), 1)),
                        len(terrain_elevations) - 1)
            terrain_home = terrain_elevations[0]
            hag = fr.rel_alt - (terrain_elevations[t_idx] - terrain_home)

        hud.append(HudFrame(
            video_time = fr.video_time,
            rel_alt    = fr.rel_alt,
            abs_alt    = fr.abs_alt,
            speed_ms   = fr.speed_ms,
            speed_kmh  = fr.speed_kmh,
            heading    = hdg,
            vspeed_ms  = vspeed,
            hag_m      = hag,
        ))
    return hud


# ---------------------------------------------------------------------------
# ASS subtitle generation
# ---------------------------------------------------------------------------

def _ms_to_ass(seconds: float) -> str:
    """Convert a float seconds value to ASS timestamp  H:MM:SS.cc"""
    s   = int(seconds)
    cs  = int(round((seconds - s) * 100))
    h   = s // 3600
    m   = (s % 3600) // 60
    s   = s % 60
    return f'{h}:{m:02d}:{s:02d}.{cs:02d}'


def _hud_line(hf: HudFrame, cfg: HudConfig) -> str:
    """Format one line of HUD text for a frame.

    Fixed-width fields prevent wobble as digit counts change:
      Altitude : 4 chars, no decimal, space-padded (leading zeros blank)
      Speed    : 2 chars, no decimal, space-padded
      Bearing  : 3 chars, no decimal, space-padded
    """
    parts = []
    if cfg.show_altitude:
        alt = hf.abs_alt if cfg.alt_type == 'abs' else hf.rel_alt
        parts.append(f'{cfg.label_alt} {int(alt):4d}m')
    if cfg.show_speed:
        if cfg.speed_unit == 'kmh':
            parts.append(f'{cfg.label_speed} {min(int(hf.speed_kmh), 99):2d}km/h')
        else:
            parts.append(f'{cfg.label_speed} {min(int(hf.speed_ms), 99):2d}m/s')
    if cfg.show_heading:
        if hf.heading is not None:
            compass = _bearing_to_compass(hf.heading)
            parts.append(f'{cfg.label_heading} {min(int(hf.heading), 359):3d}\u00b0 {compass}')
        else:
            parts.append(f'{cfg.label_heading}  ---  ---')
    if cfg.show_vspeed:
        sign = '+' if hf.vspeed_ms >= 0 else '-'
        parts.append(f'{cfg.label_vspeed} {sign}{int(abs(hf.vspeed_ms)):2d}m/s')
    if cfg.show_hag:
        if hf.hag_m is not None:
            parts.append(f'{cfg.label_hag} {int(hf.hag_m):4d}m')
        else:
            parts.append(f'{cfg.label_hag}  ---m')
    return '  '.join(parts)


def _ass_alignment(corner: str) -> int:
    """Map corner name to ASS alignment integer (numpad layout)."""
    return {'tl': 7, 'tr': 9, 'bl': 1, 'br': 3}.get(corner, 7)


def _ass_position(corner: str, mx: int, my: int, vid_w: int, vid_h: int) -> Tuple[int, int]:
    """Pixel position for the ASS \\pos() tag."""
    if corner == 'tl':
        return mx, my
    elif corner == 'tr':
        return vid_w - mx, my
    elif corner == 'bl':
        return mx, vid_h - my
    else:  # br
        return vid_w - mx, vid_h - my


def write_ass(hud_frames: List[HudFrame],
              cfg: HudConfig,
              out_path: str,
              vid_w: int = 3840,
              vid_h: int = 2160):
    """Write an ASS subtitle file with HUD overlay events."""

    alignment = _ass_alignment(cfg.corner)
    px, py    = _ass_position(cfg.corner, cfg.margin_x, cfg.margin_y, vid_w, vid_h)
    bold_i    = '1' if cfg.bold else '0'

    # ASS alpha is inverted (0x00 = opaque, 0xFF = transparent)
    bg_ass_alpha = 255 - cfg.bg_alpha
    bg_alpha_hex = f'{bg_ass_alpha:02X}'

    header = f"""\
[Script Info]
ScriptType: v4.00+
PlayResX: {vid_w}
PlayResY: {vid_h}
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: HUD,Courier New,{cfg.font_size},&H00{cfg.font_colour},&H000000FF,&H00000000,&H{bg_alpha_hex}000000,{bold_i},0,0,0,100,100,0,0,3,0,0,{alignment},0,0,0,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""

    lines = [header]
    pos_tag = f'{{\\pos({px},{py})}}'

    for i, hf in enumerate(hud_frames):
        start = _ms_to_ass(hf.video_time)
        # End = next frame's start time (or +0.1s for last frame)
        if i + 1 < len(hud_frames):
            end = _ms_to_ass(hud_frames[i + 1].video_time)
        else:
            end = _ms_to_ass(hf.video_time + 0.1)

        text = _hud_line(hf, cfg)
        lines.append(f'Dialogue: 0,{start},{end},HUD,,0,0,0,,{pos_tag}{text}\n')

    with open(out_path, 'w', encoding='utf-8') as fh:
        fh.writelines(lines)


# ---------------------------------------------------------------------------
# ffmpeg burn-in
# ---------------------------------------------------------------------------

def probe_video(mp4_path: str) -> Tuple[int, int, float]:
    """Return (width, height, duration_s) for an MP4 using ffprobe.

    DJI MP4s sometimes express 'duration' as a rational string like
    '46800/1000' rather than a plain float, so we handle both forms.
    Falls back to reading 'duration' from the format section if the
    video stream omits it.
    """
    import json

    def _parse_dur(val) -> float:
        if val is None:
            return 0.0
        s = str(val)
        if '/' in s:
            num, den = s.split('/', 1)
            return float(num) / float(den) if float(den) else 0.0
        try:
            return float(s)
        except ValueError:
            return 0.0

    # Ask ffprobe for both streams AND format so we have a duration fallback
    cmd = [
        'ffprobe', '-v', 'quiet',
        '-print_format', 'json',
        '-show_streams', '-show_format',
        mp4_path,
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        return 3840, 2160, 0.0
    try:
        data = json.loads(result.stdout)
    except json.JSONDecodeError:
        return 3840, 2160, 0.0

    w, h, dur = 3840, 2160, 0.0
    for stream in data.get('streams', []):
        if stream.get('codec_type') == 'video':
            w   = int(stream.get('width',  3840))
            h   = int(stream.get('height', 2160))
            dur = _parse_dur(stream.get('duration'))
            break

    # If stream duration is missing/zero, fall back to container duration
    if dur <= 0.0:
        dur = _parse_dur(data.get('format', {}).get('duration'))

    return w, h, dur


def burn_hud(
    mp4_path: str,
    ass_path: str,
    out_path: str,
    progress_cb: Optional[Callable[[float], None]] = None,
    cancel_event: Optional[threading.Event] = None,
    total_frames: Optional[int] = None,
) -> None:
    """Run ffmpeg to burn ASS subtitles into the video.

    Design notes
    ------------
    * stderr is redirected to a background drain thread so its pipe buffer
      never fills and blocks ffmpeg (the root cause of the progress-bar hang
      on long 4K encodes).
    * -progress pipe:2 sends key=value progress lines to stderr, read via
      read line-by-line on the calling thread.
    * The subtitles filter path must NOT be wrapped in shell-style single
      quotes when using list-form Popen — they would be passed literally to
      ffmpeg and cause a file-not-found error on macOS/Linux.
    * ASS files are written to the OS temp directory, which never contains
      colons or backslashes, so no path escaping is needed.

    Raises subprocess.CalledProcessError on ffmpeg failure.
    Raises RuntimeError if cancelled.
    """
    _, _, duration_s = probe_video(mp4_path)

    # Build the subtitles filter value — no shell quoting; this is a list arg.
    # On Windows, backslashes in the path must be doubled for the lavfi parser.
    import platform as _platform
    if _platform.system() == 'Windows':
        safe_ass = ass_path.replace('\\', '/').replace(':', '\\:')
    else:
        safe_ass = ass_path  # temp path is always safe on macOS/Linux

    vf_filter = f'subtitles={safe_ass},format=yuv420p'

    cmd = [
        'ffmpeg', '-y',
        '-i', mp4_path,
        '-vf', vf_filter,
        '-c:v', 'h264_videotoolbox',
        '-b:v', '60M',
        '-realtime', '0',
        '-c:a', 'copy',
        out_path,
    ]

    # Two separate pipes — stdout discarded, stderr read for progress + errors.
    # Do NOT use -progress pipe:N; ffmpeg writes progress to stderr by default
    # when stderr is a pipe, which is what we want.
    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        universal_newlines=True,
    )

    # Cancel monitor — SIGKILL, polled every 250 ms
    _cancelled = threading.Event()

    def _watch_cancel():
        while proc.poll() is None:
            if cancel_event and cancel_event.is_set():
                _cancelled.set()
                try:
                    proc.kill()
                except OSError:
                    pass
                return
            threading.Event().wait(0.25)

    cancel_thread = threading.Thread(target=_watch_cancel, daemon=True)
    cancel_thread.start()

    # Read stderr — contains both ffmpeg progress stats and error messages.
    # ffmpeg writes "frame=N fps=N ... time=HH:MM:SS.ss ..." to stderr.
    time_pat  = re.compile(r'time=(\d+):(\d+):(\d+\.\d+)')
    _last_pct = -1.0
    _stderr_lines: list = []
    while True:
        line = proc.stderr.readline()
        if line == '' and proc.poll() is not None:
            break
        if line:
            _stderr_lines.append(line)
        m = time_pat.search(line)
        if m and progress_cb and duration_s > 0:
            h, mn, s = int(m.group(1)), int(m.group(2)), float(m.group(3))
            elapsed = h * 3600 + mn * 60 + s
            pct = min(99.0, elapsed / duration_s * 100)
            if pct > _last_pct:
                _last_pct = pct
                progress_cb(pct)

    proc.wait()
    cancel_thread.join(timeout=1)

    if _cancelled.is_set():
        try:
            import os as _os
            _os.unlink(out_path)
        except OSError:
            pass
        raise RuntimeError('Export cancelled.')

    if proc.returncode != 0:
        stderr_text = ''.join(_stderr_lines[-40:])
        raise subprocess.CalledProcessError(proc.returncode, cmd,
                                            stderr=stderr_text)

    if progress_cb:
        progress_cb(100.0)


# ---------------------------------------------------------------------------
# Preview frame rendering (PIL → numpy for matplotlib)
# ---------------------------------------------------------------------------

def render_preview_frame(
    mp4_path: str,
    hf: HudFrame,
    cfg: HudConfig,
    target_w: int = 800,
) -> Optional['numpy.ndarray']:  # type: ignore[name-defined]
    """Extract one video frame and composite a HUD text overlay using PIL.

    Returns an H×W×3 uint8 numpy array suitable for matplotlib imshow,
    or None if extraction fails.
    """
    try:
        import numpy as np
        from PIL import Image, ImageDraw, ImageFont

        # ------ extract frame with ffmpeg ------
        fd, png_path = tempfile.mkstemp(suffix='.png')
        os.close(fd)
        try:
            ts = max(0.0, hf.video_time)
            subprocess.run(
                ['ffmpeg', '-y', '-ss', str(ts),
                 '-i', mp4_path,
                 '-vframes', '1',
                 '-q:v', '2',
                 png_path],
                capture_output=True, check=True
            )
            img = Image.open(png_path).convert('RGB')
        finally:
            try:
                os.unlink(png_path)
            except OSError:
                pass

        vid_w, vid_h = img.size

        # ------ scale for preview ------
        scale = target_w / vid_w
        prev_w = target_w
        prev_h = int(vid_h * scale)
        img    = img.resize((prev_w, prev_h), Image.LANCZOS)
        mx     = int(cfg.margin_x * scale)
        my     = int(cfg.margin_y * scale)
        fs     = max(10, int(cfg.font_size * scale))

        # ------ draw HUD ------
        draw = ImageDraw.Draw(img, 'RGBA')

        try:
            font = ImageFont.truetype('/System/Library/Fonts/Courier.ttc', fs)
        except (IOError, OSError):
            try:
                font = ImageFont.truetype('/usr/share/fonts/truetype/liberation/LiberationMono-Bold.ttf', fs)
            except (IOError, OSError):
                font = ImageFont.load_default()

        text = _hud_line(hf, cfg)

        # measure text
        bbox = draw.textbbox((0, 0), text, font=font)
        tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
        pad = int(6 * scale)

        # position
        if cfg.corner == 'tl':
            tx, ty = mx, my
        elif cfg.corner == 'tr':
            tx, ty = prev_w - mx - tw, my
        elif cfg.corner == 'bl':
            tx, ty = mx, prev_h - my - th
        else:
            tx, ty = prev_w - mx - tw, prev_h - my - th

        # background box
        r, g, b = int(cfg.font_colour[0:2], 16), 0, 0   # colour not used for bg
        bg_alpha = cfg.bg_alpha
        draw.rectangle(
            [tx - pad, ty - pad, tx + tw + pad, ty + th + pad],
            fill=(0, 0, 0, bg_alpha)
        )

        # text
        fc = (int(cfg.font_colour[0:2], 16),
              int(cfg.font_colour[2:4], 16),
              int(cfg.font_colour[4:6], 16), 255)
        draw.text((tx, ty), text, font=font, fill=fc)

        return np.array(img.convert('RGB'))

    except Exception:
        return None
