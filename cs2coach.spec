# -*- mode: python ; coding: utf-8 -*-
# Gera dist/CS2Coach/CS2Coach.exe  (rode:  pyinstaller cs2coach.spec --noconfirm)
import os
from PyInstaller.utils.hooks import collect_all

datas, binaries, hidden = [], [], ["analisar_demo", "ao_vivo", "pystray._win32"]
for pacote in ("demoparser2", "polars", "pyarrow", "tqdm"):
    d, b, h = collect_all(pacote)
    datas += d; binaries += b; hidden += h

a = Analysis(
    ["app/hub.py"], pathex=["app"], binaries=binaries, datas=datas, hiddenimports=hidden,
    excludes=["tkinter", "matplotlib", "scipy", "IPython", "pytest", "numba"], noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz, a.scripts, [], exclude_binaries=True, name="CS2Coach", debug=False, strip=False, upx=False,
    console=True,                       # janela escondida sozinha quando o icone da bandeja funciona
    icon="icone.ico" if os.path.exists("icone.ico") else None,
)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name="CS2Coach")
