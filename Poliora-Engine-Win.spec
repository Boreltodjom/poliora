# -*- mode: python ; coding: utf-8 -*-

# pywebview selects the Windows renderer dynamically, so those modules must be explicit.
a = Analysis(
    ['poliora\\app_launcher.py'],
    pathex=[],
    binaries=[],
    datas=[],
    hiddenimports=[
        'webview.platforms.edgechromium',
        'webview.platforms.mshtml',
        'webview.platforms.winforms',
        'clr',
        'clr_loader',
    ],
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
    a.binaries,
    a.datas,
    [],
    name='Poliora',
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
    icon='assets\\poliora.ico',
)