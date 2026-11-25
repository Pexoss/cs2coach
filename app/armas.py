# -*- coding: utf-8 -*-
"""
Nomes das armas e ranking "suas melhores armas".

O ranking compara as armas VOCE entre si (percentil dentro das armas que voce usou),
nao contra outros jogadores. Quanto mais partidas, mais confiavel.
"""
from __future__ import annotations

NOME_ARMA = {
    "ak47": "AK-47", "m4a1": "M4A4", "m4a1_silencer": "M4A1-S", "galilar": "Galil AR",
    "famas": "FAMAS", "aug": "AUG", "sg556": "SG 553", "awp": "AWP", "ssg08": "SSG 08",
    "scar20": "SCAR-20", "g3sg1": "G3SG1", "mac10": "MAC-10", "mp9": "MP9", "mp7": "MP7",
    "mp5sd": "MP5-SD", "ump45": "UMP-45", "p90": "P90", "bizon": "PP-Bizon",
    "nova": "Nova", "xm1014": "XM1014", "mag7": "MAG-7", "sawedoff": "Sawed-Off",
    "m249": "M249", "negev": "Negev", "glock": "Glock-18", "hkp2000": "P2000",
    "usp_silencer": "USP-S", "p250": "P250", "fiveseven": "Five-SeveN", "tec9": "Tec-9",
    "cz75a": "CZ75-Auto", "deagle": "Desert Eagle", "elite": "Dual Berettas",
    "revolver": "R8 Revolver", "taser": "Zeus x27", "hegrenade": "Granada HE",
    "inferno": "Molotov / Incendiária", "molotov": "Molotov", "incgrenade": "Granada incendiária",
    "flashbang": "Flash", "smokegrenade": "Smoke", "decoy": "Decoy",
}
UTILIDADE = {"hegrenade", "inferno", "molotov", "incgrenade", "flashbang", "smokegrenade", "decoy"}

MIN_TIROS_RANKING = 40       # abaixo disso a arma nao entra na nota (amostra pequena)


# O jogo usa nomes diferentes para a mesma arma conforme o tipo de evento:
# facas variam por skin e as incendiarias aparecem como molotov/incgrenade/inferno.
ALIAS_ARMA = {"incgrenade": "inferno", "molotov": "inferno"}
# nos eventos de DANO a M4A1-S vem como "m4a1", a USP-S como "hkp2000" e a MP5-SD como "mp7";
# nos eventos de TIRO e de KILL vem o nome verdadeiro. variantes[dano] = nomes possiveis no tiro
VARIANTES_DANO = {"m4a1": ("m4a1_silencer", "m4a1"), "hkp2000": ("usp_silencer", "hkp2000"),
                  "mp7": ("mp5sd", "mp7")}


def norm_arma(w) -> str:
    a = str(w or "").lower().replace("weapon_", "").strip()
    if "knife" in a or "bayonet" in a:
        return "knife"
    return ALIAS_ARMA.get(a, a)


def nome_arma(a: str) -> str:
    a = norm_arma(a)
    if a in NOME_ARMA:
        return NOME_ARMA[a]
    if "knife" in a or "bayonet" in a:
        return "Faca"
    return a.upper() if a else "?"


def tipo_arma(a: str) -> str:
    """'arma' (de fogo), 'utilidade' (granadas), 'faca' ou 'zeus'."""
    a = norm_arma(a)
    if "knife" in a or "bayonet" in a:
        return "faca"
    if a == "taser":
        return "zeus"
    if a in UTILIDADE:
        return "utilidade"
    return "arma"


def metricas(d: dict) -> dict:
    """Acrescenta as metricas derivadas a um dicionario de totais de uma arma."""
    # as porcentagens usam so os tiros/kills que vieram de demo (o ao vivo nao sabe quem acertou)
    tiros = d.get("tiros_demo", d.get("tiros")) or 0
    kills = d.get("kills_demo", d.get("kills")) or 0
    acertos = d.get("acertos") or 0
    out = dict(d)
    out["precisao"] = round(100.0 * (d.get("tiros_com_acerto") or 0) / tiros, 1) if tiros else None
    out["dano_por_tiro"] = round((d.get("dano") or 0) / tiros, 1) if tiros else None
    out["hs_pct"] = round(100.0 * (d.get("hs_acertos") or 0) / acertos, 1) if acertos else None
    out["kills_por_100_tiros"] = round(100.0 * kills / tiros, 1) if tiros else None
    return out


def _percentis(valores: dict) -> dict:
    """valores: chave -> numero. Retorna chave -> percentil 0..100 (empates recebem a media)."""
    itens = sorted(valores.items(), key=lambda kv: kv[1])
    n = len(itens)
    if n == 1:
        return {itens[0][0]: 100.0}
    out = {}
    i = 0
    while i < n:
        j = i
        while j + 1 < n and itens[j + 1][1] == itens[i][1]:
            j += 1
        p = 100.0 * ((i + j) / 2.0) / (n - 1)
        for k in range(i, j + 1):
            out[itens[k][0]] = round(p, 1)
        i = j + 1
    return out


def ranking(armas: list, min_tiros: int = MIN_TIROS_RANKING) -> list:
    """
    Recebe uma lista de dicionarios (um por arma, ja somados) e devolve a lista com as
    metricas e a "nota" (0-100): media dos percentis de dano por tiro, precisao, % de
    headshot e kills por 100 tiros, entre as armas de fogo com tiros suficientes.
    Armas sem amostra, granadas, faca e zeus ficam sem nota.
    """
    itens = [metricas(a) for a in armas]
    elegiveis = [a for a in itens if a.get("tipo", tipo_arma(a["arma"])) == "arma"
                 and (a.get("tiros_demo", a.get("tiros")) or 0) >= min_tiros and a["dano_por_tiro"] is not None]
    for a in itens:
        a["nota"] = None
        a["amostra_pequena"] = a.get("tipo", tipo_arma(a["arma"])) == "arma" and (a.get("tiros_demo", a.get("tiros")) or 0) < min_tiros
    if len(elegiveis) >= 2:
        metr = {
            "dano_por_tiro": _percentis({a["arma"]: a["dano_por_tiro"] for a in elegiveis}),
            "precisao": _percentis({a["arma"]: a["precisao"] or 0 for a in elegiveis}),
            "hs_pct": _percentis({a["arma"]: a["hs_pct"] or 0 for a in elegiveis}),
            "kills_por_100_tiros": _percentis({a["arma"]: a["kills_por_100_tiros"] or 0 for a in elegiveis}),
        }
        por_arma = {a["arma"]: a for a in itens}
        for arma in por_arma:
            if all(arma in m for m in metr.values()):
                por_arma[arma]["nota"] = round(sum(m[arma] for m in metr.values()) / len(metr), 1)
    return itens


ORDENACOES = {
    "nota": ("Nota", lambda a: (a["nota"] if a["nota"] is not None else -1, a.get("dano") or 0)),
    "dano": ("Dano total", lambda a: a.get("dano") or 0),
    "kills": ("Kills", lambda a: (a.get("kills") or 0, a.get("dano") or 0)),
    "precisao": ("Precisão", lambda a: (a["precisao"] if a["precisao"] is not None and not a["amostra_pequena"] else -1)),
    "dano_tiro": ("Dano por tiro", lambda a: (a["dano_por_tiro"] if a["dano_por_tiro"] is not None and not a["amostra_pequena"] else -1)),
    "hs": ("% Headshot", lambda a: (a["hs_pct"] if a["hs_pct"] is not None and not a["amostra_pequena"] else -1)),
}


def ordenar(itens: list, como: str = "dano") -> list:
    chave = ORDENACOES.get(como, ORDENACOES["dano"])[1]
    return sorted(itens, key=chave, reverse=True)
