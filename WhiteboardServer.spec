# -*- mode: python ; coding: utf-8 -*-
# PyInstaller specification file for LAN Whiteboard Server

import sys
from PyInstaller.utils.hooks import collect_submodules

block_cipher = None

a = Analysis(
    ['server_main.py'],
    pathex=['.'],
    binaries=[],
    datas=[],
    hiddenimports=[
        'config',
        'protocol',
        'protocol.framer',
        'protocol.messages',
        'network',
        'network.discovery',
        'network.tcp_server',
        'network.tcp_client',
        'state',
        'state.action',
        'state.whiteboard_state',
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
    name='WhiteboardServer',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,  # Interactive console UI for server management
    disable_windowed_traceback=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
