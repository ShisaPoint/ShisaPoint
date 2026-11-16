# -*- mode: python ; coding: utf-8 -*-


a = Analysis(
    ['desktop_lens.py'],
    pathex=[],
    binaries=[],
    datas=[('icon/*.ico', 'icon')] + __import__('PyInstaller.utils.hooks', fromlist=['x']).collect_data_files('certifi'),
    hiddenimports=['winrt.windows.media.ocr','winrt.windows.graphics.imaging','winrt.windows.storage.streams','winrt.windows.globalization','winrt.windows.foundation','winrt.windows.foundation.collections','winrt.system','winrt.windows.system','winrt.windows.graphics.directx','winrt.runtime','certifi'],
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
    name='ShisaPoint',
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
    icon=['icon/ShisaPoint.ico'],
)
