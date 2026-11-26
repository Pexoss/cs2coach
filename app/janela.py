# -*- coding: utf-8 -*-
"""
Janela propria do CS2 Coach: o Hub aparece numa janela de programa (WebView2, o mesmo motor do Edge que ja vem no
Windows 11), sem abas, sem barra de enderecos e sem depender do navegador do usuario.

Se a janela nao puder abrir (ex.: Windows antigo sem WebView2), cai para o modo "aplicativo" do Edge/Chrome e,
em ultimo caso, para o navegador padrao. Fechar a janela so esconde: o programa continua perto do relogio.
"""
from __future__ import annotations

import os
import subprocess
import threading
from pathlib import Path

TITULO = "CS2 Coach"
AUMID = "Pexoss.CS2Coach"
TAMANHO = (1240, 820)
MINIMO = (900, 600)
COR_FUNDO = "#202020"


def _caminho_icone():
    import sys
    for base in (getattr(sys, "_MEIPASS", None), Path(__file__).resolve().parent, Path(__file__).resolve().parent.parent,
                 Path(sys.executable).resolve().parent):
        if base and (Path(base) / "icone.ico").is_file():
            return Path(base) / "icone.ico"
    return None


def definir_identidade():
    """Faz o Windows tratar o programa como 'CS2 Coach' (icone proprio na barra de tarefas, nao o do Python/Edge)."""
    if os.name != "nt":
        return
    try:
        import ctypes
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(AUMID)
    except Exception:
        pass


def _tema_escuro() -> bool:
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                            r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize") as k:
            return winreg.QueryValueEx(k, "AppsUseLightTheme")[0] == 0
    except Exception:
        return True


def estilizar_janela(titulo=TITULO):
    """Icone da janela/barra de tarefas e moldura no estilo do Windows 11 (cantos redondos, barra de titulo na cor do app)."""
    if os.name != "nt":
        return False
    try:
        import ctypes
        from ctypes import wintypes
        u32 = ctypes.windll.user32
        u32.FindWindowW.restype = wintypes.HWND
        hwnd = u32.FindWindowW(None, titulo)
        if not hwnd:
            return False
        ico = _caminho_icone()
        if ico:
            u32.LoadImageW.restype = wintypes.HANDLE
            for tipo, tam in ((0, 16), (1, 32)):                     # ICON_SMALL, ICON_BIG
                h = u32.LoadImageW(None, str(ico), 1, tam, tam, 0x10)  # IMAGE_ICON, LR_LOADFROMFILE
                if h:
                    u32.SendMessageW(hwnd, 0x0080, tipo, h)            # WM_SETICON
        escuro = _tema_escuro()
        dwm = ctypes.windll.dwmapi

        def attr(n, valor):
            v = ctypes.c_int(valor)
            dwm.DwmSetWindowAttribute(wintypes.HWND(hwnd), n, ctypes.byref(v), ctypes.sizeof(v))

        def bgr(r, g, b):
            return r | (g << 8) | (b << 16)
        attr(20, 1 if escuro else 0)                  # barra de titulo escura/clara
        attr(33, 2)                                   # DWMWCP_ROUND: cantos arredondados
        cor = bgr(24, 24, 24) if escuro else bgr(228, 233, 241)       # igual ao fundo do menu lateral
        attr(35, cor)                                 # cor da barra de titulo (Windows 11)
        attr(34, bgr(60, 60, 60) if escuro else bgr(210, 216, 226))   # cor da borda
        attr(36, bgr(255, 255, 255) if escuro else bgr(26, 26, 26))   # cor do texto da barra
        return True
    except Exception:
        return False


class Janela:
    def __init__(self, url: str, pasta_dados: Path):
        self.url = url
        self.pasta = Path(pasta_dados) / "webview"
        self.wv = None
        self.janela = None
        self.saindo = False

    def disponivel(self) -> bool:
        try:
            import webview
            self.wv = webview
            for chave, valor in (("ALLOW_DOWNLOADS", True), ("OPEN_EXTERNAL_LINKS_IN_BROWSER", True)):
                try:
                    webview.settings[chave] = valor
                except Exception:
                    pass
            return True
        except Exception:
            return False

    def criar(self):
        self.pasta.mkdir(parents=True, exist_ok=True)
        self.janela = self.wv.create_window(TITULO, self.url, width=TAMANHO[0], height=TAMANHO[1], min_size=MINIMO,
                                            background_color=COR_FUNDO)
        self.janela.events.closing += self._ao_fechar
        with_suppress = getattr(self.janela.events, "shown", None)
        if with_suppress is not None:
            self.janela.events.shown += lambda: threading.Thread(target=self._estilo, daemon=True).start()
        self.janela.events.loaded += lambda: threading.Thread(target=self._estilo, daemon=True).start()

    def _estilo(self):
        import time
        for _ in range(10):                      # a janela pode demorar um instante para existir
            if estilizar_janela():
                break
            time.sleep(0.4)

    def _ao_fechar(self):
        """Fechar a janela so esconde (o programa segue na bandeja). 'Sair' no icone encerra de verdade."""
        if self.saindo:
            return True
        threading.Thread(target=self.ocultar, daemon=True).start()
        return False

    def ocultar(self):
        try:
            self.janela.hide()
        except Exception:
            pass

    def mostrar(self):
        try:
            self.janela.show()
            self.janela.restore()
        except Exception:
            pass

    def sair(self):
        self.saindo = True
        try:
            self.janela.destroy()
        except Exception:
            pass

    def iniciar(self, func=None):
        """Bloqueia (thread principal) ate a janela ser destruida. Levanta excecao se o WebView2 nao funcionar."""
        definir_identidade()
        self.wv.start(func, gui="edgechromium", storage_path=str(self.pasta), private_mode=False)


def abrir_modo_app(url: str) -> bool:
    """Plano B: Edge/Chrome em modo aplicativo (janela sem abas nem barra de enderecos)."""
    candidatos = []
    for base in (os.environ.get("ProgramFiles(x86)"), os.environ.get("ProgramFiles"), os.environ.get("LOCALAPPDATA")):
        if not base:
            continue
        candidatos += [Path(base) / "Microsoft/Edge/Application/msedge.exe", Path(base) / "Google/Chrome/Application/chrome.exe"]
    for exe in candidatos:
        if exe.is_file():
            try:
                subprocess.Popen([str(exe), f"--app={url}", f"--window-size={TAMANHO[0]},{TAMANHO[1]}"])
                return True
            except OSError:
                continue
    return False
