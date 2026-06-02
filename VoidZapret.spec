# -*- mode: python ; coding: utf-8 -*-
import os
from PyInstaller.utils.hooks import collect_all

# Встраиваем zapret, фронт webui и иконку.
datas = [('zapret', 'zapret'), ('webui', 'webui')]
if os.path.exists('icon.ico'):
    datas.append(('icon.ico', '.'))

binaries = []
# pystray на Windows подгружает бэкенд динамически.
hiddenimports = ['pystray._win32']

# WebView-стек + pydivert (наш движок, тащит WinDivert.dll/.sys).
for pkg in ('webview', 'pythonnet', 'clr_loader', 'pydivert'):
    d, b, h = collect_all(pkg)
    datas += d
    binaries += b
    hiddenimports += h

a = Analysis(
    ['main.py'],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

# onedir (папка с exe + _internal) — надёжнее onefile (нет вырезаемого Defender'ом
# overlay). UPX off — провоцирует ложные срабатывания антивируса.
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='VoidZapret',
    debug=False,
    uac_admin=True,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon='icon.ico' if os.path.exists('icon.ico') else None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name='VoidZapret',
)
