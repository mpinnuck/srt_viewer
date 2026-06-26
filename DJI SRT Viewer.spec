# -*- mode: python ; coding: utf-8 -*-
import platform
target = platform.machine()  # 'arm64' on Apple Silicon, 'x86_64' on Intel

a = Analysis(
    ['srt_viewer.py'],
    pathex=[],
    binaries=[],
    datas=[],
    hiddenimports=['mercantile', 'requests', 'PIL'],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
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
    target_arch=target,
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
