#!/usr/bin/env python3
"""
srt_viewer.py  –  DJI Air 3S SRT Flight Log Viewer
Entry point.

Build commands
source .venv/bin/activate
.venv/bin/pyinstaller --clean --noconfirm "DJI SRT Viewer.spec"
cp -r "dist/DJI SRT Viewer.app" /Applications/
"""

import os
import sys

# Ensure Homebrew binaries (ffmpeg, etc.) are on PATH when launched
# from Spotlight or Finder, which inherit a minimal environment.
for _p in ('/opt/homebrew/bin', '/usr/local/bin'):
    if _p not in os.environ.get('PATH', '') and os.path.isdir(_p):
        os.environ['PATH'] = _p + ':' + os.environ.get('PATH', '')

import tkinter as tk
from config import Config
from controller import Controller
from gui import GUI


def main():
    config     = Config()
    controller = Controller(config)

    root = tk.Tk()
    GUI(root, config, controller)
    root.mainloop()


if __name__ == '__main__':
    main()
