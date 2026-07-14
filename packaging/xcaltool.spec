# PyInstaller spec for a one-file Windows build of xcaltool.
#
# Build (on Windows, from the repo root):
#     py -3-32 -m pip install pyinstaller
#     py -3-32 -m PyInstaller packaging/xcaltool.spec
#
# Use 32-bit Python (py -3-32) if you need the Nexiq RP1210 driver, whose DLL
# (NULN2R32.dll) is 32-bit and won't load into 64-bit Python. The resulting
# dist/xcaltool.exe is a single self-contained file -- no Python install needed
# on the target PC.
#
# Paths are resolved from the spec's own location so the build works no matter
# what the current working directory is.

import os

root = os.path.abspath(os.path.join(SPECPATH, os.pardir))

block_cipher = None

a = Analysis(
    [os.path.join(root, 'run.py')],
    pathex=[os.path.join(root, 'src')],
    binaries=[],
    datas=[],
    hiddenimports=['xcaltool'],
    hookspath=[],
    runtime_hooks=[],
    excludes=[],
    cipher=block_cipher,
)
pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)
exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    name='xcaltool',
    debug=False,
    strip=False,
    upx=False,
    console=False,          # GUI app; no console window
)
