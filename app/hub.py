#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
CS2 Coach - HUB (a central)

Um site que roda SO no seu computador (127.0.0.1) e junta tudo:
  - Visão geral e evolução das suas partidas
  - Todas as partidas já analisadas (com o relatório completo de cada uma)
  - Dano por arma e ranking das suas melhores armas
  - Suas configurações do CS2 (lidas dos arquivos do jogo) com sugestões de alteração
  - Análise de novas demos (.dem) sem usar o terminal
  - Painel ao vivo (recebe os dados do jogo) e as sessões ao vivo já salvas

Tudo fica guardado em  dados/cs2coach.db  (no seu PC). Nada é enviado para a internet.

Uso:   python hub.py          (abre o navegador em http://127.0.0.1:3100)
"""
from __future__ import annotations

import argparse
import contextlib
import html
import io
import json
import os
import re
import secrets
import sys
import threading
import time
import traceback
import webbrowser
from datetime import date, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote, unquote, urlparse

sys.path.insert(0, str(Path(__file__).resolve().parent))
import armas as armas_mod  # noqa: E402
import armazem  # noqa: E402
import config_cs  # noqa: E402
import icones  # noqa: E402
import janela as janela_mod  # noqa: E402
import metricas as metricas_mod  # noqa: E402
from versao import REPO, VERSAO  # noqa: E402

BASE = Path(__file__).resolve().parent
esc = html.escape

# --------------------------------------------------------------------------
# Estado do programa
# --------------------------------------------------------------------------


class Estado:
    def __init__(self):
        self.token = secrets.token_urlsafe(16)    # protege os botoes contra sites maliciosos
        self.porta = 3100
        self.porta_gsi = 3000
        self.gsi_token = "cs2coach"
        self.coach = None                          # painel ao vivo embutido (ou None)
        self.gsi_erro = None
        self.lock = threading.RLock()
        self.tarefa = None                         # analise de demos em andamento / ultima
        self.mensagem = None                       # aviso de uma acao (mostrado uma vez)
        self.update = None                         # {'versao','url','download','notas'} se houver versao nova
        self.update_erro = None
        self.moldura = False                       # True: a janela do programa nao tem moldura do Windows (barra propria)
        self.mostrar_janela = None                 # funcao que traz a janela do programa para frente
        self.parar = threading.Event()             # 'Sair' no icone da bandeja
        self.dl = None                             # download da versao nova: {'estado','bytes','total','arquivo','erro'}
        self.auto = {"vistos": {}, "ultima": None}   # importacao automatica de demos


E = Estado()

# --------------------------------------------------------------------------
# Utilitarios de formatacao
# --------------------------------------------------------------------------


def fmt(v, suf="%", casas=0):
    if v is None:
        return "—"
    try:
        return f"{float(v):.{casas}f}{suf}"
    except (TypeError, ValueError):
        return "—"


def data_br(s):
    try:
        return datetime.strptime(str(s)[:16], "%Y-%m-%d %H:%M").strftime("%d/%m/%Y %H:%M")
    except (TypeError, ValueError):
        return str(s) if s else "—"


def barra(v, cor=None):
    v = 0 if v is None else max(0, min(100, v))
    estilo = f' style="width:{v:.0f}%' + (f";background:{cor}" if cor else "") + '"'
    return f'<div class="bar"><i{estilo}></i></div>'


def grafico_linha(pontos, titulo, sufixo="", cor="#4cc2ff", casas=0):
    """pontos: lista de (rotulo, valor|None). Devolve um SVG com tooltip em cada ponto."""
    validos = [(i, r, v) for i, (r, v) in enumerate(pontos) if isinstance(v, (int, float))]
    if len(validos) < 2:
        return (f'<div class="graf"><h4>{esc(titulo)}</h4><div class="vazio">Precisa de pelo menos 2 partidas '
                f'para mostrar a evolução.</div></div>')
    w, h, px, py = 480, 150, 38, 22
    vs = [v for _, _, v in validos]
    lo, hi = min(vs), max(vs)
    if hi == lo:
        hi, lo = hi + 1, lo - 1
    margem = (hi - lo) * 0.12
    lo, hi = lo - margem, hi + margem
    n = len(pontos) - 1 or 1

    def X(i):
        return px + (w - px - 10) * i / n

    def Y(v):
        return h - py - (h - 2 * py) * (v - lo) / (hi - lo)

    pts = " ".join(f"{X(i):.1f},{Y(v):.1f}" for i, _, v in validos)
    partes = [f'<div class="graf"><h4>{esc(titulo)}</h4><svg viewBox="0 0 {w} {h}" role="img" aria-label="{esc(titulo)}">']
    for frac in (0, 0.5, 1):
        v = lo + (hi - lo) * frac
        y = Y(v)
        partes.append(f'<line x1="{px}" x2="{w - 10}" y1="{y:.1f}" y2="{y:.1f}" stroke="#2a3346"/>'
                      f'<text x="4" y="{y + 4:.1f}" fill="#93a0b5" font-size="11">{v:.{casas}f}</text>')
    partes.append(f'<polyline fill="none" stroke="{cor}" stroke-width="2" points="{pts}"/>')
    for i, r, v in validos:
        partes.append(f'<circle cx="{X(i):.1f}" cy="{Y(v):.1f}" r="3.5" fill="{cor}"><title>{esc(str(r))}: '
                      f'{v:.{casas}f}{sufixo}</title></circle>')
    partes.append(f'<text x="{px}" y="{h - 5}" fill="#93a0b5" font-size="10">{esc(str(pontos[0][0]))}</text>'
                  f'<text x="{w - 10}" y="{h - 5}" fill="#93a0b5" font-size="10" text-anchor="end">{esc(str(pontos[-1][0]))}</text>')
    partes.append("</svg></div>")
    return "".join(partes)


# --------------------------------------------------------------------------
# Layout
# --------------------------------------------------------------------------
CSS = """
:root{--bg:#262626;--side:#181818;--card:#2f2f2f;--card2:#353535;--stroke:#3b3b3b;--tx:#ffffff;--mut:#b4b4b4;--ac:#60cdff;--onac:#00253a;
--ok:#6ccb5f;--ruim:#ff99a4;--warn:#fcc84a;--hover:rgba(255,255,255,.06);--sel:rgba(255,255,255,.09);--sombra:0 2px 8px rgba(0,0,0,.25);
--p-dados-bg:#2f2a4d;--p-dados-tx:#cdbdff;--p-live-bg:#143a47;--p-live-tx:#8fdcf0;--p-ruim-bg:#4a2329;--p-ruim-tx:#ffb3ba;--p-ok-bg:#1f3b2a;--p-ok-tx:#9be6bd;--perigo-bg:#5a2b32;--perigo-tx:#ffd5d9}
@media (prefers-color-scheme:light){:root{--bg:#f8f9fb;--side:#e4e9f1;--card:#ffffff;--card2:#eef1f6;--stroke:#dde2ea;--tx:#1a1a1a;--mut:#5f5f5f;--ac:#005fb8;--onac:#ffffff;
--ok:#0f7b0f;--ruim:#c42b1c;--warn:#9d5d00;--hover:rgba(0,0,0,.05);--sel:rgba(0,0,0,.07);--sombra:0 2px 8px rgba(0,0,0,.08);
--p-dados-bg:#ece6ff;--p-dados-tx:#4b2ca3;--p-live-bg:#dff3fb;--p-live-tx:#005a80;--p-ruim-bg:#fde7e9;--p-ruim-tx:#a4262c;--p-ok-bg:#dff6dd;--p-ok-tx:#0e700e;--perigo-bg:#fde7e9;--perigo-tx:#a4262c}}
*{box-sizing:border-box}html{color-scheme:dark light}
body{margin:0;background:var(--bg);color:var(--tx);font:14px/1.5 "Segoe UI Variable Text","Segoe UI",system-ui,Roboto,sans-serif;display:flex;height:100vh;overflow:hidden;background:var(--side)}
a{color:var(--ac);text-decoration:none}a:hover{text-decoration:underline}
.side{width:268px;flex:none;background:transparent;padding:14px 10px;height:100vh;display:flex;flex-direction:column;gap:2px;overflow:auto}
.marca{display:flex;align-items:center;gap:10px;padding:6px 10px 14px;font:600 15px "Segoe UI Variable Display","Segoe UI",sans-serif}
.marca svg{width:26px;height:26px;flex:none}.marca small{display:block;font-weight:400;color:var(--mut);font-size:11px;line-height:1.1}
.side a.item{display:flex;align-items:center;gap:12px;height:40px;padding:0 12px;border-radius:6px;color:var(--tx);position:relative;white-space:nowrap}
.side a.item svg{width:20px;height:20px;flex:none;stroke:currentColor;fill:none;stroke-width:1.6;stroke-linecap:round;stroke-linejoin:round;opacity:.9}
.side a.item:hover{background:var(--hover);text-decoration:none}
.side a.item.on{background:var(--sel);font-weight:600}.side a.item.on::before{content:"";position:absolute;left:0;top:11px;bottom:11px;width:3px;border-radius:3px;background:var(--ac)}
.side a.item.on svg{stroke:var(--ac)}
.side .esp{flex:1}.side .ver{color:var(--mut);font-size:11px;padding:8px 12px}
.side .sep{height:1px;background:var(--stroke);margin:6px 8px}
.menu{display:flex;align-items:center;justify-content:center;width:40px;height:36px;border-radius:6px;border:0;background:transparent;color:var(--tx);cursor:pointer;margin:0 0 6px 6px;padding:0}
.menu:hover{background:var(--hover)}.menu svg{width:18px;height:18px;stroke:currentColor;fill:none;stroke-width:1.8;stroke-linecap:round}
body.compacto .side{width:60px}body.compacto .side .txt,body.compacto .marca b,body.compacto .marca small,body.compacto .side .ver{display:none}
body.compacto .side a.item{padding:0;justify-content:center}body.compacto .marca{justify-content:center;padding:6px 0 10px}
@media (max-width:900px){.side{width:60px}.side .txt,.marca b,.marca small,.side .ver{display:none}.side a.item{padding:0;justify-content:center}.marca{justify-content:center}}
main{flex:1;min-width:0;margin:8px 8px 8px 0;height:calc(100vh - 16px);overflow:auto;background:var(--bg);border:1px solid var(--stroke);border-radius:14px;box-shadow:0 4px 18px rgba(0,0,0,.18)}
html.fx .side{padding-top:6px}
html.fx main{margin-top:40px;height:calc(100vh - 48px)}
html.fx.max main{margin:40px 0 0 0;height:calc(100vh - 40px);border-radius:14px 0 0 0}
#tb{position:fixed;top:0;right:0;height:40px;display:flex;z-index:55;user-select:none;-webkit-user-select:none}
.dragr{position:fixed;top:0;left:60px;right:140px;height:40px;z-index:50}
#tb button{all:unset;width:46px;height:40px;display:flex;align-items:center;justify-content:center;color:var(--tx);cursor:default;opacity:.85}
#tb button svg{width:11px;height:11px;stroke:currentColor;fill:none;stroke-width:1.2}
#tb button:hover{background:var(--hover);opacity:1}#tb button.x:hover{background:#c42b1c;color:#fff}
html.fx:not(.max) #tb button.x{border-top-right-radius:12px}
.gr{position:fixed;z-index:60}.gr.l{left:0;top:10px;bottom:10px;width:5px;cursor:ew-resize}.gr.r{right:0;top:10px;bottom:10px;width:5px;cursor:ew-resize}
.gr.t{top:0;left:10px;right:10px;height:4px;cursor:ns-resize}.gr.b{bottom:0;left:10px;right:10px;height:5px;cursor:ns-resize}
.gr.tl{left:0;top:0;width:10px;height:10px;cursor:nwse-resize}.gr.br{right:0;bottom:0;width:10px;height:10px;cursor:nwse-resize}
.gr.tr{right:0;top:0;width:10px;height:10px;cursor:nesw-resize}.gr.bl{left:0;bottom:0;width:10px;height:10px;cursor:nesw-resize}
html.fx.max .gr{display:none}
html.fx:not(.max)::after{content:"";position:fixed;inset:0;border:1px solid rgba(128,128,128,.45);border-radius:12px;pointer-events:none;z-index:70}
html.max main{margin:0;height:100vh;border-radius:0;border-width:0 0 0 1px;box-shadow:none}
main::-webkit-scrollbar,.side::-webkit-scrollbar{width:12px}
main::-webkit-scrollbar-thumb,.side::-webkit-scrollbar-thumb{background:var(--stroke);border-radius:8px;border:3px solid transparent;background-clip:content-box}
main::-webkit-scrollbar-thumb:hover{background:var(--mut);background-clip:content-box;border:3px solid transparent}
main::-webkit-scrollbar-track{background:transparent;margin:10px 0}
.wrap{background:transparent;max-width:1180px;margin:0 auto;padding:14px 28px 60px}
h1{font:600 28px/1.2 "Segoe UI Variable Display","Segoe UI",sans-serif;margin:6px 0 4px}
h2{font:600 17px/1.3 "Segoe UI Variable Display","Segoe UI",sans-serif;margin:28px 0 10px}
h4{margin:0 0 6px;font-size:13px;color:var(--mut);font-weight:600}
.sub,.nota{color:var(--mut)}.nota{font-size:12.5px}.vazio{color:var(--mut);padding:8px 0}
.cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:10px;margin:14px 0}
.card,.graf,table,.dica,.ok,.aviso,details,pre{border:1px solid var(--stroke);box-shadow:var(--sombra)}
.card{background:var(--card);border-radius:12px;padding:12px 14px}.card b{display:block;font:600 26px "Segoe UI Variable Display","Segoe UI",sans-serif}.card span{color:var(--mut);font-size:12px}
.up{color:var(--ok)}.down{color:var(--ruim)}
.grid2{display:grid;grid-template-columns:repeat(auto-fit,minmax(340px,1fr));gap:12px}
.graf{background:var(--card);border-radius:12px;padding:10px 12px}.graf svg{width:100%;height:auto}
table{width:100%;border-collapse:separate;border-spacing:0;background:var(--card);border-radius:12px;overflow:hidden;margin:8px 0}
th,td{padding:9px 12px;text-align:left;border-bottom:1px solid var(--stroke);vertical-align:top}
th{color:var(--mut);font-weight:600;font-size:12.5px;background:var(--card2)}tr:last-child td{border-bottom:0}tbody tr:hover td,table tr:hover td{background:var(--hover)}
.bar{background:var(--card2);border-radius:4px;height:8px;min-width:60px}.bar i{display:block;height:8px;border-radius:4px;background:var(--ac)}
.dica{background:var(--card);border-radius:12px;padding:12px 16px;margin:10px 0;border-left:4px solid var(--warn)}
.dica.p1{border-left-color:var(--ruim)}.dica.p3{border-left-color:var(--ac)}.dica h3{margin:0 0 6px;font-size:15px}.dica p{margin:4px 0}
.ok{background:var(--card);border-left:4px solid var(--ok);border-radius:12px;padding:8px 14px;margin:8px 0}
.aviso{background:var(--card);border-left:4px solid var(--warn);border-radius:12px;padding:10px 14px;margin:10px 0}
.tabs{display:flex;gap:4px;flex-wrap:wrap;margin:8px 0 14px;border-bottom:1px solid var(--stroke)}
.tabs a{padding:7px 14px;color:var(--mut);border-bottom:3px solid transparent;margin-bottom:-1px;border-radius:6px 6px 0 0}
.tabs a.on{color:var(--tx);font-weight:600;border-bottom-color:var(--ac)}.tabs a:hover{text-decoration:none;background:var(--hover)}
form.inline{display:inline}
input[type=text],input[type=number],select{background:var(--card);color:var(--tx);border:1px solid var(--stroke);border-bottom:2px solid var(--stroke);border-radius:4px;padding:7px 10px;font:inherit;width:100%}
input[type=text]:focus,input[type=number]:focus,select:focus{outline:0;border-bottom-color:var(--ac)}
button,.btn{background:var(--ac);color:var(--onac);border:1px solid transparent;border-radius:4px;padding:6px 16px;min-height:32px;font:inherit;font-weight:600;cursor:pointer;display:inline-block}
button:hover,.btn:hover{filter:brightness(1.08);text-decoration:none}button:active,.btn:active{filter:brightness(.92)}
button.sec,.btn.sec{background:var(--card);color:var(--tx);border-color:var(--stroke);font-weight:400}
button.sec:hover,.btn.sec:hover{background:var(--card2)}button.perigo{background:var(--perigo-bg);color:var(--perigo-tx)}
label{display:block;margin:12px 0 4px;color:var(--mut);font-size:12.5px}.campo small{color:var(--mut)}
button.cp{padding:2px 10px;min-height:26px;font-size:12px;margin:2px 4px 2px 0}
code,.cmd{background:var(--card2);padding:1px 6px;border-radius:4px;font-family:ui-monospace,Consolas,"Cascadia Mono",monospace;font-size:12.5px}
pre{background:var(--card);border-radius:8px;padding:12px;overflow:auto;font-size:12.5px;max-height:340px}
iframe{width:100%;height:calc(100vh - 150px);min-height:560px;border:0;border-radius:12px;background:transparent;color-scheme:normal}
.pill{display:inline-block;background:var(--card2);color:var(--mut);border-radius:999px;padding:1px 9px;font-size:12px}
.icarma{color:var(--ac);vertical-align:middle;margin-right:8px;width:auto}.icimg{height:30px;vertical-align:middle;margin-right:8px}
.icmask{display:inline-block;vertical-align:middle;margin-right:8px;background:var(--ac);-webkit-mask:var(--m) center/contain no-repeat;mask:var(--m) center/contain no-repeat}
.expl{color:var(--mut);font-size:12.5px;margin:2px 0 0}.pill.dados{background:var(--p-dados-bg);color:var(--p-dados-tx)}.pill.live{background:var(--p-live-bg);color:var(--p-live-tx)}
details{background:var(--card);border-radius:12px;padding:8px 14px;margin:10px 0}summary{cursor:pointer;font-weight:600}
.pill.ruim{background:var(--p-ruim-bg);color:var(--p-ruim-tx)}.pill.ok{background:var(--p-ok-bg);color:var(--p-ok-tx)}
::-webkit-scrollbar{width:12px;height:12px}::-webkit-scrollbar-thumb{background:var(--card2);border-radius:8px;border:3px solid var(--bg)}
"""

_I = lambda d: f'<svg viewBox="0 0 24 24" aria-hidden="true">{d}</svg>'
# icones de linha (estilo Fluent): inicio, lista, mira, grafico, alvo, ao vivo, engrenagem, pasta, sliders
ICONES = {
    "/": _I('<path d="M4 11.5 12 4l8 7.5"/><path d="M6 10v9h4v-5h4v5h4v-9"/>'),
    "/partidas": _I('<rect x="5" y="4" width="14" height="16" rx="2"/><path d="M9 9h6M9 13h6M9 17h3"/>'),
    "/armas": _I('<circle cx="12" cy="12" r="7"/><path d="M12 3v4M12 17v4M3 12h4M17 12h4"/><circle cx="12" cy="12" r="1" />'),
    "/evolucao": _I('<path d="M4 19V5"/><path d="M4 19h16"/><path d="m7 15 4-5 3 3 5-7"/>'),
    "/treino": _I('<circle cx="12" cy="12" r="8"/><circle cx="12" cy="12" r="4"/><circle cx="12" cy="12" r=".8"/>'),
    "/ao-vivo": _I('<circle cx="12" cy="12" r="2.4"/><path d="M7.5 7.5a6.4 6.4 0 0 0 0 9M16.5 7.5a6.4 6.4 0 0 1 0 9M4.7 4.7a10.4 10.4 0 0 0 0 14.6M19.3 4.7a10.4 10.4 0 0 1 0 14.6"/>'),
    "/configuracoes": _I('<path d="M4 7h9M17 7h3M4 17h3M11 17h9"/><circle cx="15" cy="7" r="2"/><circle cx="9" cy="17" r="2"/>'),
    "/radar": _I('<path d="M12 21s-6.5-5.6-6.5-11a6.5 6.5 0 0 1 13 0c0 5.4-6.5 11-6.5 11z"/><circle cx="12" cy="10" r="2.3"/>'),
    "/rever": _I('<circle cx="12" cy="12" r="8"/><path d="m10 8.8 5 3.2-5 3.2z"/>'),
    "/analisar": _I('<path d="M3.5 7.5a1.5 1.5 0 0 1 1.5-1.5h4l2 2h8a1.5 1.5 0 0 1 1.5 1.5V17a1.5 1.5 0 0 1-1.5 1.5H5A1.5 1.5 0 0 1 3.5 17z"/>'),
    "/ajustes": _I('<circle cx="12" cy="12" r="3"/><path d="M12 3v2.2M12 18.8V21M3 12h2.2M18.8 12H21M5.6 5.6l1.6 1.6M16.8 16.8l1.6 1.6M18.4 5.6l-1.6 1.6M7.2 16.8l-1.6 1.6"/>'),
}
LOGO = ('<svg viewBox="0 0 64 64" aria-hidden="true"><defs><linearGradient id="lg" x1="0" y1="0" x2="1" y2="1">'
        '<stop offset="0" stop-color="#146eeb"/><stop offset="1" stop-color="#5acdff"/></linearGradient></defs>'
        '<rect width="64" height="64" rx="14" fill="url(#lg)"/>'
        '<circle cx="32" cy="32" r="15" fill="none" stroke="#fff" stroke-width="3.8"/>'
        '<path d="M32 9.5v9M32 45.5v9M9.5 32h9M45.5 32h9" stroke="#fff" stroke-width="3.8" stroke-linecap="round"/>'
        '<circle cx="32" cy="32" r="2.6" fill="#fff"/></svg>')

NAV = [("/", "Início"), ("/partidas", "Partidas"), ("/armas", "Armas"), ("/evolucao", "Evolução"), ("/radar", "Mapa de mortes"), ("/rever", "Momentos"), ("/treino", "Treino"),
       ("/ao-vivo", "Ao vivo"), ("/configuracoes", "Config. do jogo"), ("/analisar", "Analisar demos")]
NAV_RODAPE = [("/ajustes", "Ajustes")]


JS = """
try{if(localStorage.getItem('menu')==='c')document.documentElement.classList.add('c')}catch(e){}
function ajustaMax(){var m=(window.outerWidth>=screen.availWidth-2&&window.outerHeight>=screen.availHeight-2)||!!document.fullscreenElement;document.documentElement.classList.toggle('max',m)}
window.addEventListener('resize',ajustaMax);document.addEventListener('DOMContentLoaded',ajustaMax);
function alterna(){var c=document.body.classList.toggle('compacto');try{localStorage.setItem('menu',c?'c':'e')}catch(e){}}
function copia(b){var t=b.dataset.c,o=b.textContent;function ok(){b.textContent='Copiado ✔';setTimeout(function(){b.textContent=o},1500)}try{navigator.clipboard.writeText(t).then(ok,function(){fb()})}catch(e){fb()}function fb(){var x=document.createElement('textarea');x.value=t;document.body.appendChild(x);x.select();try{document.execCommand('copy');ok()}catch(e){}document.body.removeChild(x)}}
function api(f,a){try{var p=window.pywebview&&window.pywebview.api;if(p&&p[f])return p[f](a)}catch(e){}}
function tbMax(){api('alternar')}
function grip(b){return function(e){if(e.button===0){e.preventDefault();api('redimensionar',b)}}}
document.addEventListener('DOMContentLoaded',function(){document.querySelectorAll('.gr').forEach(function(g){g.addEventListener('mousedown',grip(g.dataset.b))});
var t=document.querySelector('.dragr');if(t)t.addEventListener('dblclick',tbMax)});
document.addEventListener('click',function(e){var a=e.target.closest&&e.target.closest('a[target=_blank]');if(a){e.preventDefault();fetch('/abrir?u='+encodeURIComponent(a.href))}});
document.addEventListener('DOMContentLoaded',function(){try{if(localStorage.getItem('menu')==='c')document.body.classList.add('compacto')}catch(e){}});
"""


def pagina(titulo, corpo, ativo="/", refresh=None):
    if not refresh and E.dl and E.dl.get("estado") == "baixando":
        refresh = 2
    meta = f'<meta http-equiv="refresh" content="{refresh}">' if refresh else ""
    def item(h, n):
        cls = "item on" if (h == ativo or (h != "/" and ativo.startswith(h))) else "item"
        return f'<a href="{h}" class="{cls}" title="{esc(n)}">{ICONES.get(h, "")}<span class="txt">{esc(n)}</span></a>'
    links = "".join(item(h, n) for h, n in NAV)
    rodape = "".join(item(h, n) for h, n in NAV_RODAPE)
    msg = ""
    if E.update:
        u = E.update
        notas = f"<div class='expl'>{esc(u['notas'])}</div>" if u.get("notas") else ""
        msg += (f"<div class='aviso'>🔔 <b>Nova versão {esc(u['versao'])} disponível</b> (você está na {esc(VERSAO)}). "
                f"{_bloco_baixar()} "
                f"<a href='{esc(u['url'])}' target='_blank' rel='noopener'>ver novidades</a>{notas}"
                "<div class='expl'>A atualização fecha o CS2 Coach, instala a versão nova e abre de novo. Seus dados são mantidos.</div></div>")
    with E.lock:
        if E.mensagem:
            msg = f'<div class="aviso">{E.mensagem}</div>'
            E.mensagem = None
    fx = bool(E.moldura)
    barra = ""
    if fx:
        barra = ('<div class="dragr pywebview-drag-region"></div><div id="tb">'
                 '<button onclick="api(\'minimizar\')" title="Minimizar"><svg viewBox="0 0 10 10"><path d="M0 5h10"/></svg></button>'
                 '<button onclick="tbMax()" title="Maximizar"><svg viewBox="0 0 10 10"><rect x="0.6" y="0.6" width="8.8" height="8.8" rx="1"/></svg></button>'
                 '<button class="x" onclick="api(\'fechar\')" title="Fechar"><svg viewBox="0 0 10 10"><path d="M0.5 0.5l9 9M9.5 0.5l-9 9"/></svg></button></div>'
                 + "".join(f'<div class="gr {b}" data-b="{b}"></div>' for b in ("l", "r", "t", "b", "tl", "tr", "bl", "br")))
    return (f'<!doctype html><html lang="pt-BR"{" class=fx" if fx else ""}><head><meta charset="utf-8">{meta}'
            f'<meta name="viewport" content="width=device-width,initial-scale=1"><title>{esc(titulo)} · CS2 Coach</title>'
            f'<style>{CSS}</style><script>{JS}</script></head><body>{barra}'
            f'<aside class="side"><button class="menu" onclick="alterna()" title="Recolher/expandir menu" aria-label="Menu">'
            f'<svg viewBox="0 0 24 24"><path d="M4 7h16M4 12h16M4 17h16"/></svg></button>'
            f'<div class="marca">{LOGO}<div><b>CS2 Coach</b><small>Hub</small></div></div>{links}<div class="esp"></div>'
            f'<div class="sep"></div>{rodape}<div class="ver">Versão {esc(VERSAO)}</div></aside>'
            f'<main><div class="wrap">{msg}{corpo}</div></main></body></html>')


def form_post(acao, campos="", botao="Enviar", classe="", confirmar=None):
    c = f' onsubmit="return confirm(\'{esc(confirmar)}\')"' if confirmar else ""
    return (f'<form method="post" action="/acao/{acao}" class="inline"{c}><input type="hidden" name="t" value="{E.token}">'
            f'{campos}<button class="{classe}">{esc(botao)}</button></form>')


# --------------------------------------------------------------------------
# Dados do usuario
# --------------------------------------------------------------------------
def steam_do_usuario():
    return armazem.ajuste_get("meu_steamid") or config_cs.detectar_steamid()


def escopo():
    """SteamID usado nos filtros (cai para 'todos' se o ID salvo nao tem partidas)."""
    sid = steam_do_usuario()
    if sid and armazem.listar_partidas(steamid=sid, limite=1):
        return sid
    return None


def ajustes_config():
    a = armazem.ajustes_todos()
    return {"dpi": a.get("dpi"), "hz": a.get("hz"), "sens": a.get("sens")}


# --------------------------------------------------------------------------
# Tabelas reutilizaveis
# --------------------------------------------------------------------------
# armas parecidas que reaproveitam o desenho de outra
ICONE_ALIAS = {"molotov": "inferno", "usp": "usp_silencer", "p2000": "hkp2000"}


def icone_arma(arma, altura=26):
    a = armas_mod.norm_arma(arma)
    if icones.nome_png_valido(a) and (armazem.PASTA_DADOS / "armas_img" / f"{a}.png").exists():
        return f'<img class="icimg" src="/img/arma/{a}.png" alt="{esc(a)}">'      # imagem que o usuario colocou
    inc = icones.arquivo_incluido(ICONE_ALIAS.get(a, a))
    if inc:
        larg, alt = icones.tamanho_png(inc)
        nome = inc.stem
        return (f'<span class="icmask" role="img" aria-label="{esc(a)}" style="height:{altura}px;aspect-ratio:{larg}/{alt};'
                f'--m:url(/img/arma/{nome}.png)"></span>')
    return icones.svg_arma(a, altura)


def tabela_armas(itens, com_partidas=False, links=False):
    visiveis = [a for a in itens if a.get("tiros") or a.get("dano") or a.get("kills")]
    if not visiveis:
        return '<div class="vazio">Nenhuma arma registrada.</div>'
    mx = max((a.get("dano") or 0) for a in visiveis) or 1
    cab = ("<tr><th>Arma</th>" + ("<th>Partidas</th>" if com_partidas else "")
           + "<th>Dano</th><th></th><th>Tiros</th><th>Tiros que acertam</th><th>Dano/tiro</th><th>Kills</th>"
             "<th>HS dos acertos</th><th>Dist. média</th><th>Nota</th></tr>")
    linhas = ""
    for a in visiveis:
        fogo = a.get("tipo") == "arma"
        nome = esc(a["nome"] or a["arma"])
        ic = icone_arma(a["arma"])
        if links and fogo:
            nome = f'<a href="/arma/{quote(a["arma"])}">{nome}</a>'
        peq = ""
        if a.get("amostra_pequena"):
            if (a.get("tiros_demo") or 0) == 0 and (a.get("tiros_ao_vivo") or 0) > 0:
                peq = ' <span class="pill live" title="só veio do painel ao vivo: sem acertos nem dano. Analise a demo para completar.">só ao vivo</span>'
            else:
                peq = ' <span class="pill" title="menos de 40 tiros em demos: sem nota">poucos tiros</span>'
        vivo_t = f' <small class="sub" title="tiros vindos do painel ao vivo">(+{a["tiros_ao_vivo"]} ao vivo)</small>' if a.get("tiros_ao_vivo") else ""
        vivo_k = f' <small class="sub" title="kills vindas do painel ao vivo">(+{a["kills_ao_vivo"]})</small>' if a.get("kills_ao_vivo") else ""
        nota = "—" if a.get("nota") is None else f'{a["nota"]:.0f}{barra(a["nota"], "#4cd68a")}'
        linhas += (f"<tr><td style='white-space:nowrap'>{ic}{nome}{peq}</td>" + (f"<td>{a.get('partidas', '')}</td>" if com_partidas else "")
                   + f"<td>{a.get('dano') or 0}</td><td>{barra(100.0 * (a.get('dano') or 0) / mx)}</td>"
                   f"<td>{((a.get('tiros') or 0) if a.get('tipo') != 'utilidade' else '—')}{vivo_t if a.get('tipo') != 'utilidade' else ''}</td>"
                   f"<td>{fmt(a.get('precisao')) if fogo else '—'}</td>"
                   f"<td>{fmt(a.get('dano_por_tiro'), '', 1) if fogo else '—'}</td><td>{a.get('kills') or 0}{vivo_k}</td>"
                   f"<td>{fmt(a.get('hs_pct')) if fogo else '—'}</td><td>{fmt(a.get('dist_media_m'), ' m')}</td>"
                   f"<td>{nota}</td></tr>")
    return "<table>" + cab + linhas + "</table>"


def tabela_partidas(partidas, acoes=True):
    if not partidas:
        return '<div class="vazio">Nenhuma partida analisada ainda.</div>'
    cron, mm = armazem.configs_cronologicas(), armazem.momentos_partidas()
    cfg_de = {p["id"]: armazem.escolher_config(cron, (mm.get(p["id"]) or {}).get("momento")) for p in partidas}
    ordem = sorted(partidas, key=lambda p: ((mm.get(p["id"]) or {}).get("momento") or "", p["id"]))
    mudou = {}
    for a, b in zip(ordem, ordem[1:]):
        ca, cb = cfg_de[a["id"]], cfg_de[b["id"]]
        mudou[b["id"]] = bool(ca and cb and ca["id"] != cb["id"] and config_cs.diferencas(ca, cb))
    cab = ("<tr><th>Data</th><th>Mapa</th><th>Demo</th><th>K/D</th><th>ADR</th><th>HS%</th><th>Rounds</th><th>Configuração</th>"
           + ("<th></th>" if acoes else "") + "</tr>")
    linhas = ""
    for p in partidas:
        extra = ""
        if acoes:
            extra = (f'<td><a class="btn sec" href="/partida/{p["id"]}">Abrir</a> '
                     + form_post("excluir-partida", f'<input type="hidden" name="id" value="{p["id"]}">', "Excluir",
                                 "perigo", "Excluir esta partida do Hub? A demo original não é apagada.") + "</td>")
        linhas += (f'<tr><td>{data_br(p["data_demo"])}</td><td>{esc(p["mapa"] or "—")}</td>'
                   f'<td><a href="/partida/{p["id"]}">{esc(p["demo"] or "")}</a></td>'
                   f'<td>{p["kills"]}/{p["mortes"]} ({p["kd"]})</td><td>{fmt(p["adr"], "", 0)}</td>'
                   f'<td>{fmt(p["hs"])}</td><td>{p["rounds"]}</td>'
                   f'<td class="sub">{cfg_curta(cfg_de[p["id"]])}'
                   + (' <span class="pill dados" title="a configuração mudou em relação à partida anterior">mudou</span>' if mudou.get(p["id"]) else "")
                   + f'</td>{extra}</tr>')
    return "<table>" + cab + linhas + "</table>"


def lista_dicas(dicas):
    out = ""
    for d in dicas:
        out += (f"<div class='dica p{d.get('prioridade', 3)}'><h3>{esc(d['titulo'])}</h3>"
                f"<p class='sub'>{esc(d['evidencia'])}</p><p><b>O que treinar:</b> {esc(d['acao'])}</p></div>")
    return out


# --------------------------------------------------------------------------
# Paginas
# --------------------------------------------------------------------------
def sincronizar_sessoes():
    """Traz para o banco as sessões ao vivo salvas em dados/sessoes (e liga as que têm demo)."""
    with contextlib.suppress(Exception):
        armazem.importar_sessoes(armazem.PASTA_DADOS / "sessoes")


def pg_inicio():
    sincronizar_sessoes()
    sid = escopo()
    hist = armazem.historico(sid)
    if not hist and not armazem.contar_sessoes_sem_demo():
        corpo = ("<h1>Bem-vindo ao Hub</h1><p class='sub'>Aqui fica tudo que o CS2 Coach analisa, guardado só no seu computador.</p>"
                 "<div class='dica p3'><h3>Comece por aqui</h3><ol>"
                 "<li>No CS2, baixe a demo de uma partida (Seu perfil &gt; Partidas &gt; Baixar).</li>"
                 "<li>Abra <a href='/analisar'>Analisar demos</a> e informe a pasta onde as demos ficam.</li>"
                 "<li>Veja o dano por arma em <a href='/armas'>Armas</a> e as dicas de ajuste em "
                 "<a href='/configuracoes'>Configurações</a>.</li></ol></div>"
                 "<p class='nota'>Para o painel ao vivo, abra a aba <a href='/ao-vivo'>Ao vivo</a>.</p>" + banner_auto())
        return pagina("Visão geral", corpo, "/")

    def media(chave):
        v = [h[chave] for h in hist if isinstance(h.get(chave), (int, float))]
        return sum(v) / len(v) if v else None

    def tendencia(chave, casas=1):
        v = [h[chave] for h in hist if isinstance(h.get(chave), (int, float))]
        if len(v) < 6:
            return ""
        a, b = sum(v[-5:]) / 5, sum(v[-10:-5]) / len(v[-10:-5])
        dif = a - b
        cls = "up" if dif > 0 else "down" if dif < 0 else ""
        return f' <small class="{cls}">{"▲" if dif > 0 else "▼" if dif < 0 else "•"} {abs(dif):.{casas}f} vs 5 anteriores</small>'

    def pontos(chave):
        return [(f"{data_br(h.get('data_demo'))[:5]} {h.get('mapa', '')}", h.get(chave)) for h in hist]

    cards = ("<div class='cards'>"
             f"<div class='card'><b>{len(hist)}</b><span>Partidas com demo</span></div>"
             + (f"<div class='card'><b>{armazem.contar_sessoes_sem_demo()}</b><span>Sessões ao vivo sem demo (<a href='/ao-vivo'>ver</a>)</span></div>"
                if armazem.contar_sessoes_sem_demo() else "") +
             f"<div class='card'><b>{fmt(media('kd'), '', 2)}</b><span>K/D médio{tendencia('kd', 2)}</span></div>"
             f"<div class='card'><b>{fmt(media('adr'), '', 0)}</b><span>ADR médio{tendencia('adr', 0)}</span></div>"
             f"<div class='card'><b>{fmt(media('hs'))}</b><span>Headshot médio{tendencia('hs', 0)}</span></div></div>")
    graficos = ("<div class='grid2'>" + grafico_linha(pontos("adr"), "ADR por partida", "", "#4cc2ff")
                + grafico_linha(pontos("kd"), "K/D por partida", "", "#4cd68a", 2)
                + grafico_linha(pontos("hs"), "% de headshot", "%", "#ffc857")
                + grafico_linha(pontos("rifle_acerto_parado"), "Rifle: acerto atirando parado", "%", "#c792ea")
                + "</div>")

    rk = armas_mod.ranking(armazem.armas_agregadas(sid))
    fogo = [a for a in rk if a.get("tipo") == "arma" and (a.get("tiros") or 0) > 0]
    com_nota = [a for a in fogo if a["nota"] is not None]
    top = armas_mod.ordenar(com_nota, "nota")[:3] if com_nota else armas_mod.ordenar(fogo, "dano")[:3]
    topo = ""
    if top:
        itens = "".join(
            f"<div class='card'><b>{esc(a['nome'])}</b><span>"
            + (f"nota {a['nota']:.0f}" if a["nota"] is not None else "mais dano")
            + f" · {a['dano']} de dano · {a['kills']} kills</span></div>" for a in top)
        topo = (f"<h2>Suas melhores armas</h2><div class='cards'>{itens}</div>"
                "<p class='nota'><a href='/armas'>Ver o ranking completo e o dano por arma →</a></p>")

    rec = armazem.dicas_recorrentes(sid)[:5]
    recorrentes = ""
    if rec:
        recorrentes = "<h2>O que mais se repete nas suas partidas</h2>"
        for r in rec:
            recorrentes += (f"<div class='dica p{r['prioridade']}'><h3>{esc(r['titulo'])} "
                            f"<span class='pill'>{r['vezes']} de {r['total_partidas']} partidas</span></h3>"
                            f"<p class='sub'>{esc(r['evidencia'])}</p><p><b>O que treinar:</b> {esc(r['acao'])}</p></div>")

    cfg = ""
    ult = armazem.ultimas_configs(1)
    if ult:
        tips, _, _ = config_cs.sugestoes(ult[0], ajustes_config(), hist, rk)
        n1 = sum(1 for t in tips if t["prioridade"] == 1)
        cfg = (f"<h2>Configurações</h2><p>Última leitura: {data_br(ult[0]['lido_em'])}. "
               f"{len(tips)} sugestões" + (f" (<b>{n1}</b> prioritárias)" if n1 else "") +
               " · <a href='/configuracoes'>ver detalhes →</a></p>")

    ultimas = "<h2>Últimas partidas</h2>" + tabela_partidas(armazem.listar_partidas(sid, limite=5), acoes=False)
    return pagina("Visão geral", "<h1>Visão geral</h1><div class='sub'>Tudo guardado no seu computador.</div>" + banner_auto() + card_treino_hoje()
                  + cards + graficos + topo + recorrentes + cfg + ultimas, "/")


def pg_bem_vindo():
    aj = armazem.ajustes_todos()
    libs = config_cs.bibliotecas_steam()
    jogo = config_cs.achar_pasta_jogo_cfg()
    contas = config_cs.achar_userdata()
    try:
        conf = config_cs.ler_configuracoes(aj.get("pasta_cfg_usuario") or None, aj.get("pasta_cfg_jogo") or None)
        r = config_cs.resumo(conf, {})
    except Exception:
        r = {}

    def item(ok, titulo, ajuda=""):
        return (f"<div class='{'ok' if ok else 'aviso'}'>{'✔' if ok else '✖'} <b>{esc(titulo)}</b>"
                + (f"<div class='expl'>{ajuda}</div>" if ajuda else "") + "</div>")
    checks = (item(bool(libs), "Steam encontrado", "" if libs else "Não achei o Steam. Se ele está em outra pasta, o Hub ainda funciona com demos; "
                   "para o ao vivo e a leitura de configurações, informe as pastas em <a href='/ajustes'>Ajustes</a>.")
              + item(bool(jogo), "CS2 encontrado", "" if jogo else "Não achei a pasta do CS2. Abra o Steam, clique com o botão direito no CS2 &gt; "
                     "Gerenciar &gt; Explorar arquivos locais, e informe a pasta cfg em Ajustes.")
              + item(bool(contas), "Conta do Steam detectada", "" if contas else "Informe seu SteamID64 abaixo (veja em steamid.io)."))
    if len(contas) > 1:
        conta_html = ("<label>Qual é a sua conta? (este PC tem mais de uma)</label><select name='meu_steamid'>"
                      + "".join(f"<option value='{esc(u['steamid64'])}'" + (" selected" if u["steamid64"] == aj.get("meu_steamid") else "")
                                + f">Conta {esc(u['conta'])} (SteamID {esc(u['steamid64'])})</option>" for u in contas) + "</select>")
    else:
        atual = aj.get("meu_steamid") or (contas[0]["steamid64"] if contas else "")
        conta_html = (f"<label>Seu SteamID64</label><input type='text' name='meu_steamid' value='{esc(atual)}'>"
                      "<small>É assim que o Hub sabe qual jogador é você dentro das demos.</small>")
    dpi = aj.get("dpi") or "800"
    hz = aj.get("hz") or (str(r["taxa_jogo_hz"]) if r.get("taxa_jogo_hz") else "144")
    sens = aj.get("sens") or (f"{r['sensibilidade']:g}" if r.get("sensibilidade") is not None else "")
    form = (f"<form method='post' action='/acao/wizard'><input type='hidden' name='t' value='{E.token}'>" + conta_html
            + f"<label>DPI do mouse</label><input type='text' name='dpi' value='{esc(dpi)}'>"
              "<small>Aparece no programa do seu mouse. Se não souber, deixe 800 (dá para mudar depois em Ajustes).</small>"
            + f"<label>Taxa de atualização do monitor (Hz)</label><input type='text' name='hz' value='{esc(hz)}'>"
              "<small>Ex.: 60, 144, 165, 240. No Windows: Configurações &gt; Tela &gt; Opções avançadas.</small>"
            + f"<label>Sensibilidade do jogo</label><input type='text' name='sens' value='{esc(sens)}'>"
              "<small>Só preencha se o Hub não conseguir ler do jogo.</small>"
            + "<label><input type='checkbox' name='instalar_gsi' value='1' checked style='width:auto'> "
              "Preparar o painel ao vivo (cria um arquivo de configuração na pasta do CS2)</label>"
            + "<label><input type='checkbox' name='auto_demos' value='1' checked style='width:auto'> "
              "Analisar sozinho as demos novas que aparecerem na pasta do jogo</label>"
            + "<p><button>Concluir</button> "
            + form_post("pular-wizard", "", "Pular por enquanto", "sec") + "</p></form>")
    corpo = ("<h1>Vamos deixar tudo pronto (1 minuto)</h1><p class='sub'>O CS2 Coach lê as demos das suas partidas e o que o jogo informa, "
             "e mostra no que melhorar. Tudo fica só no seu computador. Ele <b>não mexe no CS2 nem na memória do jogo</b>: usa apenas "
             "arquivos de demo e a integração oficial do jogo, então não há risco de VAC.</p>"
             "<h2>O que eu encontrei</h2>" + checks + "<h2>Seus dados</h2>" + form
             + "<h2>Como pegar as demos das suas partidas</h2><ol><li>No CS2, abra <b>Seu perfil &gt; Partidas</b>.</li>"
               "<li>Escolha a partida e clique em <b>Baixar</b> (ícone de seta).</li>"
               "<li>Pronto: o Hub percebe o arquivo novo e analisa sozinho. As demos de matchmaking ficam disponíveis por pouco tempo, "
               "então baixe logo.</li></ol>")
    return pagina("Primeiros passos", corpo, "/")


def pg_partidas(q):
    mapa = (q.get("mapa") or [""])[0]
    sid = escopo()
    mapas = armazem.mapas_disponiveis(sid)
    opcoes = '<option value="">Todos os mapas</option>' + "".join(
        f'<option value="{esc(m)}"{" selected" if m == mapa else ""}>{esc(m)}</option>' for m in mapas)
    filtro = (f'<form method="get" style="max-width:260px;margin:10px 0"><select name="mapa" onchange="this.form.submit()">'
              f'{opcoes}</select></form>')
    ps = armazem.listar_partidas(sid, mapa or None)
    return pagina("Partidas", f"<h1>Partidas</h1><div class='sub'>{len(ps)} partida(s) analisada(s)</div>{filtro}"
                  + tabela_partidas(ps), "/partidas")


def pg_partida(pid):
    d = armazem.obter_partida(pid)
    if not d:
        return pagina("Partida", "<h1>Partida não encontrada</h1><a href='/partidas'>Voltar</a>", "/partidas"), 404
    res, g = d["resultado"], d["resultado"]["geral"]
    ab = res.get("abertura", {})
    cards = ("<div class='cards'>"
             f"<div class='card'><b>{g['kills']}/{g['mortes']}</b><span>Kills / Mortes (K/D {g['kd']})</span></div>"
             f"<div class='card'><b>{g['adr']:.0f}</b><span>ADR</span></div>"
             f"<div class='card'><b>{fmt(g.get('hs_pct'))}</b><span>Headshot nas kills</span></div>"
             f"<div class='card'><b>{fmt(ab.get('winrate'))}</b><span>Duelos de abertura ({ab.get('ganhas', 0)}V/{ab.get('perdidas', 0)}D)</span></div></div>")
    rk = armas_mod.ordenar(armas_mod.ranking(armazem.armas_da_partida(pid)), "dano")
    corpo = (f"<p><a href='/partidas'>← Partidas</a></p><h1>{esc(d['mapa'] or '')} · {data_br(d['data_demo'])}</h1>"
             f"<div class='sub'>{esc(d['demo'] or '')} · {g['rounds']} rounds · jogador {esc(d['jogador'] or '')}</div>"
             f"<p><a class='btn' href='/partida/{pid}/relatorio'>Abrir relatório completo</a> "
             f"<a class='btn sec' href='/partida/{pid}/kills'>Ver cada kill e morte (onde e como)</a></p>"
             + cards + bloco_scope(armazem.resultados(d.get("steamid")), pid) + "<h2>O que mais pesou</h2>" + (lista_dicas(d["dicas"][:6]) or "<div class='vazio'>Sem pontos fortes de alerta.</div>")
             + "".join(f"<div class='ok'>✔ {esc(f)}</div>" for f in d["fortes"])
             + bloco_impacto(res) + bloco_momentos_partida(pid, res)
             + "<h2>Dano por arma nesta partida</h2>" + tabela_armas(rk)
             + "<p class='nota'>A nota só existe para armas com 40+ tiros e compara as suas armas entre si; "
               "veja o ranking somado em <a href='/armas'>Armas</a>.</p>")
    mom = armazem.momentos_partidas().get(pid, {})
    ant = None
    ps = sorted(armazem.listar_partidas(escopo()), key=lambda p: ((armazem.momentos_partidas().get(p["id"]) or {}).get("momento") or "", p["id"]))
    ids = [p["id"] for p in ps]
    if pid in ids and ids.index(pid) > 0:
        mm = armazem.momentos_partidas()
        ant = armazem.escolher_config(armazem.configs_cronologicas(), (mm.get(ids[ids.index(pid) - 1]) or {}).get("momento"))
    corpo += bloco_config(mom.get("momento"), mom.get("preciso"), ant)
    sv = armazem.sessao_da_partida(pid)
    if sv:
        corpo += (f"<h2>Complemento do painel ao vivo</h2><div class='ok'>Esta partida também foi acompanhada ao vivo "
                  f"(sessão de {data_br(sv['iniciado'])}). Os dados abaixo só existem no ao vivo e completam a demo; "
                  f"nada foi contado duas vezes. <a href='/sessao/{sv['id']}'>Abrir a sessão</a></div>" + bloco_sessao(sv, completo=False))
    return pagina(f"Partida {pid}", corpo, "/partidas"), 200


# --------------------------------------------------------------------------
# Metricas estilo Scope, kills (onde e como), evolucao e plano de treino
# --------------------------------------------------------------------------
def _pill(txt, cls=""):
    return f"<span class='pill {cls}'>{esc(txt)}</span> "


def _vel_txt(v):
    if v is None:
        return ""
    return "parado" if v < 40 else "andando" if v < 110 else "correndo"


def _nota_html(nota):
    if nota is None:
        return "<span class='sub'>—</span>"
    cor = "#3ddc97" if nota >= 66 else "#ff6b6b" if nota <= 33 else "#ffb84c"
    return f"<b>{nota}</b> {barra(nota, cor)}"


def bloco_scope(todos, pid):
    notas = metricas_mod.notas_partida(todos, pid)
    if not notas or all(n["valor"] is None for n in notas):
        return ""
    linhas = ""
    for n in notas:
        if n["valor"] is None:
            continue
        casas = 2 if n["chave"] == "kd" else 0 if n["chave"] in ("ttk", "adr") else 1
        media = f"{n['media']:.{casas}f}{n['sufixo']}" if n["media"] is not None else "—"
        valor = f"{n['valor']:.{casas}f}{n['sufixo']}"
        dica = "menor é melhor" if n["melhor"] == "baixo" else "maior é melhor"
        linhas += (f"<tr><td>{esc(n['rotulo'])} <span class='sub'>({dica})</span></td><td><b>{esc(valor)}</b></td>"
                   f"<td>{esc(media)}</td><td style='min-width:140px'>{_nota_html(n['nota'])}</td></tr>")
    return ("<h2>Métricas além do placar</h2><table><tr><th>Métrica</th><th>Esta partida</th>"
            "<th>Sua média nas outras</th><th>Nota (0–100)</th></tr>" + linhas + "</table>"
            "<p class='nota'>A nota compara esta partida com o <b>seu próprio histórico</b> (quantas das suas outras partidas "
            "ela supera, já levando em conta se mais alto ou mais baixo é melhor). Aparece depois de 5 partidas analisadas. "
            "Não uso tabelas por rating porque não existem números oficiais para isso.</p>")


def _tags_kill(k):
    t = ""
    if k.get("hs"):
        t += _pill("Headshot", "ok")
    if k.get("abertura"):
        t += _pill("Abertura", "dados")
    if k.get("parede"):
        t += _pill("Parede")
    if k.get("fumaca"):
        t += _pill("Fumaça")
    if k.get("sem_mira"):
        t += _pill("Sem mira")
    if k.get("cego"):
        t += _pill("Você cego", "ruim")
    vt = _vel_txt(k.get("vel_voce"))
    if vt:
        t += _pill(f"você {vt}", "ruim" if vt == "correndo" else "")
    return t


def _tags_morte(m):
    t = ""
    if m.get("hs"):
        t += _pill("Headshot sofrido", "ruim")
    if m.get("abertura"):
        t += _pill("Abertura", "dados")
    if m.get("tiros_antes") == 0:
        t += _pill("Sem atirar", "ruim")
    if m.get("fora_visao"):
        t += _pill("Fora da visão", "ruim")
    if m.get("cego"):
        t += _pill("Cego", "ruim")
    if m.get("parede"):
        t += _pill("Parede")
    if m.get("fumaca"):
        t += _pill("Fumaça")
    vt = _vel_txt(m.get("vel_voce"))
    if vt:
        t += _pill(f"você {vt}")
    return t


def mapa_kills_svg(kills, mortes, modo):
    """Pontos e linhas por posicao no mapa (sem a imagem do radar): verde = suas kills, vermelho = suas mortes."""
    todos = []
    for k in kills:
        todos += [(k.get("x"), k.get("y")), (k.get("vx"), k.get("vy"))]
    for m in mortes:
        todos += [(m.get("x"), m.get("y")), (m.get("ax"), m.get("ay"))]
    todos = [(x, y) for x, y in todos if x is not None and y is not None]
    if not todos:
        return ""
    w, h, mg = 640, 470, 26
    minx, maxx = min(x for x, _ in todos), max(x for x, _ in todos)
    miny, maxy = min(y for _, y in todos), max(y for _, y in todos)
    sc = min((w - 2 * mg) / max(maxx - minx, 1), (h - 2 * mg) / max(maxy - miny, 1))

    def X(x):
        return mg + (x - minx) * sc

    def Y(y):
        return h - mg - (y - miny) * sc

    p = [f'<svg viewBox="0 0 {w} {h}" role="img" aria-label="Mapa de kills e mortes" '
         f'style="background:#0f141b;border-radius:10px;width:100%;height:auto">']
    for fr in (0.25, 0.5, 0.75):
        p.append(f'<line x1="{w * fr:.0f}" x2="{w * fr:.0f}" y1="0" y2="{h}" stroke="#1a2230"/>'
                 f'<line y1="{h * fr:.0f}" y2="{h * fr:.0f}" x1="0" x2="{w}" stroke="#1a2230"/>')
    if modo in ("mortes", "ambas"):
        for m in mortes:
            if None in (m.get("x"), m.get("y"), m.get("ax"), m.get("ay")):
                continue
            tip = (f"Round {m['rnd']} · morte para {_nome_arma(m['arma'])} · {m['dist']:.0f} m"
                   + (" · headshot" if m.get("hs") else ""))
            p.append(f'<g><title>{esc(tip)}</title>'
                     f'<line x1="{X(m["ax"]):.1f}" y1="{Y(m["ay"]):.1f}" x2="{X(m["x"]):.1f}" y2="{Y(m["y"]):.1f}" '
                     f'stroke="#ff6b6b" stroke-width="1.6" opacity=".75"/>'
                     f'<circle cx="{X(m["ax"]):.1f}" cy="{Y(m["ay"]):.1f}" r="3.5" fill="none" stroke="#ff6b6b" stroke-width="1.6"/>'
                     f'<rect x="{X(m["x"]) - 4:.1f}" y="{Y(m["y"]) - 4:.1f}" width="8" height="8" fill="#ff6b6b" '
                     f'transform="rotate(45 {X(m["x"]):.1f} {Y(m["y"]):.1f})"/>'
                     f'<text x="{X(m["x"]) + 7:.1f}" y="{Y(m["y"]) - 5:.1f}" fill="#ffb3ba" font-size="10">{m["rnd"]}</text></g>')
    if modo in ("kills", "ambas"):
        for k in kills:
            if None in (k.get("x"), k.get("y"), k.get("vx"), k.get("vy")):
                continue
            tip = (f"Round {k['rnd']} · {_nome_arma(k['arma'])} · {k['dist'] or 0:.0f} m"
                   + (" · headshot" if k.get("hs") else "") + (" · parede" if k.get("parede") else "")
                   + (" · fumaça" if k.get("fumaca") else "") + (" · sem mira" if k.get("sem_mira") else ""))
            vx, vy = X(k["vx"]), Y(k["vy"])
            p.append(f'<g><title>{esc(tip)}</title>'
                     f'<line x1="{X(k["x"]):.1f}" y1="{Y(k["y"]):.1f}" x2="{vx:.1f}" y2="{vy:.1f}" '
                     f'stroke="#3ddc97" stroke-width="1.6" opacity=".8"/>'
                     f'<circle cx="{X(k["x"]):.1f}" cy="{Y(k["y"]):.1f}" r="4" fill="#3ddc97"/>'
                     f'<line x1="{vx - 4:.1f}" y1="{vy - 4:.1f}" x2="{vx + 4:.1f}" y2="{vy + 4:.1f}" stroke="#3ddc97" stroke-width="2"/>'
                     f'<line x1="{vx - 4:.1f}" y1="{vy + 4:.1f}" x2="{vx + 4:.1f}" y2="{vy - 4:.1f}" stroke="#3ddc97" stroke-width="2"/>'
                     f'<text x="{X(k["x"]) + 7:.1f}" y="{Y(k["y"]) - 5:.1f}" fill="#9be6bd" font-size="10">{k["rnd"]}</text></g>')
    p.append("</svg>")
    return "".join(p)


def _nome_arma(a):
    return armas_mod.nome_arma(armas_mod.norm_arma(a))


def pg_kills(pid, q):
    d = armazem.obter_partida(pid)
    if not d:
        return pagina("Kills", "<h1>Partida não encontrada</h1><a href='/partidas'>Voltar</a>", "/partidas"), 404
    res = d["resultado"]
    kills, mortes = res.get("kills_det"), res.get("detalhes_mortes") or []
    topo = (f"<p><a href='/partida/{pid}'>← Partida</a></p><h1>Kills e mortes · {esc(d['mapa'] or '')} · "
            f"{data_br(d['data_demo'])}</h1>")
    if kills is None:
        return pagina("Kills", topo + "<div class='aviso'>Esta partida foi analisada antes dos detalhes de kills existirem. "
                      "Reanalise para ver onde e como cada kill aconteceu (a demo precisa continuar numa das pastas vigiadas)."
                      + form_post("reanalisar", f"<input type='hidden' name='id' value='{pid}'>", "Reanalisar esta partida", "")
                      + "</div>", "/partidas"), 200
    modo = (q.get("v") or ["ambas"])[0]
    if modo not in ("kills", "mortes", "ambas"):
        modo = "ambas"
    tabs = "<div class='tabs'>" + "".join(
        f"<a class='{'on' if modo == v else ''}' href='/partida/{pid}/kills?v={v}'>{rot}</a>"
        for v, rot in (("ambas", "Kills e mortes"), ("kills", "Só kills"), ("mortes", "Só mortes"))) + "</div>"
    mapa_html = mapa_kills_svg(kills, mortes, modo)
    leg = ("<p class='nota'><span style='color:#3ddc97'>●</span> você (kill) → <span style='color:#3ddc97'>✕</span> inimigo abatido · "
           "<span style='color:#ff6b6b'>◇</span> você (morte) ← <span style='color:#ff6b6b'>○</span> quem te matou. "
           "O número é o round; passe o mouse para ver arma e distância. É um mapa de pontos: não uso a imagem do radar do jogo.</p>")
    linhas_k = ""
    for k in kills:
        linhas_k += (f"<tr><td>{k['rnd']}</td><td>{k['t']:.0f}s</td><td>{icone_arma(k['arma'], 22)}{esc(_nome_arma(k['arma']))}</td>"
                     f"<td>{fmt(k.get('dist'), ' m', 0)}</td><td>{fmt(k.get('ttk_ms'), ' ms', 0)}</td>"
                     f"<td>{_tags_kill(k)}</td><td>{esc(k.get('vitima') or '')}</td></tr>")
    linhas_m = ""
    for m in mortes:
        linhas_m += (f"<tr><td>{m['rnd']}</td><td>{fmt(m.get('t'), 's', 0)}</td><td>{icone_arma(m['arma'], 22)}{esc(_nome_arma(m['arma']))}</td>"
                     f"<td>{fmt(m.get('dist'), ' m', 0)}</td><td>{_tags_morte(m)}</td><td>{esc(m.get('atacante') or '')}</td></tr>")
    corpo = topo + tabs + mapa_html + leg
    if modo in ("kills", "ambas"):
        corpo += ("<h2>Como foi cada kill</h2>" + ("<table><tr><th>Round</th><th>Tempo</th><th>Arma</th><th>Distância</th>"
                  "<th title='Do primeiro dano até a morte'>Tempo p/ matar</th><th>Como</th><th>Vítima</th></tr>"
                  + linhas_k + "</table>" if linhas_k else "<div class='vazio'>Nenhuma kill sua nesta partida.</div>"))
    ac = res.get("acertos_det")
    if ac and modo in ("kills", "ambas"):
        tot = sum((ac.get("locais") or {}).values()) or 1
        locais = " · ".join(f"{esc(k)} {100 * v / tot:.0f}%" for k, v in
                            sorted((ac.get("locais") or {}).items(), key=lambda kv: -kv[1]))
        kr = {}
        for k in kills:
            kr[k["rnd"]] = kr.get(k["rnd"], 0) + 1
        linhas_d = "".join(
            f"<tr><td>{x['rnd']}</td><td>{x['dano']}</td><td>{x['acertos']}</td><td>{kr.get(x['rnd'], 0)}</td>"
            f"<td>{fmt(x.get('dist_media'), ' m', 0)}</td>"
            f"<td class='sub'>{esc(', '.join(f'{n}× {l}' for l, n in sorted(x['locais'].items(), key=lambda kv: -kv[1])) or '—')}</td></tr>"
            for x in ac.get("rounds", []))
        corpo += ("<h2>Dano e onde os tiros acertaram</h2>"
                  f"<p class='nota'>Distribuição dos seus acertos: {locais or '—'}.</p>"
                  "<table><tr><th>Round</th><th>Dano</th><th>Acertos</th><th>Kills</th><th>Dist. média</th>"
                  "<th>Onde acertou</th></tr>" + linhas_d + "</table>")
    if modo in ("mortes", "ambas"):
        corpo += ("<h2>Como você morreu</h2>" + ("<table><tr><th>Round</th><th>Tempo</th><th>Arma</th><th>Distância</th>"
                  "<th>Como</th><th>Quem</th></tr>" + linhas_m + "</table>" if linhas_m else "<div class='vazio'>Nenhuma morte.</div>"))
    return pagina("Kills e mortes", corpo, "/partidas"), 200


# --------------------------------------------------------------------------
# Nota de impacto, momentos para rever e mapa de mortes (varias partidas)
# --------------------------------------------------------------------------
TIPOS_MOMENTO = {"sem_atirar": "Morte sem atirar", "rajada": "Rajada longa sem kill", "correndo": "Correndo contra alvo parado",
                 "cego": "Morreu cego", "abertura": "Abertura perdida"}


def _copiar(texto, rotulo):
    return (f"<button class='sec cp' type='button' data-c=\"{esc(texto)}\" onclick='copia(this)' title=\"{esc(texto)}\">"
            f"{esc(rotulo)}</button>")


def _comandos_demo(nome_demo, ir_para):
    arq = _acha_demo(nome_demo) if nome_demo else None
    abrir = ""
    if arq:
        abrir = _copiar('playdemo "' + str(arq).replace("\\", "/") + '"', "1. copiar: abrir a demo") + " "
    elif nome_demo:
        abrir = _copiar('playdemo "' + nome_demo + '"', "1. copiar: abrir a demo") + " "
    return abrir + _copiar(f"demo_gototick {ir_para}", "2. copiar: ir ao lance")


def tabela_momentos(res, limite=None, nome_demo=None):
    ms = res.get("momentos")
    if ms is None:
        return None
    nome_demo = nome_demo or (res.get("meta") or {}).get("demo")
    ms = ms[:limite] if limite else ms
    if not ms:
        return "<div class='ok'>✔ Nenhum erro marcante encontrado nesta partida.</div>"
    gcor = {3: "ruim", 2: "", 1: "dados"}
    linhas = "".join(
        f"<tr><td>{m['rnd']}</td><td>{m['t']:.0f}s</td><td>{_pill(TIPOS_MOMENTO.get(m['tipo'], m['tipo']), gcor.get(m.get('gravidade'), ''))}</td>"
        f"<td>{esc(m['texto'])}</td><td>{_nota_html(m.get('nota_round'))}</td><td>{_comandos_demo(nome_demo, m['ir_para'])}</td></tr>"
        for m in ms)
    return ("<table><tr><th>Round</th><th>Tempo</th><th>O que houve</th><th></th><th title='Nota de impacto do round'>Impacto</th>"
            "<th>Comando no jogo</th></tr>" + linhas + "</table>")


NOTA_COMANDOS = ("<p class='nota'>Como usar: abra o CS2, abra o console (tecla <code>'</code> ou <code>\"</code>) e cole o 1º comando para "
                 "abrir a demo. Quando ela carregar, cole o 2º para pular para 5 segundos antes do lance. "
                 "Se a demo já estiver aberta, use só o 2º.</p>")


def bloco_impacto(res):
    im = (res.get("impacto") or {}).get("rounds") or []
    if not im:
        return ""
    media_ = (res.get("impacto") or {}).get("media")
    melhores = sorted(im, key=lambda x: -x["nota"])[:3]
    piores = sorted(im, key=lambda x: x["nota"])[:3]
    graf = grafico_linha([(f"round {i['rnd']}", i["nota"]) for i in im], "Nota de impacto por round", "", "#8b6cff", 0) if len(im) >= 3 else ""
    graf = f"<div style='max-width:640px'>{graf}</div>" if graf else ""

    def lin(i):
        tags = " · ".join(i["tags"]) or "sem ação marcante"
        return (f"<tr><td>{i['rnd']}</td><td>{_nota_html(i['nota'])}</td><td class='sub'>{esc(tags)}</td></tr>")
    todas = "".join(lin(i) for i in im)
    return ("<h2>Impacto por round</h2>"
            f"<div class='cards'><div class='card'><b>{fmt(media_, '', 0)}</b><span>Impacto médio (0-100)</span></div>"
            f"<div class='card'><b>{melhores[0]['rnd']}</b><span>Seu melhor round ({melhores[0]['nota']})</span></div>"
            f"<div class='card'><b>{piores[0]['rnd']}</b><span>Seu pior round ({piores[0]['nota']})</span></div></div>"
            + graf +
            "<details><summary>Ver a nota de cada round e o que pesou</summary><table><tr><th>Round</th><th>Nota</th><th>O que pesou</th></tr>"
            + todas + "</table></details>"
            "<p class='nota'>A nota soma: dano, kills, abrir o round, kills em desvantagem numérica, dano de granada, assistências "
            "e se o round foi ganho (e você sobreviveu). É uma medida própria para comparar os seus rounds entre si, não a nota de outro site.</p>")


def bloco_momentos_partida(pid, res):
    t = tabela_momentos(res, limite=6)
    if t is None:
        return ""
    return ("<h2>Momentos para rever</h2>" + t + NOTA_COMANDOS
            + f"<p><a href='/rever?p={pid}'>Ver todos os momentos desta partida</a> · <a href='/rever'>Momentos de todas as partidas</a></p>")


def pg_rever(q):
    sid = escopo()
    todos = list(reversed(armazem.resultados(sid)))
    pid_f = (q.get("p") or [""])[0]
    tipo_f = (q.get("t") or [""])[0]
    if pid_f.isdigit():
        todos = [r for r in todos if r["id"] == int(pid_f)]
    else:
        todos = todos[:12]
    tabs = "<div class='tabs'>" + "".join(
        f"<a class='{'on' if tipo_f == k else ''}' href='/rever?t={k}'>{esc(v)}</a>"
        for k, v in (("", "Todos"),) + tuple(TIPOS_MOMENTO.items())) + "</div>" if not pid_f.isdigit() else ""
    blocos, sem = "", 0
    for r in todos:
        res = r["res"]
        if res.get("momentos") is None:
            sem += 1
            continue
        res2 = dict(res)
        if tipo_f in TIPOS_MOMENTO:
            res2["momentos"] = [m for m in res["momentos"] if m["tipo"] == tipo_f]
        if not res2["momentos"]:
            continue
        blocos += (f"<h2><a href='/partida/{r['id']}'>{esc(r['mapa'] or '')} · {data_br(r['data_demo'])}</a></h2>"
                   + tabela_momentos(res2))
    aviso = ""
    if sem:
        aviso = (f"<div class='aviso'>{sem} partida(s) foram analisadas antes desta função existir e ainda não têm momentos. "
                 "Elas serão atualizadas quando você reanalisar (a demo precisa estar numa das pastas vigiadas). "
                 + form_post("reanalisar-todas", "", "Reanalisar essas partidas", "") + "</div>")
    corpo = ("<h1>Momentos para rever</h1><p class='sub'>Rounds com erro: morte sem atirar, rajada longa sem kill, correr contra alvo parado, "
             "morrer cego e aberturas perdidas. Cada um vem com o comando para ir direto ao lance na demo.</p>"
             + tabs + aviso + (blocos or "<div class='vazio'>Nenhum momento encontrado com esse filtro.</div>") + NOTA_COMANDOS)
    return pagina("Momentos para rever", corpo, "/rever")


def _zona(x, y, minx, miny, cw, ch, n=8):
    c = min(n - 1, max(0, int((x - minx) / cw)))
    r = min(n - 1, max(0, int((y - miny) / ch)))
    return c, r


def pg_radar(q):
    sid = escopo()
    todos = armazem.resultados(sid)
    mapas = {}
    for r in todos:
        if (r["res"].get("detalhes_mortes") or r["res"].get("kills_det")) is not None:
            mapas[r["mapa"]] = mapas.get(r["mapa"], 0) + 1
    topo = "<h1>Mapa de mortes e kills</h1>"
    if not mapas:
        return pagina("Mapa de mortes", topo + "<div class='vazio'>Analise algumas demos para ver onde você mais morre e mata.</div>",
                      "/radar")
    m = (q.get("m") or [""])[0]
    if m not in mapas:
        m = max(mapas, key=lambda k: mapas[k])
    modo = (q.get("v") or ["ambas"])[0]
    if modo not in ("kills", "mortes", "ambas"):
        modo = "ambas"
    tabs = "<div class='tabs'>" + "".join(
        f"<a class='{'on' if k == m else ''}' href='/radar?m={esc(k)}&v={modo}'>{esc(k)} ({n})</a>"
        for k, n in sorted(mapas.items(), key=lambda kv: -kv[1])) + "</div>"
    tabs2 = "<div class='tabs'>" + "".join(
        f"<a class='{'on' if modo == v else ''}' href='/radar?m={esc(m)}&v={v}'>{rot}</a>"
        for v, rot in (("ambas", "Mortes e kills"), ("mortes", "Só mortes"), ("kills", "Só kills"))) + "</div>"
    rs = [r for r in todos if r["mapa"] == m]
    mortes, kills = [], []
    for r in rs:
        mortes += [x for x in (r["res"].get("detalhes_mortes") or []) if x.get("x") is not None and x.get("y") is not None]
        kills += [x for x in (r["res"].get("kills_det") or []) if x.get("x") is not None and x.get("y") is not None]
    pts = [(x["x"], x["y"]) for x in mortes] + [(x["x"], x["y"]) for x in kills]
    if not pts:
        return pagina("Mapa de mortes", topo + tabs + "<div class='vazio'>Sem posições nesse mapa.</div>", "/radar")
    N = 8
    minx, maxx = min(p[0] for p in pts), max(p[0] for p in pts)
    miny, maxy = min(p[1] for p in pts), max(p[1] for p in pts)
    w, h, mg = 700, 520, 24
    sc = min((w - 2 * mg) / max(maxx - minx, 1), (h - 2 * mg) / max(maxy - miny, 1))
    ox = mg + ((w - 2 * mg) - (maxx - minx) * sc) / 2
    oy = mg + ((h - 2 * mg) - (maxy - miny) * sc) / 2
    cw, ch = max(maxx - minx, 1) / N, max(maxy - miny, 1) / N

    def X(x):
        return ox + (x - minx) * sc

    def Y(y):
        return h - oy - (y - miny) * sc
    zonas = {}
    for x in mortes:
        z = zonas.setdefault(_zona(x["x"], x["y"], minx, miny, cw, ch, N), {"m": 0, "k": 0, "tipos": {}, "armas": {}})
        z["m"] += 1
        z["tipos"][x.get("tipo") or tipo_de_duelo_hub(x)] = z["tipos"].get(x.get("tipo") or tipo_de_duelo_hub(x), 0) + 1
        a = _nome_arma(x["arma"])
        z["armas"][a] = z["armas"].get(a, 0) + 1
    for x in kills:
        z = zonas.setdefault(_zona(x["x"], x["y"], minx, miny, cw, ch, N), {"m": 0, "k": 0, "tipos": {}, "armas": {}})
        z["k"] += 1
    maxm = max([z["m"] for z in zonas.values()] + [1])
    sv = [f'<svg viewBox="0 0 {w} {h}" role="img" aria-label="Mapa de mortes e kills" style="background:var(--card2);border-radius:12px;width:100%;height:auto">']
    for (c, r), z in zonas.items():
        if modo != "kills" and z["m"]:
            x0, y1 = X(minx + c * cw), Y(miny + r * ch)
            sv.append(f'<rect x="{x0:.1f}" y="{Y(miny + (r + 1) * ch):.1f}" width="{cw * sc:.1f}" height="{ch * sc:.1f}" '
                      f'fill="#ff6b6b" opacity="{0.08 + 0.45 * z["m"] / maxm:.2f}"/>')
    for i in range(N + 1):
        sv.append(f'<line x1="{X(minx + i * cw):.1f}" x2="{X(minx + i * cw):.1f}" y1="{Y(miny):.1f}" y2="{Y(maxy):.1f}" stroke="rgba(128,128,128,.25)"/>'
                  f'<line y1="{Y(miny + i * ch):.1f}" y2="{Y(miny + i * ch):.1f}" x1="{X(minx):.1f}" x2="{X(maxx):.1f}" stroke="rgba(128,128,128,.25)"/>')
    for c in range(N):
        sv.append(f'<text x="{X(minx + (c + .5) * cw):.1f}" y="{h - 6}" fill="var(--mut)" font-size="11" text-anchor="middle">{"ABCDEFGH"[c]}</text>')
    for r in range(N):
        sv.append(f'<text x="8" y="{Y(miny + (r + .5) * ch) + 4:.1f}" fill="var(--mut)" font-size="11">{r + 1}</text>')
    if modo in ("kills", "ambas"):
        for x in kills:
            sv.append(f'<circle cx="{X(x["x"]):.1f}" cy="{Y(x["y"]):.1f}" r="3.2" fill="#3ddc97" opacity=".75">'
                      f'<title>Kill · {esc(_nome_arma(x["arma"]))} · {x.get("dist") or 0:.0f} m</title></circle>')
    if modo in ("mortes", "ambas"):
        for x in mortes:
            cx, cy = X(x["x"]), Y(x["y"])
            sv.append(f'<rect x="{cx - 3.6:.1f}" y="{cy - 3.6:.1f}" width="7.2" height="7.2" fill="#ff6b6b" opacity=".9" '
                      f'transform="rotate(45 {cx:.1f} {cy:.1f})"><title>Morte · {esc(x.get("tipo") or "")} · {esc(_nome_arma(x["arma"]))} · {x["dist"]:.0f} m</title></rect>')
    sv.append("</svg>")
    top = sorted(zonas.items(), key=lambda kv: (-kv[1]["m"], -kv[1]["k"]))[:8]
    linhas = ""
    for (c, r), z in top:
        if not z["m"] and not z["k"]:
            continue
        tipo = max(z["tipos"].items(), key=lambda kv: kv[1])[0] if z["tipos"] else "—"
        arma = max(z["armas"].items(), key=lambda kv: kv[1])[0] if z["armas"] else "—"
        saldo = z["k"] - z["m"]
        linhas += (f"<tr><td><b>{'ABCDEFGH'[c]}{r + 1}</b></td><td>{z['m']}</td><td>{z['k']}</td>"
                   f"<td class='{'up' if saldo > 0 else 'down' if saldo < 0 else ''}'>{saldo:+d}</td><td>{esc(tipo)}</td><td>{esc(arma)}</td></tr>")
    tipos_tot = {}
    for x in mortes:
        t = x.get("tipo") or tipo_de_duelo_hub(x)
        tipos_tot[t] = tipos_tot.get(t, 0) + 1
    resumo_t = " · ".join(f"{esc(k)}: {v}" for k, v in sorted(tipos_tot.items(), key=lambda kv: -kv[1]))
    corpo = (topo + f"<p class='sub'>{len(rs)} partida(s) em {esc(m)} · {len(mortes)} mortes · {len(kills)} kills. "
             "Cada quadrado do mapa é uma zona (letra = coluna, número = linha); quanto mais vermelho, mais você morre ali.</p>"
             + tabs + tabs2 + "".join(sv)
             + "<p class='nota'><span style='color:#3ddc97'>●</span> onde você estava ao fazer uma kill · "
               "<span style='color:#ff6b6b'>◆</span> onde você estava ao morrer. Passe o mouse para ver arma, tipo de duelo e distância. "
               "É um mapa de pontos e zonas: o jogo não entrega a imagem do radar.</p>"
             + "<h2>Zonas onde você mais morre</h2>"
             + ("<table><tr><th>Zona</th><th>Mortes</th><th>Kills</th><th>Saldo</th><th>Duelo mais comum</th><th>Arma que mais te mata</th></tr>"
                + linhas + "</table>" if linhas else "<div class='vazio'>Sem dados.</div>")
             + (f"<h2>Em que tipo de duelo você morre</h2><p>{resumo_t}</p>" if resumo_t else "")
             + "<p class='nota'>Saldo = kills menos mortes na zona. Zonas com saldo negativo e muitas mortes são as que valem treino ou outra forma de entrar.</p>")
    return pagina("Mapa de mortes", corpo, "/radar")


def tipo_de_duelo_hub(m):
    """Partidas analisadas antes do campo 'tipo' existir: calcula o rotulo aqui."""
    if m.get("abertura"):
        return "Abertura de round"
    if m.get("fora_visao"):
        return "Por trás/lado (fora da mira)"
    d = m.get("dist") or 0
    return "Longa distância" if d >= 25 else "Curta distância" if d < 8 else "Média distância"


def partidas_sem_detalhe(todos):
    return [r for r in todos if r["res"].get("kills_det") is None or r["res"].get("momentos") is None]


def _acha_demo(nome):
    for d in todas_as_demos():
        if d.name == nome:
            return d
    return None


def _celula_bloco(b, ant, chave, casas=0):
    v = b.get(chave)
    if v is None:
        return "<td class='sub'>—</td>"
    suf = metricas_mod.METRICAS[chave][1] if chave in metricas_mod.METRICAS else ""
    seta = ""
    if ant is not None and ant.get(chave) is not None:
        dif = v - ant[chave]
        if abs(dif) >= 10 ** -casas:
            melhor = metricas_mod.METRICAS[chave][2] if chave in metricas_mod.METRICAS else "alto"
            bom = (dif > 0) == (melhor == "alto")
            seta = f" <small class='{'up' if bom else 'down'}'>{'▲' if dif > 0 else '▼'}{abs(dif):.{casas}f}</small>"
    return f"<td>{v:.{casas}f}{esc(suf)}{seta}</td>"


def pg_evolucao():
    sid = escopo()
    todos = armazem.resultados(sid)
    topo = "<h1>Evolução</h1><div class='sub'>Como o seu jogo muda ao longo das partidas, em blocos de 15 e por mapa.</div>"
    if len(todos) < 2:
        return pagina("Evolução", topo + "<div class='aviso'>Preciso de pelo menos 2 partidas analisadas para mostrar a evolução. "
                      "Analise mais demos em <a href='/analisar'>Analisar demos</a>.</div>", "/evolucao")
    bl = metricas_mod.blocos(todos)
    cols = [("kd", "K/D", 2), ("adr", "ADR", 0), ("hs_kills", "HS kills", 0), ("hs_acertos", "HS/acerto", 0),
            ("ttk", "Tempo p/ matar", 0), ("rifle_parado", "Acerta parado", 0), ("rifle_correndo", "Corre atirando", 0),
            ("sem_atirar", "Morte sem atirar", 0), ("abertura", "Abertura", 0)]
    cab = "".join(f"<th>{esc(r)}</th>" for _, r, _ in cols)
    linhas = ""
    for i, b in enumerate(bl):
        ant = bl[i - 1] if i else None
        rot = f"{b['de']}–{b['ate']}" + ("" if b["completo"] else " (em andamento)")
        celulas = "".join(_celula_bloco(b, ant, c, casas) for c, _, casas in cols)
        linhas += (f"<tr><td><b>{esc(rot)}</b><div class='sub'>{data_br(b['inicio'])[:10]} → {data_br(b['fim'])[:10]}</div></td>"
                   f"<td>{b['n']}</td>{celulas}</tr>")
    tabela_b = (f"<h2>A cada {metricas_mod.TAMANHO_BLOCO} partidas</h2><table><tr><th>Partidas</th><th>Jogos</th>{cab}</tr>"
                f"{linhas}</table><p class='nota'>As setas comparam com o bloco anterior (verde = melhorou, vermelho = piorou, "
                "considerando que em algumas métricas menos é melhor). Métricas como tempo para matar só existem em partidas "
                "analisadas na versão nova.</p>")

    def pts_bloco(chave):
        return [(f"{b['de']}-{b['ate']}", b.get(chave)) for b in bl]

    graf_b = ("<p class='nota'>Os gráficos por bloco aparecem quando houver 2 blocos (30 partidas); até lá, veja a evolução "
              "partida a partida abaixo.</p>" if len(bl) < 2 else "") + ("" if len(bl) < 2 else "<div class='grid2'>" + grafico_linha(pts_bloco("kd"), "K/D por bloco", casas=2)
              + grafico_linha(pts_bloco("adr"), "ADR por bloco", casas=0, cor="#3ddc97")
              + grafico_linha(pts_bloco("rifle_parado"), "Rifle: acerta parado (%)", casas=0, cor="#ffb84c")
              + grafico_linha(pts_bloco("sem_atirar"), "Mortes sem atirar (%)", casas=0, cor="#ff6b6b") + "</div>")

    def pts_partida(chave):
        return [(f"{data_br(r['data_demo'])[:5]} {r['mapa'] or ''}", metricas_mod.valor(r["res"], chave)) for r in todos]

    graf_p = ("<h2>Partida a partida</h2><div class='grid2'>" + grafico_linha(pts_partida("kd"), "K/D", casas=2)
              + grafico_linha(pts_partida("adr"), "ADR", casas=0, cor="#3ddc97") + "</div>")
    mp = metricas_mod.por_mapa(todos)
    linhas_m = "".join(f"<tr><td><b>{esc(m['mapa'])}</b></td><td>{m['partidas']}</td><td>{m['kd']:.2f}</td>"
                       f"<td>{fmt(m['adr'], '', 0)}</td><td>{fmt(m['hs'])}</td><td>{fmt(m['abertura'])}</td>"
                       f"<td>{fmt(m['sem_atirar'])}</td></tr>" for m in mp)
    tabela_m = ("<h2>Por mapa</h2><table><tr><th>Mapa</th><th>Partidas</th><th>K/D</th><th>ADR</th><th>HS kills</th>"
                "<th>Abertura ganha</th><th>Morte sem atirar</th></tr>" + linhas_m + "</table>")
    velhas = partidas_sem_detalhe(todos)
    aviso = ""
    if velhas:
        aviso = (f"<div class='aviso'>{len(velhas)} partida(s) foram analisadas antes das métricas novas "
                 "(tempo para matar, kills detalhadas). "
                 + form_post("reanalisar-todas", "", "Reanalisar essas partidas", "")
                 + " <span class='sub'>As demos precisam estar nas pastas vigiadas.</span></div>")
    return pagina("Evolução", topo + aviso + tabela_b + graf_b + graf_p + tabela_m, "/evolucao")


# ---- plano de treino semanal ----
DIAS_SEMANA = ["Seg", "Ter", "Qua", "Qui", "Sex", "Sáb", "Dom"]


def _plano_carregar():
    try:
        return json.loads(armazem.ajuste_get("plano_treino") or "null")
    except ValueError:
        return None


def _plano_guardar(p):
    armazem.ajuste_set("plano_treino", json.dumps(p, ensure_ascii=False))


def plano_atual(forcar=False):
    """Plano vigente; cria um novo se nao ha, se venceu (7 dias) ou se pediram. None se faltam partidas."""
    sid = escopo()
    todos = armazem.resultados(sid)
    p = _plano_carregar()
    vencido = (not p) or p.get("steamid") != (sid or "") or date.today().isoformat() > p.get("valido_ate", "")
    if (vencido or forcar) and len(todos) >= 3:
        if p and p.get("itens"):
            try:
                hist = json.loads(armazem.ajuste_get("plano_historico") or "[]")
            except ValueError:
                hist = []
            hist.append({"criado": p["criado"], "itens": [i["titulo"] for i in p["itens"] if i.get("metrica")],
                         "avaliacao": metricas_mod.avaliar_plano(p, todos),
                         "feitos": sum(len(v) for v in (p.get("feito") or {}).values())})
            armazem.ajuste_set("plano_historico", json.dumps(hist[-8:], ensure_ascii=False))
        p = metricas_mod.gerar_plano(todos)
        p["steamid"] = sid or ""
        _plano_guardar(p)
    return p if p and p.get("steamid", "") == (sid or "") else None


def card_treino_hoje():
    """Resumo para a pagina inicial: o que treinar hoje."""
    try:
        p = plano_atual()
    except Exception:
        return ""
    if not p:
        return ""
    hoje = date.today()
    itens = metricas_mod.itens_de_hoje(p, hoje)
    feitos = p.get("feito") or {}
    pend = [i for i in itens if hoje.isoformat() not in feitos.get(i["id"], [])]
    if not itens:
        return ""
    txt = ("Tudo feito hoje 🎉" if not pend else "Hoje: " + ", ".join(f"{esc(i['titulo'])} ({i['minutos']} min)" for i in pend))
    return f"<div class='ok'>🎯 <b>Treino do dia</b> — {txt} · <a href='/treino'>abrir plano</a></div>"


def pg_treino():
    p = plano_atual()
    sid = escopo()
    todos = armazem.resultados(sid)
    topo = "<h1>Plano de treino</h1><div class='sub'>Montado a partir dos seus pontos fracos nas últimas partidas e renovado a cada semana.</div>"
    if not p:
        return pagina("Treino", topo + "<div class='aviso'>Preciso de pelo menos 3 partidas analisadas para montar o seu plano. "
                      "Analise mais demos em <a href='/analisar'>Analisar demos</a>.</div>", "/treino")
    hoje = date.today()
    feitos = p.get("feito") or {}
    itens_hoje = metricas_mod.itens_de_hoje(p, hoje)
    linhas_hoje = ""
    for it in itens_hoje:
        feito = hoje.isoformat() in feitos.get(it["id"], [])
        botao = ("<span class='pill ok'>feito hoje ✔</span> " if feito else "") + form_post(
            "treino-feito", f"<input type='hidden' name='id' value='{esc(it['id'])}'>",
            "Desmarcar" if feito else "Marcar como feito", "sec" if feito else "")
        linhas_hoje += (f"<tr><td><b>{esc(it['titulo'])}</b><div class='sub'>{esc(it['mapa'])}</div></td>"
                        f"<td>{it['minutos']} min</td><td>{botao}</td></tr>")
    total_min = sum(i["minutos"] for i in itens_hoje)
    hoje_html = (f"<h2>Hoje ({DIAS_SEMANA[hoje.weekday()]}) · {total_min} min</h2><table><tr><th>Treino</th><th>Tempo</th><th></th></tr>"
                 + linhas_hoje + "</table>")
    # semana
    cab = ""
    for i, dia_nome in enumerate(DIAS_SEMANA):
        estilo = " style='color:var(--ac)'" if i == hoje.weekday() else ""
        cab += f"<th{estilo}>{dia_nome}</th>"
    sem = ""
    for it in p["itens"]:
        cel = ""
        for dia in range(7):
            marca = "●" if dia in it.get("dias", []) else ""
            cel += f"<td>{marca}</td>"
        sem += f"<tr><td>{esc(it['titulo'])}</td>{cel}</tr>"
    semana_html = f"<h2>Semana</h2><table><tr><th>Treino</th>{cab}</tr>{sem}</table>"
    # detalhes
    ava = {a["id"]: a for a in metricas_mod.avaliar_plano(p, todos)}
    det = ""
    for it in p["itens"]:
        a = ava.get(it["id"])
        prog = ""
        if it.get("metrica"):
            rot, suf, melhor, _ = metricas_mod.METRICAS[it["metrica"]]
            prog = (f"<p class='nota'>{esc(rot)}: antes <b>{it['baseline']:.0f}{esc(suf)}</b> · meta <b>{it['alvo']:.0f}{esc(suf)}</b>")
            if a and a["atual"] is not None:
                cls = "ok" if a["melhorou"] else "ruim" if a["melhorou"] is False else ""
                prog += f" · depois do plano <span class='pill {cls}'>{a['atual']:.0f}{esc(suf)}</span> ({a['n']} partidas)"
            else:
                prog += " · <span class='sub'>aguardando 2 partidas novas para medir o efeito</span>"
            prog += "</p>"
        det += (f"<div class='dica p3'><h3>{esc(it['titulo'])} · {it['minutos']} min</h3>"
                f"<p><b>Onde:</b> {esc(it['mapa'])}</p><p><b>Como:</b> {esc(it['como'])}</p>"
                f"<p class='sub'>Por quê: {esc(it['motivo'])}</p>{prog}</div>")
    feitos_sem = sum(len(v) for v in feitos.values())
    cab_plano = (f"<p class='nota'>Plano de {data_br(p['criado'] + ' 00:00')[:10]} até {data_br(p['valido_ate'] + ' 00:00')[:10]}, baseado nas "
                 f"suas últimas {p.get('n_partidas_base', 0)} partidas. Treinos marcados: <b>{feitos_sem}</b>. "
                 + form_post("treino-novo", "", "Gerar plano novo agora", "sec", "Gerar um plano novo agora? O atual vai para o histórico.") + "</p>")
    try:
        hist = json.loads(armazem.ajuste_get("plano_historico") or "[]")
    except ValueError:
        hist = []
    hist_html = ""
    if hist:
        itens_h = ""
        for h in reversed(hist):
            res_h = ", ".join(
                f"{esc(metricas_mod.METRICAS[a['id']][0])}: {a['baseline']:.0f} → {a['atual']:.0f}"
                for a in h.get("avaliacao", []) if a.get("atual") is not None and a["id"] in metricas_mod.METRICAS) or "sem partidas novas para medir"
            itens_h += (f"<li>{data_br(h['criado'] + ' 00:00')[:10]} · {h.get('feitos', 0)} treinos marcados · {res_h}</li>")
        hist_html = f"<details><summary>Planos anteriores</summary><ul>{itens_h}</ul></details>"
    nota = ("<p class='nota'>Os mapas de treino (Aim Botz, Recoil Master, Yprac, 5E Prac) são mapas da Oficina do Steam "
            "feitos pela comunidade; o CS2 Coach só sugere, não baixa nada. Treinar em mapa da Oficina não conta nos números do Hub.</p>")
    return pagina("Treino", topo + cab_plano + hoje_html + semana_html + "<h2>O plano</h2>" + det + hist_html + nota, "/treino")


def pg_armas(q):
    ord_ = (q.get("ord") or ["nota"])[0]
    if ord_ not in armas_mod.ORDENACOES:
        ord_ = "nota"
    mapa = (q.get("mapa") or [""])[0]
    ult = (q.get("ult") or [""])[0]
    ultimas = int(ult) if ult.isdigit() else None
    sincronizar_sessoes()
    sid = escopo()
    agreg = armazem.armas_agregadas(sid, mapa or None, ultimas)
    if not agreg:
        return pagina("Armas", "<h1>Armas</h1><div class='vazio'>Ainda não há partidas analisadas. "
                      "Use <a href='/analisar'>Analisar demos</a>.</div>", "/armas")
    rk = armas_mod.ranking(agreg)
    fogo = armas_mod.ordenar([a for a in rk if a.get("tipo") == "arma"], ord_)
    outras = armas_mod.ordenar([a for a in rk if a.get("tipo") != "arma"], "dano")

    def link(**kw):
        p = {"ord": ord_, "mapa": mapa, "ult": ult}
        p.update(kw)
        return "/armas?" + "&".join(f"{k}={quote(str(v))}" for k, v in p.items() if v)

    abas = "".join(f'<a href="{link(ord=k)}" class="{"on" if k == ord_ else ""}">{esc(v[0])}</a>'
                   for k, v in armas_mod.ORDENACOES.items())
    periodo = "".join(f'<a href="{link(ult=v)}" class="{"on" if v == ult else ""}">{n}</a>'
                      for v, n in (("", "Todas as partidas"), ("10", "Últimas 10"), ("5", "Últimas 5")))
    mapas = armazem.mapas_disponiveis(sid)
    mapas_html = "".join(f'<a href="{link(mapa=m)}" class="{"on" if m == mapa else ""}">{esc(m)}</a>' for m in mapas)
    if mapas:
        mapas_html = f'<a href="{link(mapa="")}" class="{"" if mapa else "on"}">Todos os mapas</a>' + mapas_html
    melhores = [a for a in fogo if a["nota"] is not None][:3] if ord_ == "nota" else [a for a in fogo if not a["amostra_pequena"]][:3]
    cards = ""
    for i, a in enumerate(melhores, 1):
        extra = f"nota {a['nota']:.0f}" if a["nota"] is not None else f"{a['dano']} de dano"
        cards += (f"<div class='card'><b>{i}º {esc(a['nome'])}</b><span>{extra} · precisão {fmt(a['precisao'])} · "
                  f"{fmt(a['dano_por_tiro'], '', 1)} dano/tiro · {fmt(a['hs_pct'])} HS</span></div>")
    corpo = ("<h1>Armas</h1><div class='sub'>Dano causado por arma e ranking das suas melhores armas "
             f"({len(armazem.listar_partidas(sid, mapa or None, ultimas))} partidas consideradas).</div>"
             f"<div class='tabs'>{periodo}</div>" + (f"<div class='tabs'>{mapas_html}</div>" if mapas_html else "")
             + (f"<h2>Pódio</h2><div class='cards'>{cards}</div>" if cards else "")
             + f"<h2>Ranking</h2><div class='tabs'>Ordenar por: {abas}</div>"
             + tabela_armas(fogo, com_partidas=True, links=True)
             + "<p class='nota'><b>Como a nota é calculada:</b> média de quatro percentis entre as suas armas — dano por tiro, "
               "precisão (tiros que acertam), % de headshot e kills por 100 tiros. Só entram armas com 40+ tiros; "
               "ela mostra quais armas você usa melhor <i>em relação às suas outras</i>, não em relação a outros jogadores. "
               "Granadas, faca e Zeus aparecem abaixo, só com dano.<br><b>Ao vivo:</b> as sessões do painel ao vivo que ainda não têm demo "
               "entram nos totais de tiros e kills (indicados como “+N ao vivo”), mas não têm acertos nem dano, então "
               "não entram nas porcentagens nem na nota. Quando você analisa a demo, ela substitui a sessão (sem duplicar).</p>"
             + ("<h2>Granadas, faca e outros</h2>" + tabela_armas(outras, com_partidas=True) if outras else ""))
    return pagina("Armas", corpo, "/armas")


def pg_arma(nome):
    sid = escopo()
    linhas = armazem.arma_por_partida(nome, sid)
    if not linhas:
        return pagina("Arma", "<h1>Arma não encontrada</h1><a href='/armas'>Voltar</a>", "/armas"), 404
    n = armas_mod.nome_arma(nome)
    pts_dano, pts_prec, corpo_tab = [], [], ""
    for r in linhas:
        m = armas_mod.metricas(r)
        rot = f"{data_br(r['data_demo'])[:5]} {r['mapa'] or ''}"
        if r["tiros"] >= 10:
            pts_dano.append((rot, m["dano_por_tiro"]))
            pts_prec.append((rot, m["precisao"]))
        corpo_tab += (f"<tr><td><a href='/partida/{r['partida_id']}'>{data_br(r['data_demo'])}</a></td><td>{esc(r['mapa'] or '')}</td>"
                      f"<td>{r['tiros']}</td><td>{fmt(m['precisao'])}</td><td>{r['dano']}</td><td>{fmt(m['dano_por_tiro'], '', 1)}</td>"
                      f"<td>{r['kills']}</td><td>{fmt(m['hs_pct'])}</td></tr>")
    corpo = (f"<p><a href='/armas'>← Armas</a></p><h1>{esc(n)}</h1><div class='sub'>Desempenho partida a partida "
             "(gráficos usam partidas com 10+ tiros nesta arma).</div><div class='grid2'>"
             + grafico_linha(pts_prec, "Precisão (tiros que acertam)", "%", "#4cc2ff")
             + grafico_linha(pts_dano, "Dano por tiro", "", "#4cd68a", 1) + "</div>"
             "<h2>Por partida</h2><table><tr><th>Data</th><th>Mapa</th><th>Tiros</th><th>Precisão</th><th>Dano</th>"
             "<th>Dano/tiro</th><th>Kills</th><th>HS</th></tr>" + corpo_tab + "</table>")
    return pagina(n, corpo, "/armas"), 200


def pg_configuracoes():
    aj = armazem.ajustes_todos()
    leituras = armazem.ultimas_configs(30)
    if not leituras:
        try:
            conf = config_cs.ler_configuracoes(aj.get("pasta_cfg_usuario") or None, aj.get("pasta_cfg_jogo") or None)
            armazem.salvar_config(conf)
            leituras = armazem.ultimas_configs(30)
        except Exception as exc:  # leitura e opcional: a pagina deve abrir mesmo se falhar
            return pagina("Configurações", f"<h1>Configurações</h1><div class='aviso'>Não consegui ler as configurações: "
                          f"{esc(str(exc))}</div>", "/configuracoes")
    conf = leituras[0]
    sid = escopo()
    hist = armazem.historico(sid)
    rk = armas_mod.ranking(armazem.armas_agregadas(sid))
    tips, ok, r = config_cs.sugestoes(conf, ajustes_config(), hist, rk)

    cab = ("<h1>Configurações do CS2</h1><div class='sub'>O Hub lê os arquivos do seu jogo, explica cada ajuste em português "
           "e sugere mudanças. Ele <b>só lê</b>: nada é alterado nos seus arquivos. "
           f"Última leitura: {data_br(conf['lido_em'])}.</div><p>"
           + form_post("ler-config", "", "Reler configurações agora") + "</p>")
    avisos = "".join(f"<div class='aviso'>{esc(a)}</div>" for a in conf.get("avisos", []))

    def card(v, l, ajuda=""):
        return (f"<div class='card'><b>{esc(str(v))}</b><span>{esc(l)}</span>"
                + (f"<div class='expl'>{esc(ajuda)}</div>" if ajuda else "") + "</div>")

    cs = []
    if r.get("sensibilidade") is not None:
        origem = "" if r.get("sens_origem") == "arquivos do jogo" else f" (veio de: {r['sens_origem']})"
        cs.append(card(r["sensibilidade"], "Sensibilidade normal" + origem,
                       "Quanto a mira gira por movimento do mouse, no dia a dia (rifle, pistola...)."))
    else:
        cs.append(card("não achei", "Sensibilidade normal",
                       "Informe em Ajustes. O jogo costuma guardá-la no arquivo cs2_user_convars."))
    if r.get("zoom_ratio") is not None:
        extra = f" → sens com luneta {r['sens_zoom']}" if r.get("sens_zoom") else ""
        cs.append(card(r["zoom_ratio"], "Multiplicador da luneta (AWP/scout)" + extra,
                       "Com a luneta aberta, a sensibilidade é multiplicada por este número (e também depende do zoom). "
                       "1.0 = sem redução extra."))
    if r.get("edpi"):
        cs.append(card(r["edpi"], "eDPI (sens × DPI)",
                       "Número que permite comparar sua sensibilidade com a de outros jogadores, mesmo com mouses diferentes. "
                       "A faixa mais comum fica entre 600 e 1200."))
        cs.append(card(f"{r['cm_360']} cm", "Para girar 360°",
                       "Quanto você precisa deslizar o mouse para dar uma volta completa. Menos cm = mira mais rápida."))
    elif r.get("sensibilidade") is not None:
        cs.append(card("—", "eDPI", "Informe o DPI do mouse em Ajustes para calcular."))
    if r.get("resolucao"):
        cs.append(card(r["resolucao"], "Resolução", "Tamanho da imagem do jogo. Menor = mais FPS e inimigos mais largos."))
    if r.get("taxa_jogo_hz"):
        cs.append(card(f"{r['taxa_jogo_hz']} Hz", "Taxa de atualização no jogo",
                       "Quantas vezes por segundo a tela se atualiza. Deve ser igual ao Hz do seu monitor."))
    if r.get("fps_max") is not None:
        cs.append(card(int(r["fps_max"]), "Limite de FPS (fps_max)", "0 significa sem limite (o ideal para reduzir atraso)."))
    if r.get("mira_estilo") is not None:
        cs.append(card(r["mira_estilo"], "Estilo da mira",
                       "4 e 5 são estáticas (não abrem ao atirar); 0 a 3 mudam conforme o movimento."))
    resumo_html = ("<h2>Resumo: os números que mais importam</h2><div class='cards' style='grid-template-columns:repeat(auto-fit,minmax(230px,1fr))'>"
                   + "".join(cs) + "</div>")

    sug = "<h2>Sugestões de alteração</h2>"
    n_dados = sum(1 for x in tips if x.get("dados"))
    if len(hist) < 3:
        sug += (f"<div class='aviso'>Você tem {len(hist)} partida(s) analisada(s). Com <b>3 ou mais</b>, o Hub passa a cruzar "
                "suas configurações com o seu desempenho (acerto parado, mortes sem atirar, AWP...) e a sugerir ajustes "
                "personalizados. Quanto mais partidas, mais confiáveis ficam.</div>")
    elif n_dados:
        sug += f"<p class='nota'>{n_dados} sugestão(ões) abaixo vêm dos seus dados (selo roxo).</p>"
    if tips:
        for t in tips:
            selo = f"<span class='pill dados' title='{esc(t.get('base', ''))}'>baseada nas suas partidas</span>" if t.get("dados") else ""
            como = ""
            if t.get("como"):
                if ">" in t["como"]:      # caminho de menu
                    como = f"<p><b>Como fazer:</b> no menu do jogo, vá em <span class='cmd'>{esc(t['como'])}</span></p>"
                else:                     # comando de console
                    como = (f"<p><b>Como fazer:</b> abra o console do jogo (tecla ´ ou ') e digite "
                            f"<span class='cmd'>{esc(t['como'])}</span>. Para ficar salvo, ponha essa linha no seu autoexec.cfg.</p>")
            sug += (f"<div class='dica p{t['prioridade']}'><h3>{esc(t['titulo'])} <span class='pill'>{esc(t['categoria'])}</span> {selo}</h3>"
                    f"<p class='sub'><b>Hoje:</b> {esc(t['atual'])}</p><p><b>Sugestão:</b> {esc(t['sugestao'])}</p>"
                    f"<p><b>Por quê:</b> {esc(t['porque'])}</p>{como}</div>")
    else:
        sug += "<div class='vazio'>Nenhuma sugestão para o que foi encontrado nos arquivos.</div>"
    sug += "".join(f"<div class='ok'>✔ {esc(o)}</div>" for o in ok)
    sug += ("<p class='nota'>Prioridade: borda vermelha = vale mudar logo; amarela = vale testar; azul = preferência pessoal. "
            "São pistas baseadas em práticas comuns, não regras: mude <b>uma coisa de cada vez</b> e use a comparação abaixo "
            "para ver se suas partidas melhoraram. Se o DPI ou o Hz do monitor estiverem errados em "
            "<a href='/ajustes'>Ajustes</a>, algumas sugestões saem erradas.</p>")

    chrono = armazem.configs_cronologicas()
    mud = config_cs.mudancas(chrono)
    comp = "<h2>O que você mudou e como foram suas partidas</h2>"
    if mud:
        moms = armazem.momentos_partidas()
        for h in hist:
            h["momento"] = (moms.get(h.get("id")) or {}).get("momento")
        ad = config_cs.antes_depois(mud, hist)
        comp += "<table><tr><th>Quando</th><th>O que mudou</th><th>Antes da mudança</th><th>Depois da mudança</th><th>Diferença</th></tr>"
        for m in reversed(ad):
            itens = "<br>".join(f"{nome_chave(k)}: {esc(str(a))} → <b>{esc(str(b))}</b>" for k, a, b in m["itens"])
            da, dd = desempenho_armas(m["antes"]["ids"]), desempenho_armas(m["depois"]["ids"])

            def lado(x, d):
                linhas_ = [f"{x['n']} partida(s)", f"ADR {fmt(x['adr'], '', 0)} · K/D {fmt(x['kd'], '', 2)} · HS {fmt(x['hs'])}"]
                linhas_.append("Rifles: " + (f"{d['rifles'][0]:.0f}% acertam ({d['rifles'][1]} tiros)" if d["rifles"] else "poucos tiros"))
                linhas_.append("AWP: " + (f"{d['awp'][0]:.0f}% acertam ({d['awp'][1]} tiros)" if d["awp"] else "poucos tiros"))
                return "<br>".join(linhas_)

            def delta(nome, a, b, suf="", casas=0, inverter=False):
                if a is None or b is None:
                    return ""
                dif = b - a
                bom = dif < 0 if inverter else dif > 0
                cls = "up" if bom and abs(dif) >= 0.5 * 10 ** -casas else "down" if (not bom) and abs(dif) >= 0.5 * 10 ** -casas else ""
                seta = "▲" if dif > 0 else "▼" if dif < 0 else "•"
                return f"<div class='{cls}'>{nome}: {seta} {abs(dif):.{casas}f}{suf}</div>"
            dif_html = (delta("ADR", m["antes"]["adr"], m["depois"]["adr"]) + delta("K/D", m["antes"]["kd"], m["depois"]["kd"], "", 2)
                        + delta("HS", m["antes"]["hs"], m["depois"]["hs"], " pts")
                        + delta("Rifles", da["rifles"][0] if da["rifles"] else None, dd["rifles"][0] if dd["rifles"] else None, " pts")
                        + delta("AWP", da["awp"][0] if da["awp"] else None, dd["awp"][0] if dd["awp"] else None, " pts"))
            aviso = "" if m["confiavel"] else "<br><span class='pill'>poucas partidas: não conclua nada ainda</span>"
            fim = f"<br>até {data_br(m['ate'])}" if m.get("ate") else "<br>até agora"
            comp += (f"<tr><td>{data_br(m['para'])}{fim}</td><td>{itens}</td><td>{lado(m['antes'], da)}</td>"
                     f"<td>{lado(m['depois'], dd)}{aviso}</td><td>{dif_html or '—'}</td></tr>")
        comp += ("</table><p class='nota'>Cada partida é colocada na configuração que valia na hora em que foi jogada (pelo início da sessão ao vivo, quando existe; "
                 "senão pela data da demo, que é aproximada). O Hub confere seus arquivos do jogo a cada 30 segundos e registra qualquer mudança. "
                 "Poucas partidas deixam a comparação instável: use como pista, não como prova.</p>")
    else:
        comp += ("<div class='vazio'>Nenhuma mudança detectada ainda. Quando você alterar a sensibilidade, a mira ou o vídeo "
                 "e reler as configurações, o Hub compara suas partidas antes e depois.</div>")

    # ---- todas as configuracoes, agrupadas e explicadas
    conhecidas, outras, linhas_teclas = "", "", ""
    for grupo, nome_grupo in (("convars", "Jogo"), ("autoexec", "Autoexec"), ("video", "Vídeo")):
        for k, v in sorted((conf.get(grupo) or {}).items()):
            nome, expl = config_cs.explicar(k)
            origem = v["origem"]
            if nome:
                conhecidas += (f"<tr><td><b>{esc(nome)}</b><div class='expl'>{esc(expl)}</div></td><td><code>{esc(k)}</code></td>"
                               f"<td><b>{esc(str(v['valor']))}</b></td><td class='sub'>{esc(origem)}</td></tr>")
            else:
                outras += (f"<tr><td class='sub'>{nome_grupo}</td><td><code>{esc(k)}</code></td>"
                           f"<td>{esc(str(v['valor']))}</td><td class='sub'>{esc(origem)}</td></tr>")
    for k, v in sorted((conf.get("teclas") or {}).items()):
        acao = config_cs.explicar_tecla(v["valor"])
        linhas_teclas += (f"<tr><td><b>{esc(k)}</b></td><td>{esc(acao) or '—'}</td><td><code>{esc(str(v['valor']))}</code></td>"
                          f"<td class='sub'>{esc(v['origem'])}</td></tr>")
    arquivos = "".join(f"<li><code>{esc(a['nome'])}</code> <span class='sub'>({esc(a['modificado'])})</span></li>"
                       for a in conf.get("arquivos", []))
    todas = ("<h2>Todas as suas configurações</h2><input type='text' id='filtro' placeholder='Filtrar (ex.: sens, mira, volume, pular)...'>"
             "<h3>As que eu sei explicar</h3><table class='filtravel'><tr><th>O que é</th><th>Nome técnico</th><th>Seu valor</th><th>Onde está</th></tr>"
             + (conhecidas or "<tr><td colspan='4' class='sub'>nenhuma encontrada</td></tr>") + "</table>"
             + ("<h3>Teclas</h3><table class='filtravel'><tr><th>Tecla</th><th>O que faz</th><th>Comando</th><th>Onde está</th></tr>"
                + linhas_teclas + "</table>" if linhas_teclas else "")
             + (f"<details><summary>Outras configurações ({outras.count('<tr>')}) sem explicação cadastrada</summary>"
                "<table class='filtravel'><tr><th>Tipo</th><th>Nome</th><th>Valor</th><th>Onde está</th></tr>" + outras + "</table></details>"
                if outras else "")
             + f"<p class='nota'>Arquivos lidos:</p><ul>{arquivos or '<li>nenhum</li>'}</ul>"
             "<script>document.getElementById('filtro').addEventListener('input',function(e){var q=e.target.value.toLowerCase();"
             "if(q)document.querySelectorAll('details').forEach(function(d){d.open=true});"
             "document.querySelectorAll('table.filtravel tr').forEach(function(tr,i){if(tr.querySelector('th'))return;"
             "tr.style.display=tr.textContent.toLowerCase().indexOf(q)>-1?'':'none'})})</script>")
    return pagina("Configurações", cab + avisos + resumo_html + sug + comp + todas, "/configuracoes")


# ---- analise de demos -------------------------------------------------------
def _demos_do_caminho(texto):
    p = Path(texto.strip().strip('"')).expanduser()
    if p.is_dir():
        return sorted(p.glob("*.dem")), p
    if p.is_file() and p.suffix.lower() == ".dem":
        return [p], p.parent
    return [], p


def _log(msg):
    with E.lock:
        if E.tarefa is not None:
            E.tarefa["linhas"].append(f"{datetime.now():%H:%M:%S}  {msg}")


def _rodar_analise(demos, steamid, nick, refazer):
    atualizar_config("demo")     # a configuracao em uso agora entra no historico antes de analisar
    try:
        try:
            import analisar_demo as ad
        except (SystemExit, ImportError):
            _log("Faltam as bibliotecas. No terminal, rode:  pip install -r requirements.txt")
            return
        for d in demos:
            _log(f"Analisando {d.name} ...")
            try:
                pid, res, dicas, fortes, ja = ad.analisar_arquivo(d, steamid=None if nick else steamid,
                                                                  nome=nick or None, refazer=refazer)
                with E.lock:
                    if ja:
                        E.tarefa["ja"] += 1
                        _log(f"  {d.name}: já estava no Hub (marque 'refazer' para analisar de novo).")
                    else:
                        E.tarefa["ok"] += 1
                        g = res["geral"]
                        _log(f"  {d.name}: {g['kills']}/{g['mortes']} (K/D {g['kd']}), ADR {g['adr']:.0f} → partida nº {pid}")
            except ad.JogadorNaoEncontrado as e:
                with E.lock:
                    E.tarefa["erros"] += 1
                jogs = ", ".join(f"{n} ({s})" for s, n in e.jogadores[:12])
                if e.ambiguo:
                    _log(f"  {d.name}: mais de um jogador com esse nick: {jogs}. Use o SteamID em Ajustes.")
                else:
                    _log(f"  {d.name}: não achei você nesta demo. Jogadores: {jogs}. "
                         "Confira o SteamID em Ajustes ou digite o nick.")
            except Exception as exc:
                with E.lock:
                    E.tarefa["erros"] += 1
                _log(f"  {d.name}: erro — {exc}")
                traceback.print_exc()
    finally:
        with E.lock:
            E.tarefa["ativa"] = False
            E.tarefa["fim"] = time.time()
        _log("Concluído.")


def iniciar_analise(demos, steamid, nick, refazer, auto=False):
    with E.lock:
        if E.tarefa and E.tarefa.get("ativa"):
            return False
        E.tarefa = {"ativa": True, "linhas": [], "ok": 0, "ja": 0, "erros": 0, "total": len(demos), "inicio": time.time(),
                    "auto": auto}
    threading.Thread(target=_rodar_analise, args=(demos, steamid, nick, refazer), daemon=True).start()
    return True


def pg_analisar():
    aj = armazem.ajustes_todos()
    cadastradas = lista_pastas_demos()
    pasta = cadastradas[0] if cadastradas else ""
    sugestao = ""
    jogo = config_cs.achar_pasta_jogo_cfg()
    if jogo:
        sugestao = str(jogo.parent)       # ...\game\csgo (onde o jogo salva as demos baixadas)
    exemplo_pasta = sugestao or "C:\\...\\game\\csgo"
    campos_pasta = (f"<label>Pasta ou arquivo .dem (analisar agora)</label><input type='text' name='caminho' value='{esc(pasta)}' "
                    f"placeholder='{esc(exemplo_pasta)}'>")
    sid = steam_do_usuario()
    avisos = ""
    if not sid and not aj.get("nick"):
        avisos = ("<div class='aviso'>Não sei qual jogador é você. Vá em <a href='/ajustes'>Ajustes</a> e informe o SteamID, "
                  "ou digite o seu nick abaixo.</div>")
    form = ("<h2>Analisar</h2>"
            + f"<form method='post' action='/acao/analisar'><input type='hidden' name='t' value='{E.token}'>{campos_pasta}"
              "<label>Seu nick (opcional: o Hub já usa o seu SteamID)</label><input type='text' name='nick' value=''>"
              "<label><input type='checkbox' name='refazer' value='1' style='width:auto'> Refazer demos que já estão no Hub</label>"
              "<p><button>Analisar</button></p></form>" + bloco_pastas_demos("/analisar"))
    exemplos = ""
    demos = todas_as_demos()
    if demos:
        itens = "".join(f"<li><code>{esc(d.name)}</code> <span class='sub'>{esc(d.parent.name)} · {d.stat().st_size // 1_000_000} MB · "
                        f"{datetime.fromtimestamp(d.stat().st_mtime):%d/%m/%Y %H:%M}</span></li>" for d in demos[-15:])
        exemplos = (f"<h2>Demos nas pastas vigiadas ({len(demos)})</h2><ul>{itens}</ul>"
                    + form_post("analisar-todas", "", "Analisar todas as demos das pastas", ""))
    with E.lock:
        t = dict(E.tarefa) if E.tarefa else None
        linhas = list(t["linhas"]) if t else []
    status = ""
    if t:
        estado = "em andamento..." if t["ativa"] else "concluída"
        status = (f"<h2>Última análise ({estado})</h2><div class='cards'><div class='card'><b>{t['ok']}</b><span>novas</span></div>"
                  f"<div class='card'><b>{t['ja']}</b><span>já estavam no Hub</span></div>"
                  f"<div class='card'><b>{t['erros']}</b><span>com erro</span></div>"
                  f"<div class='card'><b>{t['total']}</b><span>demos</span></div></div><pre>{esc(chr(10).join(linhas))}</pre>")
    ajuda = ("<p class='nota'>Onde achar as demos: no CS2, abra Seu perfil &gt; Partidas, escolha a partida e clique em baixar. "
             "O arquivo .dem fica na pasta do jogo (<code>...\\Counter-Strike Global Offensive\\game\\csgo</code>). "
             "Demos de partidas de matchmaking ficam disponíveis por tempo limitado, então baixe logo depois de jogar.</p>")
    return pagina("Analisar demos", "<h1>Analisar demos</h1>" + banner_auto() + avisos + form + status + exemplos + ajuda,
                  "/analisar", refresh=2 if t and t["ativa"] else None)


# ---- aviso de versao nova (GitHub Releases) ----
def _numeros(v):
    return tuple(int(x) for x in re.findall(r"\d+", str(v))[:4]) or (0,)


def repo_github():
    r = (armazem.ajuste_get("repo_github") or REPO or "").strip().strip("/")
    return r if re.fullmatch(r"[A-Za-z0-9_-][A-Za-z0-9_.-]*/[A-Za-z0-9_-][A-Za-z0-9_.-]*", r) else ""


def verificar_atualizacao(base=None):
    """Pergunta ao GitHub qual e a versao mais recente publicada. So le; nao envia nenhum dado seu."""
    import urllib.request
    repo = repo_github()
    if not repo:
        E.update_erro = "repositório não informado"
        return None
    base = base or os.environ.get("CS2COACH_API") or "https://api.github.com"
    req = urllib.request.Request(f"{base}/repos/{repo}/releases/latest",
                                 headers={"Accept": "application/vnd.github+json", "User-Agent": f"CS2Coach/{VERSAO}"})
    try:
        with urllib.request.urlopen(req, timeout=8) as r:
            d = json.loads(r.read().decode("utf-8", "replace"))
    except Exception as exc:
        E.update_erro = f"não consegui consultar o GitHub ({exc})"
        return None
    E.update_erro = None
    nova = str(d.get("tag_name") or "")
    if not nova or _numeros(nova) <= _numeros(VERSAO):
        E.update = None
        return None
    url = str(d.get("html_url") or "")
    baixar = next((a.get("browser_download_url") for a in d.get("assets", [])
                   if str(a.get("name", "")).lower().endswith("setup.exe")), None) or url
    if not (url.startswith("https://github.com/") and str(baixar).startswith("https://github.com/")):
        if not base.startswith("http://127.0.0.1"):       # (so os testes usam um servidor local)
            return None
    E.update = {"versao": nova.lstrip("vV"), "url": url, "download": baixar,
                "notas": str(d.get("body") or "")[:600]}
    return E.update


def _bloco_baixar():
    """Botoes do aviso de versao nova: baixar dentro do proprio app e instalar."""
    u = E.update or {}
    dl = E.dl or {}
    estado = dl.get("estado")
    if estado == "baixando":
        total = dl.get("total") or 0
        pct = f"{int(dl['bytes'] * 100 / total)}%" if total else f"{dl['bytes'] // 1_000_000} MB"
        return f"<b>Baixando a atualização... {pct}</b>"
    if estado == "pronto":
        return (form_post("instalar-atualizacao", "", "Instalar agora (reinicia o CS2 Coach)", "")
                + " <span class='sub'>já baixada</span>")
    erro = (f"<div class='expl'>Não consegui baixar: {esc(dl.get('erro', ''))}. Você ainda pode "
            f"<a href='{esc(u.get('download', ''))}' target='_blank' rel='noopener'>baixar pelo navegador</a>.</div>"
            if estado == "erro" else "")
    return form_post("baixar-atualizacao", "", "Baixar e instalar agora", "") + erro


def baixar_atualizacao():
    """Baixa o instalador da versao nova (so de github.com) para a pasta temporaria. Roda em segundo plano."""
    import tempfile
    import urllib.request
    u = E.update
    if not u:
        return False
    url = str(u.get("download") or "")
    if not (url.startswith("https://github.com/") or url.startswith("http://127.0.0.1")):
        return False
    with E.lock:
        if E.dl and E.dl.get("estado") == "baixando":
            return True
        pasta = Path(tempfile.gettempdir()) / "CS2Coach-atualizacao"
        E.dl = {"estado": "baixando", "bytes": 0, "total": 0, "arquivo": str(pasta / "CS2Coach-Setup.exe"), "erro": ""}

    def rodar():
        try:
            pasta.mkdir(parents=True, exist_ok=True)
            destino = Path(E.dl["arquivo"])
            parcial = destino.with_suffix(".baixando")
            req = urllib.request.Request(url, headers={"User-Agent": f"CS2Coach/{VERSAO}"})
            with urllib.request.urlopen(req, timeout=30) as r, open(parcial, "wb") as f:
                E.dl["total"] = int(r.headers.get("Content-Length") or 0)
                while True:
                    bloco = r.read(256 * 1024)
                    if not bloco:
                        break
                    f.write(bloco)
                    E.dl["bytes"] += len(bloco)
            if E.dl["total"] and E.dl["bytes"] != E.dl["total"]:
                raise OSError("download incompleto")
            with open(parcial, "rb") as f:
                if f.read(2) != b"MZ" or E.dl["bytes"] < 500_000:
                    raise OSError("o arquivo baixado não parece um instalador")
            os.replace(parcial, destino)
            E.dl["estado"] = "pronto"
        except Exception as exc:
            E.dl["estado"] = "erro"
            E.dl["erro"] = str(exc)[:120]

    threading.Thread(target=rodar, daemon=True).start()
    return True


def _lancar_instalador(caminho):
    """Abre o instalador sozinho (sem janela de perguntas) e devolve True se conseguiu."""
    if os.name != "nt":
        return False
    import subprocess
    flags = 0x00000008 | 0x00000200            # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
    subprocess.Popen([str(caminho), "/SILENT", "/CLOSEAPPLICATIONS"], creationflags=flags, close_fds=True)
    return True


def instalar_atualizacao():
    dl = E.dl or {}
    arq = Path(dl.get("arquivo") or "")
    if dl.get("estado") != "pronto" or not arq.is_file():
        return "Baixe a atualização primeiro."
    if not _lancar_instalador(arq):
        return "A instalação automática só funciona no Windows. Abra o arquivo baixado: " + esc(str(arq))

    def sair():
        if E.coach is not None:
            with contextlib.suppress(Exception):
                with E.coach.lock:
                    E.coach._fechar_rajada()
                    E.coach._encerrar_partida()
        os._exit(0)
    threading.Timer(1.5, sair).start()
    return ""


def vigia_atualizacao():
    time.sleep(20)
    while True:
        try:
            if (armazem.ajuste_get("avisar_atualizacao") or "1") != "0":
                verificar_atualizacao()
        except Exception:
            traceback.print_exc()
        time.sleep(6 * 3600)


# ---- importacao automatica de demos ----
def lista_pastas_demos():
    """Pastas de demos cadastradas pelo usuario (varias). Mantem compatibilidade com o ajuste antigo (uma so)."""
    bruto = (armazem.ajuste_get("pastas_demos") or "") + "\n" + (armazem.ajuste_get("pasta_demos") or "")
    out = []
    for linha in bruto.splitlines():
        t = linha.strip().strip('"')
        if t and t not in out:
            out.append(t)
    return out


def salvar_pastas_demos(lista):
    limpas = []
    for t in lista:
        t = t.strip().strip('"')
        if t and t not in limpas:
            limpas.append(t)
    armazem.ajuste_set("pastas_demos", "\n".join(limpas))
    armazem.ajuste_set("pasta_demos", "")      # o ajuste antigo foi absorvido pela lista


def listar_dems(pasta, profundidade=2):
    """.dem da pasta e de subpastas (ate `profundidade` niveis), para achar demos organizadas em subpastas."""
    achados = []
    try:
        for item in pasta.iterdir():
            if item.is_file() and item.suffix.lower() == ".dem":
                achados.append(item)
            elif item.is_dir() and profundidade > 0 and not item.name.startswith("."):
                achados.extend(listar_dems(item, profundidade - 1))
    except OSError:
        pass
    return achados


def pastas_de_demos():
    cands = [Path(t) for t in lista_pastas_demos()]
    jogo = config_cs.achar_pasta_jogo_cfg()
    if jogo:
        cands.append(jogo.parent)          # ...\game\csgo: onde o CS2 salva as demos baixadas
    out = []
    for c in cands:
        if c.is_dir() and c not in out:
            out.append(c)
    return out


def todas_as_demos():
    jogo = config_cs.achar_pasta_jogo_cfg()
    pasta_jogo = jogo.parent if jogo else None
    vistos, out = set(), []
    for pasta in pastas_de_demos():
        for d in listar_dems(pasta, 0 if pasta == pasta_jogo else 2):   # a pasta do jogo tem subpastas que nao interessam
            if d not in vistos:
                vistos.add(d)
                out.append(d)

    def quando(d):
        try:
            return d.stat().st_mtime
        except OSError:
            return 0
    return sorted(out, key=quando)


def bloco_pastas_demos(volta):
    """Gerenciador de pastas de demos (formularios proprios; nao pode ficar dentro de outro <form>)."""
    cadastradas = lista_pastas_demos()
    jogo = config_cs.achar_pasta_jogo_cfg()
    linhas = ""
    for i, t in enumerate(cadastradas):
        existe = Path(t).is_dir()
        linhas += (f"<li><code>{esc(t)}</code> " + ("" if existe else "<span class='sub'>(pasta não encontrada)</span> ")
                   + form_post("rem-pasta", f"<input type='hidden' name='i' value='{i}'><input type='hidden' name='volta' value='{esc(volta)}'>",
                               "Remover", "sec") + "</li>")
    if jogo:
        linhas += f"<li><code>{esc(str(jogo.parent))}</code> <span class='sub'>(pasta do jogo, sempre vigiada)</span></li>"
    if not linhas:
        linhas = "<li>nenhuma pasta ainda</li>"
    add = (f"<form method='post' action='/acao/add-pasta'><input type='hidden' name='t' value='{E.token}'>"
           f"<input type='hidden' name='volta' value='{esc(volta)}'>"
           "<label>Adicionar pasta de demos</label><input type='text' name='caminho' placeholder='D:\\Demos\\Faceit'>"
           "<small>Pode adicionar quantas quiser, de discos diferentes. O Hub também olha as subpastas (2 níveis).</small>"
           "<p><button>Adicionar pasta</button></p></form>")
    return ("<h2>Pastas de demos</h2><ul>" + linhas + "</ul>" + add)


def auto_ligado():
    return (armazem.ajuste_get("auto_demos") or "1") != "0"


def vigia_demos():
    """De 15 em 15 segundos procura .dem novos nas pastas conhecidas e analisa sozinho."""
    while True:
        time.sleep(15)
        try:
            if not auto_ligado():
                continue
            sid = steam_do_usuario()
            if not sid:
                continue
            with E.lock:
                if E.tarefa and E.tarefa.get("ativa"):
                    continue
            agora, novas = time.time(), []
            for d in todas_as_demos():
                try:
                    st = d.stat()
                except OSError:
                    continue
                sig = (st.st_size, int(st.st_mtime))
                if E.auto["vistos"].get(str(d)) == sig:
                    continue
                if agora - st.st_mtime < 20:          # ainda esta sendo gravada pelo jogo
                    continue
                E.auto["vistos"][str(d)] = sig
                if agora - st.st_mtime > 30 * 86400:  # demos muito antigas: so sob pedido
                    continue
                novas.append(d)
            if novas:
                E.auto["ultima"] = time.time()
                iniciar_analise(novas, sid, None, False, auto=True)
        except Exception:
            traceback.print_exc()


def banner_auto():
    pastas = pastas_de_demos()
    if not auto_ligado():
        return ("<div class='aviso'>Importação automática de demos: <b>desligada</b>. "
                "Ligue em <a href='/ajustes'>Ajustes</a> para o Hub analisar as demos novas sozinho.</div>")
    if not pastas:
        return ("<div class='aviso'>Importação automática: não achei a pasta das demos. "
                "Informe-a em <a href='/ajustes'>Ajustes</a>.</div>")
    with E.lock:
        t = dict(E.tarefa) if E.tarefa and E.tarefa.get("auto") else None
    extra = ""
    if t:
        extra = (f" Última importação automática: {t['ok']} nova(s)" + (", em andamento..." if t["ativa"] else ".")
                 + " <a href='/analisar'>ver</a>")
    return ("<div class='ok'>Importação automática de demos <b>ligada</b>: o Hub analisa sozinho as demos novas que aparecem em "
            + ", ".join(f"<code>{esc(str(p))}</code>" for p in pastas) + "." + extra + "</div>")


def instalar_gsi_texto():
    saida = io.StringIO()
    try:
        import ao_vivo
        with contextlib.redirect_stdout(saida):
            ao_vivo.instalar(E.porta_gsi, E.gsi_token)
        return esc(saida.getvalue().strip()).replace("\n", "<br>")
    except Exception as exc:
        return f"erro: {esc(str(exc))}"


# ---- configuracao usada em cada momento ----
RIFLES = {"ak47", "m4a1", "m4a1_silencer", "galilar", "famas", "aug", "sg556"}


def nome_chave(k):
    n, _ = config_cs.explicar(k)
    return f"{esc(n)} (<code>{esc(k)}</code>)" if n else f"<code>{esc(k)}</code>"


def cfg_curta(conf):
    if not conf:
        return "—"
    r = config_cs.resumo(conf, ajustes_config())
    s = f"sens {r['sensibilidade']:g}" if r.get("sensibilidade") is not None else "sens ?"
    if r.get("zoom_ratio") is not None:
        s += f" · luneta ×{esc(str(r['zoom_ratio']))}"
    if r.get("edpi"):
        s += f" · eDPI {r['edpi']}"
    return s


def bloco_config(momento, preciso, conf_ant=None, titulo="Configuração usada nesta partida"):
    """Cartões da configuração que valia no momento + o que mudou em relação à partida anterior."""
    cron = armazem.configs_cronologicas()
    conf = armazem.escolher_config(cron, momento)
    quando = ("início da sessão ao vivo" if preciso else "data da demo (aproximada: é a hora em que o arquivo foi baixado)")
    if not conf:
        desde = data_br(cron[0]["vigente_desde"]) if cron else None
        return (f"<h2>{esc(titulo)}</h2><div class='aviso'>Não tenho registro da configuração para esta data. "
                + (f"O Hub só conhece as configurações a partir de {desde}; " if desde else "O Hub ainda não leu suas configurações; ")
                + "daqui para frente cada partida terá a sua registrada (o Hub confere os arquivos do jogo a cada 30 segundos "
                  "enquanto estiver aberto).</div>")
    r = config_cs.resumo(conf, ajustes_config())

    def card(v, l):
        return f"<div class='card'><b>{esc(str(v))}</b><span>{esc(l)}</span></div>"
    cs = [card(f"{r['sensibilidade']:g}" if r.get("sensibilidade") is not None else "?", "Sensibilidade normal")]
    if r.get("zoom_ratio") is not None:
        cs.append(card(r["zoom_ratio"], "Multiplicador da luneta (AWP)"))
    if r.get("edpi"):
        cs.append(card(r["edpi"], "eDPI"))
        cs.append(card(f"{r['cm_360']} cm", "Para girar 360°"))
    if r.get("resolucao"):
        cs.append(card(r["resolucao"], "Resolução"))
    if r.get("taxa_jogo_hz"):
        cs.append(card(f"{r['taxa_jogo_hz']} Hz", "Taxa no jogo"))
    if r.get("mira_estilo") is not None:
        cs.append(card(r["mira_estilo"], "Estilo da mira"))
    html_ = (f"<h2>{esc(titulo)}</h2><p class='nota'>Registrada em {data_br(conf['lido_em'])}, vale desde "
             f"{data_br(conf['vigente_desde'])}. Momento considerado: {quando}.</p><div class='cards'>" + "".join(cs) + "</div>")
    if conf_ant is not None and conf_ant.get("id") != conf.get("id"):
        d = config_cs.diferencas(conf_ant, conf)
        if d:
            html_ += ("<div class='aviso'><b>O que mudou desde a partida anterior:</b><br>"
                      + "<br>".join(f"{nome_chave(k)}: {esc(str(a))} → <b>{esc(str(b))}</b>" for k, a, b in d) + "</div>")
    return html_ + "<p class='nota'><a href='/configuracoes'>Ver todas as configurações e o histórico de mudanças →</a></p>"


def desempenho_armas(ids):
    rows = armazem.armas_de_partidas(ids)

    def agg(nomes):
        tiros = sum(r["tiros"] or 0 for r in rows if r["arma"] in nomes)
        hits = sum(r["tiros_com_acerto"] or 0 for r in rows if r["arma"] in nomes)
        return (100.0 * hits / tiros, tiros) if tiros >= 20 else None
    return {"awp": agg({"awp"}), "rifles": agg(RIFLES)}


def _tpk(s):
    d = s["dados"]
    k = d.get("kills") or 0
    tiros = d.get("tiros_total") or sum((v.get("tiros") or 0) for v in (d.get("por_arma") or {}).values())
    return (tiros / k) if k >= 5 and tiros else None


def grafico_treinos(treinos):
    """Evolucao nos treinos: tiros por kill (quanto menor, melhor) e kills por minuto, sessao a sessao."""
    cron = sorted(treinos, key=lambda s: s["iniciado"] or "")
    pts_tpk, pts_kpm = [], []
    for s in cron:
        d = armazem.obter_sessao(s["id"])["dados"]
        rot = f"{data_br(s['iniciado'])[:5]} {s['mapa'] or ''}"
        k, dur = d.get("kills") or 0, d.get("duracao_min") or 0
        pts_tpk.append((rot, _tpk({"dados": d})))
        pts_kpm.append((rot, round(k / dur, 1) if dur >= 3 else None))
    return ("<div class='grid2'>" + grafico_linha(pts_tpk, "Treino: tiros por kill (menor = melhor)", "", "#ffc857", 1)
            + grafico_linha(pts_kpm, "Treino: kills por minuto", "", "#4cd68a", 1) + "</div>")


# ---- ao vivo ----
def bloco_sessao(s, completo=True):
    """Dados que o painel ao vivo registrou (cards, armas, rajadas, tabelas, dicas)."""
    d = s["dados"]
    cards = "<div class='cards'>" + "".join(f"<div class='card'><b>{esc(str(v))}</b><span>{esc(str(l))}</span></div>"
                                            for v, l in d.get("cards", [])) + "</div>"
    pa = d.get("por_arma") or {}
    armas_t = ""
    if pa:
        linhas = "".join(f"<tr><td style='white-space:nowrap'>{icone_arma(a)}{esc(armas_mod.nome_arma(a))}</td>"
                         f"<td>{v.get('tiros', 0)}</td><td>{v.get('kills', 0)}</td></tr>"
                         for a, v in sorted(pa.items(), key=lambda kv: -((kv[1].get('kills') or 0) * 1000 + (kv[1].get('tiros') or 0))))
        armas_t = ("<h2>Armas nesta sessão</h2><table><tr><th>Arma</th><th>Tiros</th><th>Kills</th></tr>" + linhas + "</table>"
                   "<p class='nota'>O jogo informa ao painel só os tiros e as kills; acertos e dano por arma só vêm da demo.</p>")
    raj = d.get("rajadas_auto") or {}
    rajadas = ""
    if raj and (raj.get("tap") or raj.get("curta") or raj.get("media") or raj.get("longa")):
        rajadas = ("<h2>Rajadas automáticas</h2><div class='cards'>"
                   f"<div class='card'><b>{raj.get('tap', 0)}</b><span>tiros soltos (tap)</span></div>"
                   f"<div class='card'><b>{raj.get('curta', 0)}</b><span>rajadas curtas (2-3)</span></div>"
                   f"<div class='card'><b>{raj.get('media', 0)}</b><span>rajadas médias (4-7)</span></div>"
                   f"<div class='card'><b>{raj.get('longa', 0)}</b><span>rajadas longas (8+)</span></div></div>")
    dicas = ("<h2>Dicas da sessão</h2><ul>" + "".join(f"<li>{esc(x)}</li>" for x in d.get("dicas", [])) + "</ul>") if d.get("dicas") else ""
    tabs = ""
    if completo:
        for tb in d.get("tabelas", []):
            if not tb.get("linhas"):
                continue
            tabs += (f"<h2>{esc(tb['titulo'])}</h2><table><tr>" + "".join(f"<th>{esc(c)}</th>" for c in tb["colunas"]) + "</tr>"
                     + "".join("<tr>" + "".join(f"<td>{esc(str(c))}</td>" for c in row) + "</tr>" for row in tb["linhas"]) + "</table>")
    return cards + armas_t + rajadas + dicas + tabs


def pg_ao_vivo():
    sincronizar_sessoes()
    if E.coach is not None:
        painel = (f"<h2>Painel ao vivo</h2><p class='sub'>Rodando dentro do Hub. Abra o CS2 e entre numa partida; "
                  f"os dados chegam em <code>127.0.0.1:{E.porta_gsi}</code>. "
                  f"<a href='http://127.0.0.1:{E.porta_gsi}/' target='_blank'>Abrir em outra aba</a> "
                  "(boa ideia para o segundo monitor ou o celular).</p>"
                  f"<iframe src='http://127.0.0.1:{E.porta_gsi}/' title='Painel ao vivo'></iframe>")
    else:
        painel = ("<h2>Painel ao vivo</h2><div class='aviso'>O painel ao vivo não está rodando dentro do Hub"
                  + (f": {esc(E.gsi_erro)}" if E.gsi_erro else "") + ". Se você abriu o <code>ao_vivo.py</code> em outra janela, "
                  "ele continua funcionando por lá e as sessões aparecem aqui.</div>")
    cron_ = armazem.configs_cronologicas()
    agora_cfg = (bloco_config(cron_[-1]["vigente_desde"], True, titulo="Configuração em uso agora") if cron_ else "")
    instalar = ("<h2>Conexão com o jogo</h2><p>O jogo precisa do arquivo <code>gamestate_integration_cs2coach.cfg</code> na pasta "
                "<code>game\\csgo\\cfg</code>. " + form_post("instalar-gsi", "", "Instalar / reinstalar o arquivo", "sec") + "</p>")
    ss = armazem.listar_sessoes(200)
    contam = armazem.modos_que_contam()

    def grupo(s):
        if s["modo"] in contam:
            return "oficial"
        return "treino" if s["modo"] == "treino" else "diversao"

    def estado(s):
        if grupo(s) != "oficial":
            return "<span class='pill'>fora da contagem do Hub</span>"
        if s.get("partida_id"):
            return f"<span class='pill ok'>completa com a demo <a href='/partida/{s['partida_id']}'>nº {s['partida_id']}</a></span>"
        return "<span class='pill live'>sem demo: conta só tiros e kills</span>"

    def tabela_sessoes(lista, com_estado=True):
        linhas = "".join(
            f"<tr><td>{data_br(s['iniciado'])}</td><td>{esc(s['mapa'] or '—')}</td><td>{esc(s['modo_nome'] or s['modo'] or '—')}</td>"
            f"<td>{s['kills']}/{s['mortes']}</td><td class='sub'>{cfg_curta(armazem.escolher_config(cron_, s['iniciado']))}</td>"
            f"<td>{estado(s) if com_estado else ''}</td><td><a href='/sessao/{s['id']}'>Abrir</a></td></tr>" for s in lista)
        return ("<table><tr><th>Início</th><th>Mapa</th><th>Modo</th><th>K/M</th><th>Configuração</th><th>Situação</th><th></th></tr>"
                + linhas + "</table>")

    of = [s for s in ss if grupo(s) == "oficial"]
    tr = [s for s in ss if grupo(s) == "treino"]
    dv = [s for s in ss if grupo(s) == "diversao"]
    if not ss:
        sessoes = "<h2>Sessões ao vivo salvas</h2><div class='vazio'>As sessões são salvas quando uma partida termina.</div>"
    else:
        sessoes = ("<h2>Partidas que valem para o Hub</h2>"
                   + (tabela_sessoes(of) if of else "<div class='vazio'>Nenhuma ainda.</div>")
                   + "<p class='nota'>Competitivo/Premier e Braço Direito (você pode mudar quais modos contam em "
                     "<a href='/ajustes'>Ajustes</a>). Quando você analisar a demo da mesma partida (mesmo mapa e mesmo número de kills "
                     "e mortes), o Hub liga as duas sozinho: a demo vale para os números e o ao vivo vira complemento, sem duplicar.</p>")
        if tr:
            sessoes += ("<h2>Treinos (detalhados, fora da contagem)</h2>" + grafico_treinos(tr) + tabela_sessoes(tr)
                        + "<p class='nota'>Treino e mapas da oficina não entram em Visão geral, Armas nem no ranking: aqui você vê só o seu "
                          "progresso no treino. Abra uma sessão para ver minuto a minuto e arma por arma.</p>")
        if dv:
            sessoes += ("<h2>Modos de diversão (fora da contagem)</h2>" + tabela_sessoes(dv))
    return pagina("Ao vivo", "<h1>Ao vivo</h1>" + painel + agora_cfg + instalar + sessoes, "/ao-vivo")


def bloco_treino(s):
    """Detalhes extras das sessoes de treino: minuto a minuto, arma por arma e ritmo de tiros por kill."""
    d = s["dados"]
    mins = d.get("minutos") or []
    pa = d.get("por_arma") or {}
    dur = d.get("duracao_min")
    kills = d.get("kills") or 0
    tiros = d.get("tiros_total") or sum((v.get("tiros") or 0) for v in pa.values())
    out = "<h2>Treino em detalhe</h2><div class='cards'>"
    out += f"<div class='card'><b>{dur if dur is not None else '—'}</b><span>minutos de treino</span></div>"
    out += f"<div class='card'><b>{kills}</b><span>kills</span></div>"
    out += f"<div class='card'><b>{tiros or '—'}</b><span>tiros contados</span></div>"
    out += f"<div class='card'><b>{(tiros / kills):.1f}</b><span>tiros por kill</span></div>" if kills and tiros else ""
    out += (f"<div class='card'><b>{kills / dur:.1f}</b><span>kills por minuto</span></div>" if dur and dur >= 1 and kills else "")
    out += "</div>"
    if mins:
        pts_tpk = [(f"min {m['min']}", (m["tiros"] / m["kills"]) if m.get("kills") and m.get("tiros") else None) for m in mins]
        pts_k = [(f"min {m['min']}", m.get("kills")) for m in mins]
        out += ("<div class='grid2'>" + grafico_linha(pts_k, "Kills por minuto", "", "#4cd68a")
                + grafico_linha(pts_tpk, "Tiros por kill, minuto a minuto", "", "#ffc857", 1) + "</div>")
        linhas = "".join(f"<tr><td>{m['min']}</td><td>{m.get('kills', 0)}</td><td>{m.get('tiros', 0)}</td>"
                         f"<td>{(m['tiros'] / m['kills']):.1f}</td><td>{m.get('longas', 0)}</td></tr>"
                         if m.get("kills") and m.get("tiros") else
                         f"<tr><td>{m['min']}</td><td>{m.get('kills', 0)}</td><td>{m.get('tiros', 0)}</td><td>—</td><td>{m.get('longas', 0)}</td></tr>"
                         for m in mins)
        out += ("<h3>Minuto a minuto</h3><table><tr><th>Minuto</th><th>Kills</th><th>Tiros</th><th>Tiros/kill</th><th>Rajadas 7+</th></tr>"
                + linhas + "</table>")
    if pa:
        linhas = "".join(f"<tr><td style='white-space:nowrap'>{icone_arma(a)}{esc(armas_mod.nome_arma(a))}</td><td>{v.get('tiros', 0)}</td>"
                         f"<td>{v.get('kills', 0)}</td><td>{(v['tiros'] / v['kills']):.1f}</td></tr>" if v.get("kills") and v.get("tiros") else
                         f"<tr><td style='white-space:nowrap'>{icone_arma(a)}{esc(armas_mod.nome_arma(a))}</td><td>{v.get('tiros', 0)}</td><td>{v.get('kills', 0)}</td><td>—</td></tr>"
                         for a, v in sorted(pa.items(), key=lambda kv: -((kv[1].get("tiros") or 0))))
        out += ("<h3>Arma por arma</h3><table><tr><th>Arma</th><th>Tiros</th><th>Kills</th><th>Tiros/kill</th></tr>" + linhas + "</table>")
    if d.get("municao_infinita"):
        out += "<div class='aviso'>Este mapa tem munição infinita: o jogo não informa os tiros, então só as kills foram contadas.</div>"
    return out


def pg_sessao(sid):
    s = armazem.obter_sessao(sid)
    if not s:
        return pagina("Sessão", "<h1>Sessão não encontrada</h1><a href='/ao-vivo'>Voltar</a>", "/ao-vivo"), 404
    contam = armazem.modos_que_contam()
    treino = s["modo"] == "treino"
    if s["modo"] not in contam:
        estado = ("<div class='aviso'>" + ("<b>Treino:</b> esta sessão tem mais detalhes que as demais, mas " if treino
                                            else "<b>Modo de diversão:</b> ") + "não entra nos números do Hub (Visão geral, Armas e ranking).</div>")
    elif s.get("partida_id"):
        estado = (f"<div class='ok'>Ligada à demo da <a href='/partida/{s['partida_id']}'>partida nº {s['partida_id']}</a>. "
                  "Nas armas e nos gráficos vale a demo (sem duplicar). "
                  + form_post("desvincular", f"<input type='hidden' name='id' value='{s['id']}'>", "Desfazer vínculo", "sec",
                              "Desfazer o vínculo? A sessão voltará a contar sozinha nas armas.") + "</div>")
    else:
        estado = ("<div class='aviso'>Sem demo ligada: esta sessão entra nas contagens de tiros e kills por arma. "
                  "Para completar com acertos e dano, baixe a demo desta partida e analise em "
                  "<a href='/analisar'>Analisar demos</a>.</div>")
    corpo = (f"<p><a href='/ao-vivo'>← Ao vivo</a></p><h1>{esc(s['mapa'] or '')} · {esc(s['modo_nome'] or '')}</h1>"
             f"<div class='sub'>{data_br(s['iniciado'])}</div>" + estado
             + bloco_config(s["iniciado"], True, titulo="Configuração usada nesta sessão")
             + (bloco_treino(s) if treino else "") + bloco_sessao(s))
    return pagina("Sessão ao vivo", corpo, "/ao-vivo"), 200


# ---- ajustes ---------------------------------------------------------------------
def pg_ajustes():
    aj = armazem.ajustes_todos()
    det = config_cs.detectar_steamid()
    usuario = config_cs.achar_userdata()
    pastas = "".join(f"<li><code>{esc(str(u['pasta']))}</code> <span class='sub'>(conta {u['conta']}, SteamID {u['steamid64']})</span></li>"
                     for u in usuario) or "<li>nenhuma encontrada</li>"
    tamanho = armazem.CAMINHO_DB.stat().st_size // 1024 if armazem.CAMINHO_DB.exists() else 0

    def campo(nome, rot, valor, dica=""):
        return (f"<div class='campo'><label>{esc(rot)}</label><input type='text' name='{nome}' value='{esc(valor or '')}'>"
                + (f"<small>{dica}</small>" if dica else "") + "</div>")

    form = (f"<form method='post' action='/acao/ajustes'><input type='hidden' name='t' value='{E.token}'>"
            + campo("meu_steamid", "Seu SteamID64", aj.get("meu_steamid") or det,
                    f"Detectado pela pasta do Steam: {esc(det or 'não encontrado')}. É assim que o Hub sabe qual jogador é você nas demos.")
            + ("<div class='campo'><label>Analisar demos novas automaticamente</label><select name='auto_demos'>"
               f"<option value='1'{' selected' if auto_ligado() else ''}>Sim (recomendado)</option>"
               f"<option value='0'{'' if auto_ligado() else ' selected'}>Não</option></select>"
               "<small>O Hub confere a pasta do jogo (e todas as pastas de demos cadastradas) a cada 15 segundos.</small></div>")
            + ("<div class='campo'><label>Avisar quando houver versão nova</label><select name='avisar_atualizacao'>"
               f"<option value='1'{'' if (aj.get('avisar_atualizacao') or '1') == '0' else ' selected'}>Sim</option>"
               f"<option value='0'{' selected' if (aj.get('avisar_atualizacao') or '1') == '0' else ''}>Não</option></select>"
               "<small>O Hub consulta o GitHub a cada poucas horas só para saber se há versão nova. Nenhum dado seu é enviado.</small></div>")
            + campo("repo_github", "Repositório do GitHub (usuario/nome)", aj.get("repo_github") or REPO,
                    "Normalmente já vem preenchido. Só mude se quem te passou o programa avisar.")
            + campo("sens", "Sensibilidade normal (a do rifle)", aj.get("sens"),
                    "Só é usada se o Hub não achar a sensibilidade nos arquivos do jogo.")
            + campo("modos_contam", "Modos ao vivo que contam nos números do Hub",
                    aj.get("modos_contam") if aj.get("modos_contam") is not None else armazem.MODOS_CONTAM_PADRAO,
                    "Separe por vírgula. Opções: competitivo, wingman, casual, demolicao, dm, arms, perigo. "
                    "O treino (mapas da oficina) nunca conta; modos de diversão ficam de fora a menos que você os inclua aqui.")
            + campo("dpi", "DPI do mouse", aj.get("dpi"), "Usado para calcular o eDPI e a distância de 360°.")
            + campo("hz", "Taxa de atualização do monitor (Hz)", aj.get("hz"), "Ex.: 144, 165, 240.")
            + campo("pasta_cfg_usuario", "Pasta de configuração do usuário (opcional)", aj.get("pasta_cfg_usuario"),
                    "Só preencha se o Hub não achar sozinho: ...\\Steam\\userdata\\&lt;número&gt;\\730\\local\\cfg")
            + campo("pasta_cfg_jogo", "Pasta cfg do jogo (opcional)", aj.get("pasta_cfg_jogo"),
                    "...\\Counter-Strike Global Offensive\\game\\csgo\\cfg")
            + "<p><button>Salvar ajustes</button></p></form>")
    atual = (f"<p>Você está na versão <b>{esc(VERSAO)}</b>. "
             + (f"Há uma versão nova: <b>{esc(E.update['versao'])}</b>." if E.update else
                ("Nenhuma versão nova encontrada." if not E.update_erro else f"Última consulta: {esc(E.update_erro)}.")) + " "
             + form_post("checar-atualizacao", "", "Procurar atualização agora", "sec") + "</p>")
    dados = (atual + f"<h2>Seus dados</h2><p>Tudo fica em <code>{esc(str(armazem.CAMINHO_DB))}</code> ({tamanho} KB). Para fazer backup, "
             "copie a pasta <code>dados</code>; para começar do zero, apague-a (com o Hub fechado). Nada sai do seu computador.</p>"
             "<p><a class='btn sec' href='/exportar.json'>Exportar resumo (JSON)</a></p>")
    bloco_pastas = bloco_pastas_demos("/ajustes")
    return pagina("Ajustes", f"<h1>Ajustes</h1><div class='sub'>CS2 Coach versão {esc(VERSAO)}</div>{form}{bloco_pastas}<h2>Contas do Steam neste PC</h2><ul>{pastas}</ul>{dados}", "/ajustes")


def exportar_json():
    sid = escopo()
    return json.dumps({"exportado_em": datetime.now().isoformat(timespec="seconds"),
                       "partidas": armazem.listar_partidas(sid), "historico": armazem.historico(sid),
                       "armas": armas_mod.ranking(armazem.armas_agregadas(sid)), "sessoes": armazem.listar_sessoes(500)},
                      ensure_ascii=False, indent=1, default=float)


# --------------------------------------------------------------------------
# Servidor
# --------------------------------------------------------------------------
class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass

    # -- seguranca: so aceita pedidos feitos para localhost
    def _host_ok(self):
        host = (self.headers.get("Host") or "").lower()
        return host in (f"127.0.0.1:{E.porta}", f"localhost:{E.porta}")

    def _enviar(self, corpo, status=200, tipo="text/html; charset=utf-8", extra=None):
        dados = corpo.encode("utf-8") if isinstance(corpo, str) else corpo
        self.send_response(status)
        self.send_header("Content-Type", tipo)
        self.send_header("Content-Length", str(len(dados)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(dados)

    def _responder_instalando(self):
        corpo = ("<!doctype html><html lang='pt-BR'><head><meta charset='utf-8'><title>Atualizando</title>"
                 f"<style>{CSS}</style></head><body><div class='wrap'><h1>Atualizando o CS2 Coach...</h1>"
                 "<p>O instalador está rodando. Esta janela fecha sozinha e o CS2 Coach abre de novo em instantes "
                 "(se não abrir, procure “CS2 Coach” no menu Iniciar). Seus dados foram mantidos.</p></div></body></html>")
        self._enviar(corpo, 200, "text/html; charset=utf-8")

    def _redirecionar(self, destino):
        self.send_response(303)
        self.send_header("Location", destino)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_GET(self):
        if not self._host_ok():
            return self._enviar("Acesso negado.", 403, "text/plain; charset=utf-8")
        u = urlparse(self.path)
        q = parse_qs(u.query)
        seg = [unquote(s) for s in u.path.strip("/").split("/") if s]
        try:
            if not seg:
                if armazem.ajuste_get("configurado") is None:
                    return self._redirecionar("/bem-vindo")
                return self._enviar(pg_inicio())
            if seg[0] == "ping":
                return self._enviar("cs2coach-hub", tipo="text/plain; charset=utf-8")
            if seg[0] == "mostrar":
                if E.mostrar_janela:
                    E.mostrar_janela()
                else:
                    webbrowser.open(f"http://127.0.0.1:{E.porta}")
                return self._enviar("ok", tipo="text/plain; charset=utf-8")
            if seg[0] == "abrir":
                alvo = (q.get("u") or [""])[0]
                if alvo.startswith(("https://github.com/", f"http://127.0.0.1:{E.porta_gsi}/")):
                    webbrowser.open(alvo)
                return self._enviar("ok", tipo="text/plain; charset=utf-8")
            if seg[0] == "bem-vindo":
                return self._enviar(pg_bem_vindo())
            rota = seg[0]
            if rota == "partidas" and len(seg) == 1:
                return self._enviar(pg_partidas(q))
            if rota == "partida" and len(seg) >= 2 and seg[1].isdigit():
                pid = int(seg[1])
                if len(seg) == 3 and seg[2] == "relatorio":
                    h = armazem.html_partida(pid)
                    if h is None:
                        return self._enviar("Relatório não encontrado.", 404, "text/plain; charset=utf-8")
                    return self._enviar(h, extra={"Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'"})
                if len(seg) == 3 and seg[2] == "kills":
                    corpo, status = pg_kills(pid, q)
                    return self._enviar(corpo, status)
                corpo, status = pg_partida(pid)
                return self._enviar(corpo, status)
            if rota == "radar":
                return self._enviar(pg_radar(q))
            if rota == "rever":
                return self._enviar(pg_rever(q))
            if rota == "evolucao":
                return self._enviar(pg_evolucao())
            if rota == "treino":
                return self._enviar(pg_treino())
            if rota == "armas" and len(seg) == 1:
                return self._enviar(pg_armas(q))
            if rota == "arma" and len(seg) == 2:
                corpo, status = pg_arma(seg[1])
                return self._enviar(corpo, status)
            if rota == "configuracoes":
                return self._enviar(pg_configuracoes())
            if rota == "analisar":
                return self._enviar(pg_analisar())
            if rota == "ao-vivo":
                return self._enviar(pg_ao_vivo())
            if rota == "sessao" and len(seg) == 2 and seg[1].isdigit():
                corpo, status = pg_sessao(int(seg[1]))
                return self._enviar(corpo, status)
            if rota == "ajustes":
                return self._enviar(pg_ajustes())
            if rota == "img" and len(seg) == 3 and seg[1] == "arma" and seg[2].endswith(".png"):
                nome = seg[2][:-4]
                arq = armazem.PASTA_DADOS / "armas_img" / f"{nome}.png"
                if not arq.is_file():
                    arq = icones.arquivo_incluido(nome) or arq
                if icones.nome_png_valido(nome) and arq.is_file():
                    return self._enviar(arq.read_bytes(), tipo="image/png")
                return self._enviar("Imagem não encontrada.", 404, "text/plain; charset=utf-8")
            if rota == "exportar.json":
                return self._enviar(exportar_json(), tipo="application/json; charset=utf-8",
                                    extra={"Content-Disposition": 'attachment; filename="cs2coach_resumo.json"'})
            return self._enviar(pagina("Não encontrado", "<h1>Página não encontrada</h1><a href='/'>Voltar ao início</a>"), 404)
        except Exception as exc:
            traceback.print_exc()
            return self._enviar(pagina("Erro", f"<h1>Algo deu errado</h1><pre>{esc(str(exc))}</pre><a href='/'>Voltar</a>"), 500)

    def do_POST(self):
        if not self._host_ok():
            return self._enviar("Acesso negado.", 403, "text/plain; charset=utf-8")
        origem = self.headers.get("Origin")
        if origem and origem not in (f"http://127.0.0.1:{E.porta}", f"http://localhost:{E.porta}"):
            return self._enviar("Origem não permitida.", 403, "text/plain; charset=utf-8")
        try:
            n = min(int(self.headers.get("Content-Length") or 0), 1_000_000)
            campos = {k: v[0] for k, v in parse_qs(self.rfile.read(n).decode("utf-8", "replace")).items()}
        except Exception:
            return self._enviar("Pedido inválido.", 400, "text/plain; charset=utf-8")
        if not secrets.compare_digest(campos.get("t", ""), E.token):
            return self._enviar("Sessão expirada. Volte à página anterior e recarregue (F5).", 403, "text/plain; charset=utf-8")
        acao = urlparse(self.path).path.rsplit("/", 1)[-1]
        try:
            self._acao(acao, campos)
        except Exception as exc:
            traceback.print_exc()
            with E.lock:
                E.mensagem = f"Não consegui concluir a ação: {esc(str(exc))}"
            self._redirecionar("/")

    def _acao(self, acao, c):
        if acao == "ajustes":
            for k in ("meu_steamid", "auto_demos", "avisar_atualizacao", "repo_github", "sens", "dpi", "hz", "modos_contam", "pasta_cfg_usuario", "pasta_cfg_jogo"):
                if k in c:
                    v = c[k].strip().strip('"')
                    if k in ("meu_steamid", "sens", "dpi", "hz") and v and not v.replace(".", "", 1).isdigit():
                        with E.lock:
                            E.mensagem = f"O valor de “{esc(k)}” precisa ser um número."
                        return self._redirecionar("/ajustes")
                    armazem.ajuste_set(k, v)
            with E.lock:
                E.mensagem = "Ajustes salvos."
            return self._redirecionar("/ajustes")
        if acao == "ler-config":
            aj = armazem.ajustes_todos()
            conf = config_cs.ler_configuracoes(aj.get("pasta_cfg_usuario") or None, aj.get("pasta_cfg_jogo") or None)
            _, mudou = armazem.salvar_config(conf)
            with E.lock:
                E.mensagem = ("Configurações lidas: encontrei mudanças desde a última leitura." if mudou
                              else "Configurações lidas: nada mudou desde a última leitura.")
            return self._redirecionar("/configuracoes")
        if acao == "analisar":
            texto = (c.get("caminho") or "").strip()
            demos, pasta = _demos_do_caminho(texto) if texto else ([], None)
            if not demos:
                with E.lock:
                    E.mensagem = "Não encontrei nenhum arquivo .dem nesse caminho."
                return self._redirecionar("/analisar")
            if pasta and Path(texto).is_dir() and texto.strip().strip('"') not in lista_pastas_demos():
                salvar_pastas_demos(lista_pastas_demos() + [texto])
            ok = iniciar_analise(demos, steam_do_usuario(), (c.get("nick") or "").strip(), c.get("refazer") == "1")
            if not ok:
                with E.lock:
                    E.mensagem = "Já existe uma análise em andamento. Aguarde terminar."
            return self._redirecionar("/analisar")
        if acao == "add-pasta":
            destino = c.get("volta") if c.get("volta") in ("/ajustes", "/analisar") else "/analisar"
            novo = (c.get("caminho") or "").strip().strip('"')
            if not novo or not Path(novo).expanduser().is_dir():
                with E.lock:
                    E.mensagem = "Essa pasta não existe neste computador. Confira o caminho (ex.: D:\\Demos)."
                return self._redirecionar(destino)
            atuais = lista_pastas_demos()
            if novo in atuais:
                with E.lock:
                    E.mensagem = "Essa pasta já está na lista."
            else:
                salvar_pastas_demos(atuais + [novo])
                with E.lock:
                    E.mensagem = "Pasta adicionada. O Hub vai procurar demos nela a cada 15 segundos."
            return self._redirecionar(destino)
        if acao == "rem-pasta":
            destino = c.get("volta") if c.get("volta") in ("/ajustes", "/analisar") else "/analisar"
            atuais = lista_pastas_demos()
            if (c.get("i") or "").isdigit() and int(c["i"]) < len(atuais):
                atuais.pop(int(c["i"]))
                salvar_pastas_demos(atuais)
                with E.lock:
                    E.mensagem = "Pasta removida da lista (os arquivos não foram apagados)."
            return self._redirecionar(destino)
        if acao == "analisar-todas":
            demos = todas_as_demos()
            if not demos:
                with E.lock:
                    E.mensagem = "Não encontrei nenhum arquivo .dem nas pastas cadastradas."
                return self._redirecionar("/analisar")
            if not iniciar_analise(demos, steam_do_usuario(), None, False):
                with E.lock:
                    E.mensagem = "Já existe uma análise em andamento. Aguarde terminar."
            return self._redirecionar("/analisar")
        if acao == "treino-novo":
            plano_atual(forcar=True)
            return self._redirecionar("/treino")
        if acao == "treino-feito":
            p = _plano_carregar()
            ids = {i["id"] for i in (p or {}).get("itens", [])}
            if p and (c.get("id") or "") in ids:
                dias = p.setdefault("feito", {}).setdefault(c["id"], [])
                hoje_s = date.today().isoformat()
                if hoje_s in dias:
                    dias.remove(hoje_s)
                else:
                    dias.append(hoje_s)
                _plano_guardar(p)
            return self._redirecionar("/treino")
        if acao in ("reanalisar", "reanalisar-todas"):
            sid = steam_do_usuario()
            if acao == "reanalisar":
                d = armazem.obter_partida(int(c["id"])) if (c.get("id") or "").isdigit() else None
                nomes = [d["demo"]] if d else []
            else:
                nomes = [r["res"].get("meta", {}).get("demo") for r in partidas_sem_detalhe(armazem.resultados(escopo()))]
            achadas = [x for x in (_acha_demo(n) for n in nomes if n) if x]
            if not achadas:
                with E.lock:
                    E.mensagem = ("Não encontrei o arquivo da demo nas pastas vigiadas. Adicione a pasta onde ela está em "
                                  "<a href='/analisar'>Analisar demos</a> e tente de novo.")
                return self._redirecionar("/evolucao")
            if not iniciar_analise(achadas, sid, None, True):
                with E.lock:
                    E.mensagem = "Já existe uma análise em andamento. Aguarde terminar."
            else:
                with E.lock:
                    E.mensagem = f"Reanalisando {len(achadas)} partida(s)... acompanhe em Analisar demos."
            return self._redirecionar("/analisar")
        if acao == "excluir-partida" and (c.get("id") or "").isdigit():
            armazem.excluir_partida(int(c["id"]))
            with E.lock:
                E.mensagem = "Partida excluída do Hub (a demo original não foi apagada)."
            return self._redirecionar("/partidas")
        if acao == "desvincular" and (c.get("id") or "").isdigit():
            armazem.desvincular_sessao(int(c["id"]))
            with E.lock:
                E.mensagem = "Vínculo desfeito."
            return self._redirecionar(f"/sessao/{c['id']}")
        if acao == "instalar-gsi":
            with E.lock:
                E.mensagem = instalar_gsi_texto()
            return self._redirecionar("/ao-vivo")
        if acao == "wizard":
            feitos = []
            sid = (c.get("meu_steamid") or "").strip()
            if sid.isdigit():
                armazem.ajuste_set("meu_steamid", sid)
                feitos.append("conta escolhida")
            for k in ("dpi", "hz", "sens"):
                v = (c.get(k) or "").strip().replace(",", ".")
                try:
                    float(v)
                    armazem.ajuste_set(k, v)
                except ValueError:
                    pass
            armazem.ajuste_set("auto_demos", "1" if c.get("auto_demos") == "1" else "0")
            if c.get("instalar_gsi") == "1":
                feitos.append("integração ao vivo: " + instalar_gsi_texto())
            with contextlib.suppress(Exception):
                aj = armazem.ajustes_todos()
                armazem.salvar_config(config_cs.ler_configuracoes(aj.get("pasta_cfg_usuario") or None, aj.get("pasta_cfg_jogo") or None))
                feitos.append("configurações do jogo lidas")
            armazem.ajuste_set("configurado", "1")
            with E.lock:
                E.mensagem = "Tudo pronto! " + " · ".join(feitos) + "."
            return self._redirecionar("/")
        if acao == "baixar-atualizacao":
            if not baixar_atualizacao():
                with E.lock:
                    E.mensagem = "Não há atualização para baixar agora."
            return self._redirecionar("/")
        if acao == "instalar-atualizacao":
            erro = instalar_atualizacao()
            if erro:
                with E.lock:
                    E.mensagem = erro
                return self._redirecionar("/")
            return self._responder_instalando()
        if acao == "checar-atualizacao":
            u = verificar_atualizacao()
            with E.lock:
                E.mensagem = (f"Versão nova encontrada: {esc(u['versao'])}." if u else
                              ("Você já está na versão mais recente." if not E.update_erro else f"Não deu para conferir: {esc(E.update_erro)}."))
            return self._redirecionar("/ajustes")
        if acao == "pular-wizard":
            armazem.ajuste_set("configurado", "1")
            return self._redirecionar("/")
        self._enviar("Ação desconhecida.", 400, "text/plain; charset=utf-8")


# --------------------------------------------------------------------------
def iniciar_ao_vivo(host, porta, token):
    """Liga o receptor dos dados do jogo (o mesmo do ao_vivo.py) dentro do Hub."""
    try:
        import ao_vivo
    except Exception as exc:
        E.gsi_erro = f"não consegui carregar o ao_vivo.py ({exc})"
        return
    try:
        coach = ao_vivo.Coach(token)
        srv = ThreadingHTTPServer((host, porta), ao_vivo.criar_handler(coach))
    except OSError:
        E.gsi_erro = (f"a porta {porta} já está em uso (provavelmente o ao_vivo.py aberto em outra janela)")
        return
    E.coach = coach
    coach.ao_iniciar = lambda: atualizar_config("ao vivo")
    with contextlib.suppress(Exception):
        if ao_vivo.migrar_cfg(porta, token):
            with E.lock:
                E.mensagem = ("Atualizei o arquivo de integração com o jogo (agora com posição e dano por round). "
                              "Reinicie o CS2 uma vez para valer.")
    threading.Thread(target=srv.serve_forever, daemon=True).start()

    def vigia():
        while True:
            time.sleep(0.2)
            coach.fechar_rajadas_inativas()

    threading.Thread(target=vigia, daemon=True).start()


_LOCK_CONFIG = threading.Lock()


def atualizar_config(motivo=""):
    """Le a configuracao do jogo agora (sens, mira, video...) e guarda se mudou. Retorna True se mudou."""
    with _LOCK_CONFIG:
        try:
            aj = armazem.ajustes_todos()
            conf = config_cs.ler_configuracoes(aj.get("pasta_cfg_usuario") or None, aj.get("pasta_cfg_jogo") or None)
            if conf.get("arquivos"):
                _, mudou = armazem.salvar_config(conf)
                return mudou
        except Exception:
            pass
    return False


def vigia_config():
    """Confere os arquivos de configuracao do jogo de tempos em tempos e registra qualquer mudanca."""
    while True:
        atualizar_config("vigia")
        time.sleep(20)


def _console_windows(mostrar: bool):
    """No Windows esconde/mostra a janela preta (so quando o icone da bandeja esta funcionando)."""
    if os.name != "nt":
        return
    with contextlib.suppress(Exception):
        import ctypes
        hwnd = ctypes.windll.kernel32.GetConsoleWindow()
        if hwnd:
            ctypes.windll.user32.ShowWindow(hwnd, 5 if mostrar else 0)


def iniciar_bandeja(url, janela=None):
    """Icone perto do relogio do Windows (abrir, pasta dos dados, sair), em segundo plano. Devolve o icone ou None."""
    try:
        import pystray
        from PIL import Image, ImageDraw
    except Exception:
        return None
    try:
        from icone_app import desenhar
        img = desenhar(64)
    except Exception:
        img = Image.new("RGBA", (64, 64), (15, 18, 24, 255))
        ImageDraw.Draw(img).ellipse((14, 14, 50, 50), outline=(76, 194, 255, 255), width=4)

    def abrir(icon, item):
        if janela is not None:
            janela.mostrar()
        elif E.mostrar_janela:
            E.mostrar_janela()
        else:
            webbrowser.open(url)

    def pasta(icon, item):
        with contextlib.suppress(Exception):
            if os.name == "nt":
                os.startfile(str(armazem.PASTA_DADOS))   # noqa: S606

    def sair(icon, item):
        E.parar.set()
        icon.stop()
        if janela is not None:
            janela.sair()

    icone = pystray.Icon("cs2coach", img, "CS2 Coach", menu=pystray.Menu(
        pystray.MenuItem("Abrir o CS2 Coach", abrir, default=True),
        pystray.MenuItem("Abrir a pasta dos dados", pasta),
        pystray.MenuItem("Sair", sair)))
    try:
        icone.run_detached()
    except Exception:
        return None
    return icone


def ja_esta_aberto(porta) -> bool:
    import urllib.request
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{porta}/ping", timeout=2) as r:
            return r.read().decode("utf-8", "ignore").strip() == "cs2coach-hub"
    except Exception:
        return False


def main():
    # versao "sem janela" (pythonw) nao tem terminal: manda as mensagens para um arquivo de log
    if sys.stdout is None or sys.stderr is None:
        log = open(armazem.PASTA_DADOS / "hub.log", "a", encoding="utf-8", buffering=1)
        sys.stdout = sys.stdout or log
        sys.stderr = sys.stderr or log
    for stream in (sys.stdout, sys.stderr):
        with contextlib.suppress(Exception):
            stream.reconfigure(errors="replace", line_buffering=True)
    with contextlib.suppress(Exception):
        janela_mod.definir_identidade()     # icone proprio na barra de tarefas
    ap = argparse.ArgumentParser(description="CS2 Coach: Hub (central local).")
    ap.add_argument("--porta", type=int, default=3100, help="porta do Hub (padrão 3100)")
    ap.add_argument("--porta-gsi", type=int, default=3000, help="porta do painel ao vivo (padrão 3000)")
    ap.add_argument("--token", default="cs2coach", help="token do arquivo de integração do jogo")
    ap.add_argument("--sem-ao-vivo", action="store_true", help="não liga o painel ao vivo dentro do Hub")
    ap.add_argument("--sem-navegador", action="store_true", help="não abre janela nem navegador (só o servidor)")
    ap.add_argument("--janela-nativa", action="store_true", help="usa a moldura normal do Windows (sem a barra de título própria)")
    ap.add_argument("--sem-janela", action="store_true", help="usa o navegador em vez da janela própria")
    ap.add_argument("--sem-bandeja", action="store_true", help="não cria o ícone perto do relógio")
    ap.add_argument("--teste-janela", action="store_true", help="(teste automático) abre a janela, confere a página e sai")
    args = ap.parse_args()
    E.porta, E.porta_gsi, E.gsi_token = args.porta, args.porta_gsi, args.token
    url = f"http://127.0.0.1:{args.porta}"

    # ja tem um CS2 Coach aberto? So abre o navegador nele (evita duas copias brigando pela porta)
    if ja_esta_aberto(args.porta):
        print("O CS2 Coach já está aberto: trazendo a janela dele para frente.")
        if not args.sem_navegador:
            import urllib.request
            with contextlib.suppress(Exception):
                urllib.request.urlopen(url + "/mostrar", timeout=3).read()
        return

    # quem ja tinha dados ou ajustes de uma versao anterior nao precisa do assistente de primeira vez
    if armazem.ajuste_get("configurado") is None and (armazem.ajuste_get("dpi") or armazem.listar_partidas(limite=1)):
        armazem.ajuste_set("configurado", "1")
    if armazem.ajuste_get("modos_contam") is None:
        armazem.ajuste_set("modos_contam", armazem.MODOS_CONTAM_PADRAO)
    threading.Thread(target=vigia_config, daemon=True).start()
    threading.Thread(target=vigia_demos, daemon=True).start()
    threading.Thread(target=vigia_atualizacao, daemon=True).start()
    if not args.sem_ao_vivo:
        iniciar_ao_vivo("127.0.0.1", args.porta_gsi, args.token)
        if E.gsi_erro:
            print("Aviso (ao vivo):", E.gsi_erro)
    try:
        servidor = ThreadingHTTPServer(("127.0.0.1", args.porta), Handler)
    except OSError:
        print(f"A porta {args.porta} está ocupada por outro programa. Tente: --porta {args.porta + 1}")
        sys.exit(1)
    print(f"CS2 Coach rodando em {url}")
    print(f"Dados guardados em: {armazem.PASTA_DADOS}")
    print("Para encerrar: Ctrl+C (ou 'Sair' no ícone perto do relógio).")
    threading.Thread(target=servidor.serve_forever, daemon=True).start()
    janela = None
    icone = None
    try:
        if not args.sem_navegador and not args.sem_janela and (os.name == "nt" or args.teste_janela):
            propria = not args.janela_nativa and armazem.ajuste_get("janela_nativa") != "1"
            candidata = janela_mod.Janela(url, armazem.PASTA_DADOS, moldura_propria=propria)
            if candidata.disponivel():
                janela = candidata
        if not args.sem_bandeja:
            icone = iniciar_bandeja(url, janela)
            if icone is not None:
                _console_windows(False)
        if janela is not None:
            E.mostrar_janela = janela.mostrar
            janela.saindo = icone is None      # sem icone na bandeja nao ha como reabrir: fechar encerra
            E.moldura = janela.moldura_propria
            janela.criar()
            try:
                janela.iniciar(_teste_janela(janela) if args.teste_janela else None)
            except Exception as exc:
                print("Não consegui abrir a janela própria:", exc)
                E.mostrar_janela = None
                E.moldura = False
                janela = None
                if not (janela_mod.abrir_modo_app(url)):
                    webbrowser.open(url)
        elif not args.sem_navegador:
            if not janela_mod.abrir_modo_app(url):
                threading.Timer(0.8, lambda: webbrowser.open(url)).start()
        if janela is None:
            # modo navegador (ou janela indisponivel): espera 'Sair' no icone ou Ctrl+C
            while not E.parar.wait(3600):
                pass
    except KeyboardInterrupt:
        print("\nEncerrando...")
    finally:
        if icone is not None:
            with contextlib.suppress(Exception):
                icone.stop()
        if E.coach is not None:
            with contextlib.suppress(Exception):
                with E.coach.lock:
                    E.coach._fechar_rajada()
                    E.coach._encerrar_partida()
        servidor.shutdown()
        servidor.server_close()


def _teste_janela(janela):
    """Teste automatico (CI): confere que a janela carregou o Hub e grava o resultado num arquivo."""
    def rodar():
        resultado = "FALHOU: tempo esgotado"
        for _ in range(60):
            time.sleep(1)
            try:
                titulo = janela.janela.evaluate_js("document.title")
                if titulo and "CS2 Coach" in str(titulo):
                    resultado = f"OK: janela abriu o Hub ({titulo})"
                    break
            except Exception as exc:
                resultado = f"FALHOU: {exc}"
        if resultado.startswith("OK"):
            extra = []
            with contextlib.suppress(Exception):
                time.sleep(2)
                extra.append("moldura propria=" + str(janela.moldura_propria))
                extra.append("api=" + str(janela.janela.evaluate_js("typeof (window.pywebview && window.pywebview.api && window.pywebview.api.alternar)")))
                extra.append("barra=" + str(janela.janela.evaluate_js("!!document.getElementById('tb')")))
                janela.alternar_maximizar()
                time.sleep(1)
                extra.append("maximizada=" + str(janela.maximizada))
                janela.alternar_maximizar()
                time.sleep(1)
                extra.append("restaurada=" + str(not janela.maximizada))
                from PIL import ImageGrab
                ImageGrab.grab().save(str(armazem.PASTA_DADOS / "janela_teste.png"))
            resultado += " | " + "; ".join(extra)
        with contextlib.suppress(Exception):
            (armazem.PASTA_DADOS / "janela_teste.txt").write_text(resultado, encoding="utf-8")
        janela.sair()
        E.parar.set()
    return rodar


if __name__ == "__main__":
    main()
