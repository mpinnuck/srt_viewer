"""
config.py  –  Persistent JSON configuration for DJI SRT Viewer.
"""

import json
import os
from pathlib import Path


_DEFAULT_CONFIG = {
    "last_srt_dir":    "",
    "window_width":    1400,
    "window_height":   860,
    "stride":          60,        # parse every Nth frame (60 = 1/sec at 60fps)
    "map_tile_server": "OpenStreetMap",
    "speed_unit":      "kmh",     # "kmh" or "ms"
    "alt_type":        "rel",     # "rel" or "abs"
    "theme":           "dark",

    # HUD export settings
    "hud_show_altitude":  True,
    "hud_show_speed":     True,
    "hud_show_heading":   True,
    "hud_show_vspeed":    False,
    "hud_show_hag":       False,
    "hud_speed_unit":     "kmh",
    "hud_alt_type":       "rel",
    "hud_corner":         "tl",
    "hud_margin_x":       30,
    "hud_margin_y":       30,
    "hud_font_size":      22,
    "hud_font_colour":    "FFFFFF",
    "hud_bg_alpha":       140,
    "hud_bold":           True,
    "hud_last_mp4_dir":   "",
}

_CONFIG_PATH = Path.home() / ".dji_srt_viewer" / "config.json"


class Config:
    """Load/save app configuration from a JSON file."""

    def __init__(self):
        self._data: dict = dict(_DEFAULT_CONFIG)
        self._load()

    # ------------------------------------------------------------------
    def _load(self):
        if _CONFIG_PATH.exists():
            try:
                with open(_CONFIG_PATH, 'r') as f:
                    saved = json.load(f)
                self._data.update(saved)
            except Exception:
                pass  # corrupt config – use defaults

    def save(self):
        _CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(_CONFIG_PATH, 'w') as f:
            json.dump(self._data, f, indent=2)

    # ------------------------------------------------------------------
    def get(self, key: str, default=None):
        return self._data.get(key, default)

    def set(self, key: str, value):
        self._data[key] = value

    def __getitem__(self, key):
        return self._data.get(key)

    def __setitem__(self, key, value):
        self._data[key] = value
