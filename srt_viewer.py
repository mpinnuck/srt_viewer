#!/usr/bin/env python3
"""
srt_viewer.py  –  DJI Air 3S SRT Flight Log Viewer
Entry point.

Build commands
source .venv/bin/activate
.venv/bin/pyinstaller --clean --noconfirm "DJI SRT Viewer.spec"
cp -r "dist/DJI SRT Viewer.app" /Applications/
"""

import threading
import tkinter as tk
from config import Config
from controller import Controller

BG      = '#1e1e2e'
SUBTEXT = '#6c7086'


def main():
    config     = Config()
    controller = Controller(config)

    root = tk.Tk()
    root.title("DJI Air 3S  –  SRT Flight Log Viewer")
    root.configure(bg=BG)

    w = config.get('window_width',  1400)
    h = config.get('window_height', 860)
    sw = root.winfo_screenwidth()
    sh = root.winfo_screenheight()
    root.geometry(f"{w}x{h}+{(sw-w)//2}+{(sh-h)//2}")

    splash = tk.Label(root, text='Loading…', bg=BG, fg=SUBTEXT,
                      font=('SF Pro Display', 13))
    splash.pack(expand=True)
    root.update()

    def _load():
        from gui import GUI
        root.after(0, lambda: _launch(GUI))

    def _launch(GUI):
        splash.destroy()
        GUI(root, config, controller)

    threading.Thread(target=_load, daemon=True).start()
    root.mainloop()


if __name__ == '__main__':
    main()
