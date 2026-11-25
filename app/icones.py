# -*- coding: utf-8 -*-
"""
Icones das armas (silhuetas simples desenhadas para este programa, em SVG).

Quer as imagens reais do jogo? Coloque PNGs na pasta  dados/armas_img/  com o nome interno da arma
(ex.: ak47.png, awp.png, m4a1_silencer.png, usp_silencer.png). O Hub usa o seu PNG no lugar do desenho.
"""
from __future__ import annotations

import re

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


def _forma(f: str) -> str:
    g = []
    if f in ("ak", "m4", "m4s"):
        g += [_poly([(2, 15), (24, 13), (24, 25), (7, 27)]), _rect(24, 13, 40, 11), _rect(64, 15, 24, 7),
              _rect(88, 17, 28 if f != "m4s" else 22, 3), _poly([(31, 24), (38, 24), (35, 35), (29, 35)])]
        if f == "ak":
            g.append('<path d="M44 24 L54 24 Q57 34 50 38 L42 36 Q47 30 44 24Z"/>')
            g.append(_rect(64, 12, 3, 4))
        else:
            g.append(_poly([(42, 24), (52, 24), (50, 37), (41, 37)]))
            g.append(_rect(34, 9, 24, 4, 2))
        if f == "m4s":
            g.append(_rect(104, 15, 12, 7, 3))
    elif f == "bullpup":
        g += [_rect(4, 13, 66, 12, 3), _rect(70, 16, 34, 4), _rect(104, 15, 10, 6, 2),
              _poly([(40, 25), (48, 25), (47, 36), (39, 36)]), _poly([(58, 25), (64, 25), (62, 33), (57, 33)]),
              _rect(20, 9, 20, 4, 2)]
    elif f in ("sniper", "scout", "autosniper"):
        larg = {"sniper": 112, "scout": 106, "autosniper": 98}[f]
        g += [_poly([(2, 16), (24, 14), (24, 25), (6, 27)]), _rect(24, 15, 40, 8), _rect(64, 17, larg - 64, 3),
              _poly([(32, 23), (38, 23), (36, 33), (31, 33)]), _rect(36, 7, 32, 6, 3), _rect(33, 11, 3, 4), _rect(60, 11, 3, 4)]
        if f == "autosniper":
            g.append(_poly([(46, 23), (56, 23), (54, 37), (45, 37)]))
        else:
            g.append(_rect(44, 23, 14, 4, 1))
    elif f in ("smg", "bizon", "p90"):
        if f == "p90":
            g += [_rect(6, 12, 70, 14, 7), _rect(76, 17, 24, 4), _poly([(30, 26), (36, 26), (34, 36), (29, 36)]),
                  _rect(24, 8, 24, 4, 2)]
        elif f == "bizon":
            g += [_rect(10, 14, 56, 11, 3), _rect(66, 17, 26, 3), _rect(18, 25, 40, 7, 3),
                  _poly([(70, 25), (76, 25), (74, 34), (69, 34)])]
        else:
            g += [_poly([(6, 16), (22, 15), (22, 24), (10, 25)]), _rect(22, 14, 36, 11), _rect(58, 17, 30, 4),
                  _poly([(30, 25), (36, 25), (34, 35), (29, 35)]), _poly([(46, 25), (53, 25), (52, 38), (45, 38)])]
    elif f in ("shotgun", "sawed"):
        long = 112 if f == "shotgun" else 84
        g += [_poly([(2, 15), (22, 14), (22, 25), (6, 27)]), _rect(22, 14, 30, 10), _rect(52, 15, long - 52, 4),
              _rect(52, 21, long - 70, 4, 2), _poly([(28, 24), (34, 24), (32, 34), (27, 34)])]
    elif f == "mg":
        g += [_poly([(2, 15), (22, 13), (22, 25), (6, 27)]), _rect(22, 13, 36, 11), _rect(58, 15, 32, 6),
              _rect(90, 17, 22, 3), _rect(36, 24, 26, 13, 3), _poly([(28, 24), (34, 24), (32, 34), (27, 34)])]
    elif f in ("pistol", "pistols", "deagle"):
        k = 1.25 if f == "deagle" else 1.0
        g += [_rect(30, 10, 54 * k, 11, 2), _poly([(40, 21), (56, 21), (50, 38), (38, 38)]) if k == 1 else
              _poly([(40, 21), (60, 21), (54, 38), (40, 38)]), _rect(24, 10, 8, 6, 1)]
        if f == "pistols":
            g.append(_rect(84, 12, 24, 6, 3))
    elif f == "knife":
        g += ['<path d="M8 20 Q60 4 112 20 Q60 26 8 24Z"/>', _rect(2, 18, 14, 8, 3)]
    elif f in ("he", "flash", "smoke", "decoy"):
        g += ['<ellipse cx="60" cy="24" rx="15" ry="13"/>' if f == "he" else _rect(46, 12, 28, 26, 7),
              _rect(54, 6, 12, 7, 1), '<path d="M66 8 Q80 4 82 14" stroke="currentColor" stroke-width="2" fill="none"/>']
    elif f == "molotov":
        g += ['<path d="M52 38 Q42 38 44 26 Q46 20 54 18 L54 8 L66 8 L66 18 Q74 20 76 26 Q78 38 68 38Z"/>']
    elif f == "taser":
        g += [_rect(30, 12, 46, 13, 4), _rect(76, 15, 20, 7, 1), _poly([(38, 25), (48, 25), (46, 37), (37, 37)]),
              '<path d="M96 14 L104 20 L98 20 L106 28" stroke="currentColor" stroke-width="2.4" fill="none"/>']
    else:
        g += [_rect(30, 12, 60, 14, 4), '<text x="60" y="23" font-size="10" text-anchor="middle" fill="#0f1218">?</text>']
    return "".join(g)


def svg_arma(arma: str, altura: int = 30) -> str:
    a = armas_mod.norm_arma(arma)
    f = FORMA.get(a, "pistol" if armas_mod.tipo_arma(a) == "arma" else "?")
    return (f'<svg class="icarma" viewBox="0 0 120 40" height="{altura}" role="img" aria-label="{a}" fill="{_COR}">'
            f'{_forma(f)}</svg>')


def nome_png_valido(nome: str) -> bool:
    return bool(re.fullmatch(r"[a-z0-9_]{1,40}", nome or ""))
