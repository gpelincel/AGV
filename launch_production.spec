# -*- mode: python ; coding: utf-8 -*-
"""
Spec PyInstaller do executavel principal do AGV.

Gera um unico binario console: dist/launch_production
"""

import glob
import os

import cv2

_freenect_so = glob.glob(os.path.expanduser("~/.local/lib/python*/site-packages/freenect*.so"))
if not _freenect_so:
    _freenect_so = glob.glob("/usr/lib/python*/dist-packages/freenect*.so")
if not _freenect_so:
    raise FileNotFoundError("freenect .so nao encontrado. Instale python3-freenect antes do build.")

_binaries = [(_freenect_so[0], ".")]
for lib_path in [
    "/lib/x86_64-linux-gnu/libfreenect.so.0.5",
    "/lib/x86_64-linux-gnu/libfreenect_sync.so.0.5",
    "/lib/x86_64-linux-gnu/libusb-1.0.so.0",
]:
    if os.path.exists(lib_path):
        _binaries.append((lib_path, "."))

_datas = []
_cascade_name = "haarcascade_frontalface_default.xml"
_opencv_cascade = os.path.join(os.path.dirname(cv2.__file__), "data", _cascade_name)
if os.path.exists(_opencv_cascade):
    _datas.append((_opencv_cascade, os.path.join("cv2", "data")))
if os.path.exists("pc_agv/templates"):
    _datas.append(("pc_agv/templates", "pc_agv/templates"))
if os.path.exists("pc_agv/static"):
    _datas.append(("pc_agv/static", "pc_agv/static"))

a = Analysis(
    ["launch_production.py"],
    pathex=[],
    binaries=_binaries,
    datas=_datas,
    hiddenimports=[
        "pc_agv.iniciar_sistema",
        "freenect",
        "cv2",
        "numpy",
        "flask",
        "werkzeug",
        "werkzeug.serving",
        "jinja2",
        "click",
        "itsdangerous",
        "markupsafe",
        "serial",
        "serial.serialutil",
        "serial.serialposix",
        "serial.tools",
        "serial.tools.list_ports",
        "serial.tools.list_ports_common",
        "serial.tools.list_ports_linux",
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
