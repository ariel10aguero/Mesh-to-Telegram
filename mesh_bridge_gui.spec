# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for mesh_bridge_gui (tkinter + meshtastic + telebot)."""

from PyInstaller.utils.hooks import collect_all

datas, binaries, hiddenimports = collect_all("meshtastic")

block_cipher = None

a = Analysis(
    ["mesh_bridge_gui.py"],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=list(hiddenimports),
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
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name="MeshTelegramBridge",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
