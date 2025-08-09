# -*- mode: python ; coding: utf-8 -*-

import os
from pathlib import Path

block_cipher = None

# Optionally include ffmpeg.exe from vendor/ffmpeg/ into the bundled app.
# If present, it will be available at runtime via sys._MEIPASS and discovered by which_ffmpeg().
ffmpeg_vendor = Path('vendor/ffmpeg/ffmpeg.exe')
binaries = []
if ffmpeg_vendor.exists():
    # (source, dest) — in onefile, dest lives in the extraction dir (_MEIPASS)
    binaries.append((str(ffmpeg_vendor), 'ffmpeg.exe'))


a = Analysis(
    ['clipper.py'],
    pathex=['.'],
    binaries=binaries,
    datas=[],
    hiddenimports=['screeninfo', 'psutil', 'keyboard', 'pynput'],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='Clipper',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,  # --noconsole
    disable_windowed_traceback=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    *([] if not binaries else binaries),
    strip=False,
    upx=True,
    upx_exclude=[],
    name='Clipper'
)
