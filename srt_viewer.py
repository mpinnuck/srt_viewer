#!/usr/bin/env python3
"""
srt_viewer.py  –  DJI Air 3S SRT Flight Log Viewer
Entry point.
"""

import tkinter as tk
from config import Config
from controller import Controller
from gui import GUI


def main():
    config     = Config()
    controller = Controller(config)

    root = tk.Tk()
    app  = GUI(root, config, controller)
    root.mainloop()


if __name__ == '__main__':
    main()
