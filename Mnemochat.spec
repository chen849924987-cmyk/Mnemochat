# -*- mode: python ; coding: utf-8 -*-
from PyInstaller.utils.hooks import collect_data_files

datas = [('memory/schema.sql', 'memory'), ('memory/schema_stickers.sql', 'memory'), ('gui/page.html', 'gui'), ('editor/page.html', 'editor'), ('personas', 'personas'), ('config.example.yaml', '.'), ('.env.example', '.'), ('docs', 'docs')]
datas += collect_data_files('jieba')


a = Analysis(
    ['D:\\bot\\Mnemochat\\main.py'],
    pathex=[],
    binaries=[],
    datas=datas,
    hiddenimports=['distro'],
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
    name='Mnemochat',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='Mnemochat',
)
