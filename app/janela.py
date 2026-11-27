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


RAIO = 12            # cantos arredondados (px logicos)


class _Win32:
    """Chamadas de janela do Windows (so existem no Windows)."""
    def __init__(self):
        import ctypes
        from ctypes import wintypes
        self.ct, self.wt = ctypes, wintypes
        u = ctypes.windll.user32
        g = ctypes.windll.gdi32
        H = wintypes.HWND
        u.GetWindowRect.argtypes = [H, ctypes.POINTER(wintypes.RECT)]
        u.SetWindowPos.argtypes = [H, H, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_uint]
        u.SetWindowRgn.argtypes = [H, wintypes.HRGN, wintypes.BOOL]
        u.MonitorFromWindow.argtypes = [H, wintypes.DWORD]
        u.MonitorFromWindow.restype = wintypes.HANDLE
        u.GetMonitorInfoW.argtypes = [wintypes.HANDLE, ctypes.c_void_p]
        u.GetCursorPos.argtypes = [ctypes.POINTER(wintypes.POINT)]
        u.GetAsyncKeyState.argtypes = [ctypes.c_int]
        g.CreateRoundRectRgn.restype = wintypes.HRGN
        g.CreateRoundRectRgn.argtypes = [ctypes.c_int] * 6
        self.u, self.g = u, g

    def retangulo(self, hwnd):
        r = self.wt.RECT()
        self.u.GetWindowRect(hwnd, self.ct.byref(r))
        return r.left, r.top, r.right - r.left, r.bottom - r.top

    def mover(self, hwnd, x, y, w, h):
        self.u.SetWindowPos(hwnd, None, int(x), int(y), int(w), int(h), 0x0014)   # NOZORDER | NOACTIVATE

    def cursor(self):
        p = self.wt.POINT()
        self.u.GetCursorPos(self.ct.byref(p))
        return p.x, p.y

    def botao_esquerdo(self):
        return bool(self.u.GetAsyncKeyState(0x01) & 0x8000)

    def escala(self, hwnd):
        try:
            return max(1.0, self.u.GetDpiForWindow(hwnd) / 96.0)
        except Exception:
            return 1.0

    def area_de_trabalho(self, hwnd):
        class MI(self.ct.Structure):
            _fields_ = [("cb", self.wt.DWORD), ("mon", self.wt.RECT), ("work", self.wt.RECT), ("fl", self.wt.DWORD)]
        mi = MI()
        mi.cb = self.ct.sizeof(MI)
        mon = self.u.MonitorFromWindow(hwnd, 2)     # MONITOR_DEFAULTTONEAREST
        self.u.GetMonitorInfoW(mon, self.ct.byref(mi))
        w = mi.work
        return w.left, w.top, w.right - w.left, w.bottom - w.top

    def arredondar(self, hwnd, ativo):
        if not ativo:
            self.u.SetWindowRgn(hwnd, None, True)
            return
        _, _, w, h = self.retangulo(hwnd)
        d = int(RAIO * 2 * self.escala(hwnd))
        self.u.SetWindowRgn(hwnd, self.g.CreateRoundRectRgn(0, 0, w + 1, h + 1, d, d), True)


class _Api:
    """Funcoes chamadas pelos botoes da barra de titulo desenhada na pagina (so metodos publicos ficam expostos)."""
    def __init__(self, jan):
        self._j = jan

    def estado(self):
        return {"max": self._j.maximizada}

    def minimizar(self):
        self._j.minimizar()

    def alternar(self):
        self._j.alternar_maximizar()
        return self._j.maximizada

    def fechar(self):
        self._j.fechar()

    def redimensionar(self, borda):
        self._j.redimensionar(str(borda))


class Janela:
    def __init__(self, url: str, pasta_dados: Path, moldura_propria: bool = True):
        self.moldura_propria = moldura_propria and os.name == "nt"
        self.maximizada = False
        self._normal = None
        self._w32 = None
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
        extra = {}
        if self.moldura_propria:
            # sem a moldura do Windows: a pagina desenha a barra de titulo e os cantos sao arredondados por aqui
            extra = dict(frameless=True, easy_drag=False, shadow=False, js_api=_Api(self))
        self.janela = self.wv.create_window(TITULO, self.url, width=TAMANHO[0], height=TAMANHO[1], min_size=MINIMO,
                                            background_color=COR_FUNDO, **extra)
        self.janela.events.closing += self._ao_fechar
        if self.moldura_propria:
            self.janela.events.resized += lambda *a: self._cantos()
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
        self._cantos()

    # ---- moldura propria ----------------------------------------------------------------------------------------
    def _hwnd(self):
        try:
            return int(self.janela.native.Handle.ToInt64())
        except Exception:
            pass
        try:
            import ctypes
            return ctypes.windll.user32.FindWindowW(None, TITULO) or None
        except Exception:
            return None

    def _w(self):
        if self._w32 is None:
            self._w32 = _Win32()
        return self._w32

    def _cantos(self):
        if not self.moldura_propria:
            return
        try:
            h = self._hwnd()
            if h:
                self._w().arredondar(h, not self.maximizada)
        except Exception:
            pass

    def minimizar(self):
        try:
            self.janela.minimize()
        except Exception:
            pass

    def fechar(self):
        if self.saindo:
            self.sair()
        else:
            self.ocultar()

    def alternar_maximizar(self):
        try:
            h, w = self._hwnd(), self._w()
            if not h:
                return
            if not self.maximizada:
                self._normal = w.retangulo(h)
                w.mover(h, *w.area_de_trabalho(h))
                self.maximizada = True
            else:
                if self._normal:
                    w.mover(h, *self._normal)
                self.maximizada = False
            self._cantos()
        except Exception:
            pass

    def redimensionar(self, borda):
        """Arrasta uma borda/canto (l, r, t, b, combinados): acompanha o mouse enquanto o botao esquerdo estiver apertado."""
        if self.maximizada:
            return
        import time
        try:
            h, w = self._hwnd(), self._w()
            if not h:
                return
            x0, y0, w0, h0 = w.retangulo(h)
            cx0, cy0 = w.cursor()
            esc = w.escala(h)
            mw, mh = int(MINIMO[0] * esc), int(MINIMO[1] * esc)
            while w.botao_esquerdo():
                cx, cy = w.cursor()
                dx, dy = cx - cx0, cy - cy0
                x, y, nw, nh = x0, y0, w0, h0
                if "r" in borda:
                    nw = max(mw, w0 + dx)
                if "l" in borda:
                    nw = max(mw, w0 - dx)
                    x = x0 + (w0 - nw)
                if "b" in borda:
                    nh = max(mh, h0 + dy)
                if "t" in borda:
                    nh = max(mh, h0 - dy)
                    y = y0 + (h0 - nh)
                w.mover(h, x, y, nw, nh)
                w.arredondar(h, True)
                time.sleep(0.012)
        except Exception:
            pass

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
