# -*- mode: python ; coding: utf-8 -*-
import subprocess

a = Analysis(
    ['srt_viewer.py'],
    pathex=[],
    binaries=[],
    datas=[],
    hiddenimports=['mercantile', 'requests', 'PIL'],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['psutil'],
    noarchive=False,
    optimize=0,
)

# Drop any binaries that don't match arm64
def _arch(path):
    try:
        return set(subprocess.check_output(
            ['lipo', '-archs', path], stderr=subprocess.DEVNULL
        ).decode().split())
    except Exception:
        return {'arm64'}

a.binaries = [(n, p, k) for n, p, k in a.binaries if 'arm64' in _arch(p)]

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='DJI SRT Viewer',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch='arm64',
    codesign_identity=None,
    entitlements_file=None,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name='DJI SRT Viewer',
)
app = BUNDLE(
    coll,
    name='DJI SRT Viewer.app',
    icon=None,
    bundle_identifier='com.microconcepts.dji-srt-viewer',
)
