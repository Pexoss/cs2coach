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
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote, unquote, urlparse

sys.path.insert(0, str(Path(__file__).resolve().parent))
import armas as armas_mod  # noqa: E402
import armazem  # noqa: E402
import config_cs  # noqa: E402
import icones  # noqa: E402
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
:root{--bg:#0f1218;--card:#181d27;--card2:#202736;--tx:#e8ecf3;--mut:#93a0b5;--ac:#4cc2ff;--ok:#4cd68a;--ruim:#ff6b6b;--warn:#ffc857}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--tx);font:15px/1.5 system-ui,Segoe UI,Roboto,sans-serif}
a{color:var(--ac);text-decoration:none}a:hover{text-decoration:underline}
nav{background:var(--card);border-bottom:1px solid var(--card2);position:sticky;top:0;z-index:5}
nav .in{max-width:1100px;margin:0 auto;padding:0 16px;display:flex;gap:4px;flex-wrap:wrap;align-items:center}
nav b{margin-right:14px;padding:12px 0}nav a{padding:12px 12px;color:var(--mut);border-bottom:3px solid transparent}
nav a.on{color:var(--tx);border-color:var(--ac)}nav a:hover{text-decoration:none;color:var(--tx)}
.wrap{max-width:1100px;margin:0 auto;padding:22px 16px 60px}
h1{font-size:24px;margin:0 0 4px}h2{font-size:18px;margin:30px 0 12px;border-left:4px solid var(--ac);padding-left:10px}
h4{margin:0 0 6px;font-size:14px;color:var(--mut);font-weight:600}
.sub,.nota{color:var(--mut)}.nota{font-size:13px}.vazio{color:var(--mut);padding:8px 0}
.cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:10px;margin:14px 0}
.card{background:var(--card);border-radius:10px;padding:12px 14px}.card b{display:block;font-size:24px}.card span{color:var(--mut);font-size:12px}
.up{color:var(--ok)}.down{color:var(--ruim)}
.grid2{display:grid;grid-template-columns:repeat(auto-fit,minmax(340px,1fr));gap:12px}
.graf{background:var(--card);border-radius:10px;padding:10px 12px}.graf svg{width:100%;height:auto}
table{width:100%;border-collapse:collapse;background:var(--card);border-radius:10px;overflow:hidden;margin:8px 0}
th,td{padding:8px 10px;text-align:left;border-bottom:1px solid var(--card2);vertical-align:top}
th{color:var(--mut);font-weight:600;font-size:13px}tr:last-child td{border-bottom:0}
.bar{background:var(--card2);border-radius:4px;height:10px;min-width:60px}.bar i{display:block;height:10px;border-radius:4px;background:var(--ac)}
.dica{background:var(--card);border-radius:10px;padding:12px 16px;margin:10px 0;border-left:4px solid var(--warn)}
.dica.p1{border-color:var(--ruim)}.dica.p3{border-color:var(--ac)}.dica h3{margin:0 0 6px;font-size:16px}.dica p{margin:4px 0}
.ok{background:var(--card);border-left:4px solid var(--ok);border-radius:10px;padding:8px 14px;margin:8px 0}
.aviso{background:var(--card);border-left:4px solid var(--warn);border-radius:10px;padding:10px 14px;margin:10px 0}
.tabs{display:flex;gap:6px;flex-wrap:wrap;margin:8px 0}.tabs a{background:var(--card);padding:5px 12px;border-radius:999px;color:var(--mut)}
.tabs a.on{background:var(--ac);color:#06202d;font-weight:600}.tabs a:hover{text-decoration:none}
form.inline{display:inline}input[type=text],input[type=number],select{background:var(--card);color:var(--tx);border:1px solid var(--card2);border-radius:6px;padding:7px 10px;font:inherit;width:100%}
button,.btn{background:var(--ac);color:#06202d;border:0;border-radius:6px;padding:8px 14px;font:inherit;font-weight:600;cursor:pointer;display:inline-block}
button.sec,.btn.sec{background:var(--card2);color:var(--tx)}button.perigo{background:#5a2b32;color:#ffd5d9}
label{display:block;margin:12px 0 4px;color:var(--mut);font-size:13px}.campo small{color:var(--mut)}
code,.cmd{background:var(--card2);padding:1px 6px;border-radius:4px;font-family:ui-monospace,Consolas,monospace;font-size:13px}
pre{background:var(--card);border-radius:10px;padding:12px;overflow:auto;font-size:13px;max-height:340px}
iframe{width:100%;height:760px;border:1px solid var(--card2);border-radius:10px;background:var(--bg)}
.pill{display:inline-block;background:var(--card2);color:var(--mut);border-radius:999px;padding:1px 9px;font-size:12px}
.icarma{color:var(--ac);vertical-align:middle;margin-right:8px;width:auto}.icimg{height:30px;vertical-align:middle;margin-right:8px}
.expl{color:var(--mut);font-size:13px;margin:2px 0 0}.pill.dados{background:#2a2447;color:#c9b8ff}.pill.live{background:#12343f;color:#8fdcf0}
details{background:var(--card);border-radius:10px;padding:8px 14px;margin:10px 0}summary{cursor:pointer;font-weight:600}
.pill.ruim{background:#4a2329;color:#ffb3ba}.pill.ok{background:#1e3b2d;color:#9be6bd}
"""

NAV = [("/", "Visão geral"), ("/partidas", "Partidas"), ("/armas", "Armas"), ("/configuracoes", "Configurações"),
       ("/analisar", "Analisar demos"), ("/ao-vivo", "Ao vivo"), ("/ajustes", "Ajustes")]


def pagina(titulo, corpo, ativo="/", refresh=None):
    if not refresh and E.dl and E.dl.get("estado") == "baixando":
        refresh = 2
    meta = f'<meta http-equiv="refresh" content="{refresh}">' if refresh else ""
    links = "".join(f'<a href="{h}" class="{"on" if h == ativo else ""}">{esc(n)}</a>' for h, n in NAV)
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
    return (f'<!doctype html><html lang="pt-BR"><head><meta charset="utf-8">{meta}'
            f'<meta name="viewport" content="width=device-width,initial-scale=1"><title>{esc(titulo)} · CS2 Coach</title>'
            f'<style>{CSS}</style></head><body><nav><div class="in"><b>CS2 Coach · Hub</b>{links}</div></nav>'
            f'<div class="wrap">{msg}{corpo}</div></body></html>')


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
def icone_arma(arma, altura=26):
    a = armas_mod.norm_arma(arma)
    if icones.nome_png_valido(a) and (armazem.PASTA_DADOS / "armas_img" / f"{a}.png").exists():
        return f'<img class="icimg" src="/img/arma/{a}.png" alt="{esc(a)}">'
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
    return pagina("Visão geral", "<h1>Visão geral</h1><div class='sub'>Tudo guardado no seu computador.</div>" + banner_auto()
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
             f"<p><a class='btn' href='/partida/{pid}/relatorio'>Abrir relatório completo</a></p>"
             + cards + "<h2>O que mais pesou</h2>" + (lista_dicas(d["dicas"][:6]) or "<div class='vazio'>Sem pontos fortes de alerta.</div>")
             + "".join(f"<div class='ok'>✔ {esc(f)}</div>" for f in d["fortes"])
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
                corpo, status = pg_partida(pid)
                return self._enviar(corpo, status)
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
    threading.Thread(target=srv.serve_forever, daemon=True).start()

    def vigia():
        while True:
            time.sleep(0.2)
            coach.fechar_rajadas_inativas()

    threading.Thread(target=vigia, daemon=True).start()


def vigia_config():
    """Confere os arquivos de configuracao do jogo de tempos em tempos e registra qualquer mudanca."""
    while True:
        try:
            aj = armazem.ajustes_todos()
            conf = config_cs.ler_configuracoes(aj.get("pasta_cfg_usuario") or None, aj.get("pasta_cfg_jogo") or None)
            if conf.get("arquivos"):
                armazem.salvar_config(conf)
        except Exception:
            pass
        time.sleep(30)


def _console_windows(mostrar: bool):
    """No Windows esconde/mostra a janela preta (so quando o icone da bandeja esta funcionando)."""
    if os.name != "nt":
        return
    with contextlib.suppress(Exception):
        import ctypes
        hwnd = ctypes.windll.kernel32.GetConsoleWindow()
        if hwnd:
            ctypes.windll.user32.ShowWindow(hwnd, 5 if mostrar else 0)


def iniciar_bandeja(url, parar):
    """Icone perto do relogio do Windows: abrir o Hub, abrir a pasta dos dados, sair. Devolve False se nao der."""
    try:
        import pystray
        from PIL import Image, ImageDraw
    except Exception:
        return False
    try:
        from icone_app import desenhar
        img = desenhar(64)
    except Exception:
        img = Image.new("RGBA", (64, 64), (15, 18, 24, 255))
        ImageDraw.Draw(img).ellipse((14, 14, 50, 50), outline=(76, 194, 255, 255), width=4)

    def abrir(icon, item):
        webbrowser.open(url)

    def pasta(icon, item):
        with contextlib.suppress(Exception):
            if os.name == "nt":
                os.startfile(str(armazem.PASTA_DADOS))   # noqa: S606

    def sair(icon, item):
        icon.stop()

    icone = pystray.Icon("cs2coach", img, "CS2 Coach", menu=pystray.Menu(
        pystray.MenuItem("Abrir o CS2 Coach", abrir, default=True),
        pystray.MenuItem("Abrir a pasta dos dados", pasta),
        pystray.MenuItem("Sair", sair)))
    _console_windows(False)
    try:
        icone.run()
    except Exception:
        _console_windows(True)
        return False
    return True


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
    ap = argparse.ArgumentParser(description="CS2 Coach: Hub (central local).")
    ap.add_argument("--porta", type=int, default=3100, help="porta do Hub (padrão 3100)")
    ap.add_argument("--porta-gsi", type=int, default=3000, help="porta do painel ao vivo (padrão 3000)")
    ap.add_argument("--token", default="cs2coach", help="token do arquivo de integração do jogo")
    ap.add_argument("--sem-ao-vivo", action="store_true", help="não liga o painel ao vivo dentro do Hub")
    ap.add_argument("--sem-navegador", action="store_true", help="não abre o navegador sozinho")
    ap.add_argument("--sem-bandeja", action="store_true", help="não cria o ícone perto do relógio")
    args = ap.parse_args()
    E.porta, E.porta_gsi, E.gsi_token = args.porta, args.porta_gsi, args.token
    url = f"http://127.0.0.1:{args.porta}"

    # ja tem um CS2 Coach aberto? So abre o navegador nele (evita duas copias brigando pela porta)
    if ja_esta_aberto(args.porta):
        print("O CS2 Coach já está aberto: abrindo o navegador nele.")
        if not args.sem_navegador:
            webbrowser.open(url)
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
    print("Deixe esta janela aberta enquanto usa. Para encerrar: Ctrl+C (ou 'Sair' no ícone perto do relógio).")
    threading.Thread(target=servidor.serve_forever, daemon=True).start()
    if not args.sem_navegador:
        threading.Timer(0.8, lambda: webbrowser.open(url)).start()
    try:
        if args.sem_bandeja or not iniciar_bandeja(url, servidor.shutdown):
            while True:
                time.sleep(3600)
    except KeyboardInterrupt:
        print("\nEncerrando...")
    finally:
        if E.coach is not None:
            with contextlib.suppress(Exception):
                with E.coach.lock:
                    E.coach._fechar_rajada()
                    E.coach._encerrar_partida()
        servidor.shutdown()
        servidor.server_close()


if __name__ == "__main__":
    main()
