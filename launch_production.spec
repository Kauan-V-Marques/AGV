# -*- mode: python ; coding: utf-8 -*-
"""
Spec PyInstaller — AGV Launcher de Produção (raiz do projeto)
Gera: dist/launch_production (abre o servidor + browser automaticamente)

⚠ Coloque o executável dist/iniciar_sistema na mesma pasta que dist/launch_production.

Como usar:
    cd '/home/kauan-linux/Área de trabalho/agv'
    ~/.local/bin/pyinstaller launch_production.spec
"""

a = Analysis(
    ["launch_production.py"],
    pathex=[],
    binaries=[],
    datas=[],
    hiddenimports=[],
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
    name="launch_production",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
