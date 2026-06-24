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
        return self._data[key]

    def __setitem__(self, key, value):
        self._data[key] = value
