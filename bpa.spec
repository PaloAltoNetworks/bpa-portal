# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for BPA Portal — single-file desktop binary.

PyInstaller cannot cross-compile: run this on each target OS. On macOS the
build is forced to universal2 so one .app runs on both Apple Silicon and
Intel — set BPA_MAC_ARCH=native to opt out (e.g. when the build Python is
not itself universal2).
"""
import os
import sys
from pathlib import Path

block_cipher = None
HERE = Path(SPECPATH)
PKG = HERE / "bpa_portal"

# Bundled files: (source_on_disk, destination_inside_bundle).
# resource_path() looks these up at the bundle root under sys._MEIPASS.
DATAS = [
    (str(PKG / "templates"), "templates"),
    (str(PKG / "static"), "static"),
]

# macOS: build a universal2 (arm64 + x86_64) binary by default. An arm64-only
# build silently fails to launch on Intel Macs, which is the single most common
# "it doesn't work on my machine" report.
TARGET_ARCH = None
if sys.platform == "darwin" and os.environ.get("BPA_MAC_ARCH", "universal2") != "native":
    TARGET_ARCH = "universal2"

a = Analysis(
    [str(HERE / "run_portal.py")],
    pathex=[str(HERE)],
    binaries=[],
    datas=DATAS,
    hiddenimports=[
        "waitress",
        "platformdirs",
        "platformdirs.macos",
        "platformdirs.unix",
        "platformdirs.windows",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["tkinter", "matplotlib", "numpy", "PIL", "pytest"],
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
    name="BPA Portal",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=(sys.platform == "win32"),  # show console only on Windows; Mac/Linux silent
    disable_windowed_traceback=False,
    target_arch=TARGET_ARCH,
    codesign_identity=None,
    entitlements_file=None,
)

# macOS-only: wrap in a .app bundle
if sys.platform == "darwin":
    app = BUNDLE(
        exe,
        name="BPA Portal.app",
        icon=None,
        bundle_identifier="com.paloaltonetworks.bpa-portal",
        info_plist={
            "CFBundleName": "BPA Portal",
            "CFBundleDisplayName": "BPA Portal",
            "CFBundleVersion": "1.1.0",
            "CFBundleShortVersionString": "1.1.0",
            "NSHighResolutionCapable": True,
            "LSBackgroundOnly": False,
            "LSMinimumSystemVersion": "11.0",
        },
    )
