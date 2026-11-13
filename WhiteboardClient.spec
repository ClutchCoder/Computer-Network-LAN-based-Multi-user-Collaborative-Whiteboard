# -*- mode: python ; coding: utf-8 -*-
# PyInstaller specification file for LAN Whiteboard Client GUI

import sys
from PyInstaller.utils.hooks import collect_submodules

block_cipher = None

a = Analysis(
    ['client_main.py'],
    pathex=['.'],
    binaries=[],
    datas=[],
    hiddenimports=[
        'tkinter',
        'tkinter.ttk',
        'tkinter.filedialog',
        'tkinter.messagebox',
        'tkinter.colorchooser',
        'PIL',
        'PIL.Image',
        'PIL.ImageDraw',
        'config',
        'protocol',
        'protocol.framer',
        'protocol.messages',
        'network',
        'network.discovery',
        'network.tcp_client',
        'state',
        'state.action',
        'state.whiteboard_state',
        'gui',
        'gui.main_window',
        'gui.canvas_view',
        'gui.chat_panel',
        'gui.color_palette',
        'gui.connection_dialog',
    ],
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
    name='WhiteboardClient',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,  # GUI application window (no background terminal window)
    disable_windowed_traceback=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
