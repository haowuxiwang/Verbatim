# -*- mode: python ; coding: utf-8 -*-

from pathlib import Path


project_root = Path.cwd()
datas = [('core', 'core')]
if (project_root / 'umi').exists():
    datas.append(('umi', 'umi'))
if (project_root / 'ocr_runtime').exists():
    datas.append(('ocr_runtime', 'ocr_runtime'))

a = Analysis(
    ['main.py'],
    pathex=[],
    binaries=[],
    datas=datas,
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['torch', 'torchvision', 'torchaudio', 'tensorflow', 'paddle', 'cv2', 'sklearn', 'scipy', 'pandas', 'transformers', 'openpyxl', 'sqlalchemy', 'pytest'],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='Verbatim',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
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
    name='Verbatim',
)
