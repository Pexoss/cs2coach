# -*- coding: utf-8 -*-
"""
Icones das armas (silhuetas simples desenhadas para este programa, em SVG).

Quer as imagens reais do jogo? Coloque PNGs na pasta  dados/armas_img/  com o nome interno da arma
(ex.: ak47.png, awp.png, m4a1_silencer.png, usp_silencer.png). O Hub usa o seu PNG no lugar do desenho.
"""
from __future__ import annotations

import re
import struct
import sys
from pathlib import Path

import armas as armas_mod

# forma de cada arma
FORMA = {
    "ak47": "ak", "m4a1": "m4", "m4a1_silencer": "m4s", "galilar": "ak", "famas": "bullpup", "aug": "bullpup",
    "sg556": "m4", "awp": "sniper", "ssg08": "scout", "scar20": "autosniper", "g3sg1": "autosniper",
    "mac10": "smg", "mp9": "smg", "mp7": "smg", "mp5sd": "smg", "ump45": "smg", "p90": "p90", "bizon": "bizon",
    "nova": "shotgun", "xm1014": "shotgun", "mag7": "shotgun", "sawedoff": "sawed",
    "m249": "mg", "negev": "mg", "glock": "pistol", "hkp2000": "pistol", "usp_silencer": "pistols",
    "p250": "pistol", "fiveseven": "pistol", "tec9": "pistol", "cz75a": "pistol", "deagle": "deagle",
    "elite": "pistol", "revolver": "deagle", "taser": "taser", "hegrenade": "he", "inferno": "molotov",
    "flashbang": "flash", "smokegrenade": "smoke", "decoy": "decoy", "knife": "knife",
}

_COR = "currentColor"


def _p(pts):
    return " ".join(f"{x},{y}" for x, y in pts)


def _poly(pts):
    return f'<polygon points="{_p(pts)}"/>'


def _rect(x, y, w, h, r=1):
    return f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="{r}"/>'


def _path(d):
    return f'<path d="{d}"/>'


def _forma(f: str) -> str:
    g = []
    if f == "ak":
        g += [_path("M1 13 L21 11 L26 13 L26 25 L9 29 Q2 29 1 23Z"),                       # coronha
              _path("M27 12 L60 12 L60 24 L27 24Z"), _path("M60 14 Q72 13 86 15 L86 23 Q72 24 60 23Z"),   # corpo e guarda-mao
              _rect(86, 16.5, 25, 3.2, 1), _rect(110, 14.5, 6, 6.2, 1.5), _rect(99, 10.5, 3, 6.5, 1),    # cano, boca, massa de mira
              _rect(56, 9.5, 5, 3, 1), _path("M37 24 L49 24 Q50 33 58 37 L53 40 Q40 35 37 24Z"),          # alca e carregador curvo
              _path("M27 24 L35 24 L32 36 Q31 38 28 37Z")]                                                # empunhadura
    elif f in ("m4", "m4s"):
        g += [_path("M3 14 L22 12 L26 14 L26 25 L9 28 Q3 28 3 23Z"), _path("M26 11 L58 11 L58 25 L26 25Z"),
              _rect(28, 7.5, 28, 3.5, 1.5), _rect(58, 13, 30, 10, 2),
              _rect(88, 16.5, 16, 3.2, 1)]
        g.append(_rect(103, 14, 14, 8, 3.5) if f == "m4s" else _rect(103, 14.5, 9, 6.4, 1.5))
        g += [_path("M38 25 L48 25 L49 38 L40 38Z"), _path("M27 25 L34 25 L32 36 Q31 38 28 37Z")]
    elif f == "bullpup":
        g += [_path("M3 14 Q3 12 8 12 L64 12 L70 15 L70 24 L8 26 Q3 26 3 22Z"), _rect(70, 15.5, 36, 3.6, 1),
              _rect(104, 14, 9, 6.5, 1.5), _rect(14, 8, 24, 4, 2), _path("M44 25 L53 25 L52 37 L45 37Z"),
              _path("M57 24 L64 24 L62 33 L58 33Z")]
    elif f in ("sniper", "scout", "autosniper"):
        fim = {"sniper": 118, "scout": 112, "autosniper": 104}[f]
        g += [_path("M1 15 L20 13 L26 15 L26 26 L8 29 Q2 29 1 24Z"), _path("M26 14 L68 14 L68 24 L26 24Z"),
              _rect(68, 16.5, fim - 68, 3, 1), _rect(fim - 6, 14.5, 6, 6, 1.5),
              _rect(30, 6.5, 34, 6, 3), _rect(28, 9.5, 3, 5), _rect(62, 9.5, 3, 5),                       # luneta
              _path("M33 24 L40 24 L37 35 Q36 37 33 36Z")]
        g.append(_path("M48 24 L58 24 L57 38 L48 38Z") if f == "autosniper" else _rect(46, 24, 14, 4, 2))
        if f != "autosniper":
            g.append(_path("M62 14 L66 9 L69 9 L66 14Z"))                                                  # ferrolho
    elif f in ("smg", "bizon", "p90"):
        if f == "p90":
            g += [_path("M4 18 Q4 10 14 10 L76 10 Q84 10 84 18 Q84 26 76 26 L14 26 Q4 26 4 18Z"),
                  _rect(84, 15.5, 22, 3.6, 1), _rect(22, 5.5, 30, 4.5, 2), _path("M40 26 L47 26 L45 36 L39 36Z")]
        elif f == "bizon":
            g += [_path("M8 14 Q8 12 12 12 L68 12 L68 24 L12 24 Q8 24 8 22Z"), _rect(68, 16, 26, 3.4, 1),
                  _rect(16, 24, 46, 8, 4), _path("M72 24 L79 24 L77 35 L71 35Z")]
        else:
            g += [_path("M6 15 L24 14 L24 25 L10 26 Q6 26 6 22Z"), _path("M24 12 L60 12 L60 25 L24 25Z"),
                  _rect(60, 16, 30, 3.6, 1), _rect(88, 14.5, 8, 6.6, 1.5),
                  _path("M32 25 L39 25 L37 35 L31 35Z"), _path("M47 25 L54 25 L53 39 L46 39Z")]
    elif f in ("shotgun", "sawed"):
        fim = 114 if f == "shotgun" else 86
        g += [_path("M1 15 L22 13 L28 15 L28 25 L8 29 Q2 29 1 24Z"), _path("M28 13 L54 13 L54 24 L28 24Z"),
              _rect(54, 14, fim - 54, 4, 1.5), _rect(54, 20, fim - 66, 4, 2), _path("M33 24 L40 24 L38 35 L32 35Z")]
    elif f == "mg":
        g += [_path("M1 14 L20 12 L26 14 L26 26 L8 29 Q2 29 1 24Z"), _path("M26 11 L62 11 L62 25 L26 25Z"),
              _rect(62, 14, 30, 6, 2), _rect(92, 16, 24, 3.4, 1), _rect(36, 25, 28, 13, 4),
              _path("M27 25 L34 25 L32 35 L28 35Z"), _rect(40, 7.5, 18, 3.5, 1.5)]
    elif f in ("pistol", "pistols", "deagle"):
        big = f == "deagle"
        fim = 88 if big else 80
        g += [_path(f"M26 10 L{fim} 10 L{fim} 21 L52 21 L26 21Z"), _path("M26 21 L45 21 L40 38 Q39 40 36 40 L30 40 Q27 40 28 37Z") if not big
              else _path("M26 21 L48 21 L43 38 Q42 40 39 40 L31 40 Q28 40 29 37Z"),
              _rect(22, 9, 6, 5, 1), _rect(fim - 4, 7.5, 3, 3, 1), _path("M47 21 Q52 28 56 21Z")]
        if f == "pistols":
            g.append(_rect(fim, 11.5, 24, 6.5, 3))
        if big:
            g.append(_rect(fim, 11, 12, 4, 1.5))
    elif f == "knife":
        g += [_path("M30 20 Q70 5 114 21 Q70 27 30 24Z"), _rect(14, 17, 18, 9, 4), _rect(28, 14, 4, 15, 1.5), _rect(4, 18, 10, 7, 3)]
    elif f in ("he", "flash", "smoke", "decoy"):
        corpo = {"he": '<ellipse cx="60" cy="26" rx="17" ry="13"/>', "flash": _rect(46, 12, 28, 26, 7),
                 "smoke": _rect(45, 9, 30, 30, 9), "decoy": _rect(47, 11, 26, 27, 7)}[f]
        g += [corpo, _rect(53, 5, 14, 7, 2), _path("M67 6 Q82 1 84 13 L81 13 Q79 6 68 9Z")]
    elif f == "molotov":
        g += [_path("M50 39 Q39 39 41 27 Q43 20 53 18 L53 8 L67 8 L67 18 Q77 20 79 27 Q81 39 70 39Z"),
              _path("M56 8 Q60 -1 64 8Z")]
    elif f == "taser":
        g += [_path("M26 11 L78 11 Q84 11 84 17 Q84 23 78 23 L26 23Z"), _rect(84, 14, 20, 6, 1.5),
              _path("M34 23 L45 23 L42 37 Q41 39 38 39 L34 39Z"),
              '<path d="M104 12 L111 19 L105 19 L113 28" stroke="currentColor" stroke-width="2.4" fill="none"/>']
    else:
        g += [_rect(30, 12, 60, 16, 5), '<text x="60" y="24" font-size="11" text-anchor="middle" fill="#0f1218">?</text>']
    return "".join(g)


def svg_arma(arma: str, altura: int = 30) -> str:
    a = armas_mod.norm_arma(arma)
    f = FORMA.get(a, "pistol" if armas_mod.tipo_arma(a) == "arma" else "?")
    return (f'<svg class="icarma" viewBox="0 0 120 40" height="{altura}" role="img" aria-label="{a}" fill="{_COR}">'
            f'{_forma(f)}</svg>')


_INCLUIDAS = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent)) / "armas_img"


def arquivo_incluido(nome: str):
    """PNG branco com fundo transparente que vem junto com o programa (tingido pelo tema na tela)."""
    f = _INCLUIDAS / f"{nome}.png"
    return f if nome_png_valido(nome) and f.is_file() else None


def tamanho_png(caminho):
    """(largura, altura) lendo so o cabecalho do PNG (sem precisar do Pillow)."""
    try:
        with open(caminho, "rb") as f:
            cab = f.read(24)
        return struct.unpack(">II", cab[16:24])
    except Exception:
        return (300, 90)


def nome_png_valido(nome: str) -> bool:
    return bool(re.fullmatch(r"[a-z0-9_]{1,40}", nome or ""))
