# -*- mode: python ; coding: utf-8 -*-
"""
Spec PyInstaller — AGV Servidor (PC AGV Linux)
Gera um executável único: dist/iniciar_sistema

Como usar:
    cd '/home/kauan-linux/Área de trabalho/agv/pc_agv'
    ~/.local/bin/pyinstaller iniciar_sistema.spec
"""

import glob
import os
import sys
import cv2

# ─── Localiza o .so do freenect dinamicamente ────────────────────────────────
_freenect_so = glob.glob(
    os.path.expanduser("~/.local/lib/python*/site-packages/freenect*.so")
)
if not _freenect_so:
    _freenect_so = glob.glob("/usr/lib/python*/dist-packages/freenect*.so")
if not _freenect_so:
    raise FileNotFoundError(
        "freenect .so não encontrado. Verifique: sudo apt install python3-freenect"
    )
_freenect_so = _freenect_so[0]
print(f"[spec] freenect: {_freenect_so}")

# ─── Bibliotecas nativas do freenect ─────────────────────────────────────────
_native_libs = [
    "/lib/x86_64-linux-gnu/libfreenect.so.0.5",
    "/lib/x86_64-linux-gnu/libfreenect_sync.so.0.5",
    "/lib/x86_64-linux-gnu/libusb-1.0.so.0",
]
_binaries = [(_freenect_so, ".")]
for _lib in _native_libs:
    if os.path.exists(_lib):
        _binaries.append((_lib, "."))
        print(f"[spec] lib: {_lib}")
    else:
        print(f"[spec] AVISO: {_lib} não encontrado (pode estar em caminho diferente)")

_datas = []
_cascade_name = "haarcascade_frontalface_default.xml"
_opencv_cascade = os.path.join(os.path.dirname(cv2.__file__), "data", _cascade_name)
if os.path.exists(_opencv_cascade):
    _datas.append((_opencv_cascade, os.path.join("cv2", "data")))
    print(f"[spec] cascade: {_opencv_cascade}")
else:
    print(f"[spec] AVISO: cascade não encontrado em {_opencv_cascade}")

# ─────────────────────────────────────────────────────────────────────────────

a = Analysis(
    ["iniciar_sistema.py"],
    pathex=[],
    binaries=_binaries,
    datas=_datas,
    hiddenimports=[
        "freenect",
        "cv2",
        "numpy",
        "flask",
        "werkzeug",
        "werkzeug.serving",
        "werkzeug.routing",
        "jinja2",
        "click",
        "itsdangerous",
        "markupsafe",
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
    name="iniciar_sistema",
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
