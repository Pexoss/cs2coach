# -*- mode: python ; coding: utf-8 -*-
# Gera dist/CS2Coach/CS2Coach.exe  (rode:  pyinstaller cs2coach.spec --noconfirm)
import os
from PyInstaller.utils.hooks import collect_all

datas, binaries, hidden = [("app/armas_img", "armas_img")] + ([("icone.ico", ".")] if os.path.exists("icone.ico") else []), [], ["analisar_demo", "ao_vivo", "janela", "icone_app", "pystray._win32",
                         "webview.platforms.edgechromium", "clr"]
for pacote in ("demoparser2", "polars", "pyarrow", "tqdm", "webview", "clr_loader", "pythonnet", "proxy_tools", "bottle"):
    d, b, h = collect_all(pacote)
    datas += d; binaries += b; hidden += h

a = Analysis(
    ["app/hub.py"], pathex=["app"], binaries=binaries, datas=datas, hiddenimports=hidden,
    excludes=["tkinter", "matplotlib", "scipy", "IPython", "pytest", "numba"], noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz, a.scripts, [], exclude_binaries=True, name="CS2Coach", debug=False, strip=False, upx=False,
    console=False,                      # programa de janela: sem terminal preto (mensagens vao para hub.log)
    icon="icone.ico" if os.path.exists("icone.ico") else None,
)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name="CS2Coach")
