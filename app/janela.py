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
TAMANHO = (1240, 820)
MINIMO = (900, 600)
COR_FUNDO = "#202020"


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
