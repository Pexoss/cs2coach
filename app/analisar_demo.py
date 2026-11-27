#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
CS2 Coach - analisador de demos (.dem)

Le o arquivo de demo de uma partida (gravado oficialmente pelo jogo, sem
nenhum risco de VAC) e gera um relatorio HTML com:
  - tiros em movimento x parado (counter-strafe)
  - rajadas longas x curtas (controle de spray)
  - AWP (noscope, tiro em movimento, acerto) e escopeta (distancia)
  - como voce morre (correndo, sem atirar, pelas costas, abertura de round)
  - constancia round a round e evolucao entre partidas

Uso:
  python analisar_demo.py partida.dem --jogador "SeuNick"
  python analisar_demo.py pasta_de_demos --jogador "SeuNick"
  python analisar_demo.py partida.dem --listar

Sem --jogador, usa o seu SteamID salvo no Hub (ou detectado pela pasta do Steam).
Cada partida analisada fica guardada em dados/cs2coach.db (veja tudo no Hub: python hub.py).
"""
from __future__ import annotations

import argparse
import bisect
import html
import json
import math
import statistics
import sys
from datetime import datetime
from pathlib import Path

try:
    import pandas as pd
    from demoparser2 import DemoParser
except ImportError:  # pragma: no cover
    print("Faltam as bibliotecas. Rode:  pip install -r requirements.txt")
    sys.exit(1)

sys.path.insert(0, str(Path(__file__).resolve().parent))
import armas as _armas  # noqa: E402
import armazem  # noqa: E402

# --------------------------------------------------------------------------
# Constantes (heuristicas ajustaveis)
# --------------------------------------------------------------------------
UNIDADE_PARA_METROS = 0.0254   # 1 unidade do jogo ~ 1 polegada
VEL_PARADO = 40.0              # u/s: abaixo disso a arma esta (quase) com precisao total
VEL_CORRENDO = 110.0           # u/s: acima disso voce esta correndo
TICKRATE = 64                  # demos de matchmaking: 64 ticks/s
FORA_DE_VISAO_GRAUS = 60.0     # inimigo a mais de 60 graus da sua mira
DIST_ESCOPETA_LONGE = 8.0      # metros
MIN_AMOSTRA = 10

CLASSES = {
    "rifle": {"ak47", "m4a1", "m4a1_silencer", "galilar", "famas", "aug", "sg556"},
    "awp": {"awp"},
    "sniper": {"ssg08", "scar20", "g3sg1"},
    "escopeta": {"nova", "xm1014", "mag7", "sawedoff"},
    "smg": {"mac10", "mp9", "mp7", "mp5sd", "ump45", "p90", "bizon"},
    "pistola": {"glock", "hkp2000", "usp_silencer", "p250", "fiveseven", "tec9",
                "cz75a", "deagle", "elite", "revolver"},
    "metralhadora": {"m249", "negev"},
}
ARMA_PARA_CLASSE = {a: c for c, armas in CLASSES.items() for a in armas}
NOME_CLASSE = {
    "rifle": "Rifle", "awp": "AWP", "sniper": "Outros snipers", "escopeta": "Escopeta",
    "smg": "SMG", "pistola": "Pistola", "metralhadora": "Metralhadora",
}
# distancia (ticks) para considerar tiros consecutivos como a mesma rajada
GAP_RAJADA = {"rifle": 16, "smg": 16, "metralhadora": 16, "pistola": 10}

GRUPOS_TAMANHO = [("1 bala (tap)", 1, 1), ("2-3 balas", 2, 3),
                  ("4-6 balas", 4, 6), ("7+ balas (spray)", 7, 999)]
GRUPOS_VEL = [("Parado", 0.0, VEL_PARADO), ("Andando", VEL_PARADO, VEL_CORRENDO),
              ("Correndo", VEL_CORRENDO, 1e9)]


# --------------------------------------------------------------------------
# Utilitarios
# --------------------------------------------------------------------------
def norm_arma(w) -> str:
    return str(w).lower().replace("weapon_", "").strip()


def classe_da_arma(w):
    return ARMA_PARA_CLASSE.get(norm_arma(w))


def pct(a, b):
    return None if not b else round(100.0 * a / b, 1)


def media(valores):
    valores = [v for v in valores if v is not None and not math.isnan(v)]
    return round(sum(valores) / len(valores), 1) if valores else None


def vel_xy(df, prefixo):
    return (df[f"{prefixo}_velocity_X"] ** 2 + df[f"{prefixo}_velocity_Y"] ** 2) ** 0.5


def dist_metros(df):
    dx = df["attacker_X"] - df["user_X"]
    dy = df["attacker_Y"] - df["user_Y"]
    dz = df["attacker_Z"] - df["user_Z"]
    return ((dx ** 2 + dy ** 2 + dz ** 2) ** 0.5) * UNIDADE_PARA_METROS


def evento(parser, nome, player=None, other=None):
    kwargs = {}
    if player:
        kwargs["player"] = player
    if other:
        kwargs["other"] = other
    try:
        return parser.parse_event(nome, **kwargs)
    except Exception as exc:  # evento ausente nessa demo
        if nome in ("begin_new_match", "round_freeze_end", "round_end"):
            return pd.DataFrame({"tick": []})
        raise RuntimeError(f"Não consegui ler o evento '{nome}': {exc}") from exc


# --------------------------------------------------------------------------
# Leitura da demo
# --------------------------------------------------------------------------
class Demo:
    def __init__(self, caminho: Path):
        self.caminho = Path(caminho)
        self.parser = DemoParser(str(self.caminho))
        try:
            self.mapa = self.parser.parse_header().get("map_name", "?")
        except Exception:
            self.mapa = "?"
        info = self.parser.parse_player_info()
        info = info.copy()
        info["steamid"] = info["steamid"].map(lambda x: str(int(x)))
        self.jogadores = info

        base = ["X", "Y", "Z", "velocity_X", "velocity_Y"]
        self.fire = evento(self.parser, "weapon_fire",
                           player=base + ["pitch", "yaw", "is_scoped", "team_num"])
        self.hurt = evento(self.parser, "player_hurt", player=base + ["team_num"])
        self.death = evento(self.parser, "player_death",
                            player=base + ["yaw", "team_num", "flash_duration"])
        ticks = [int(df["tick"].max()) for df in (self.fire, self.hurt, self.death) if len(df)]
        self.ultimo_tick = max(ticks) if ticks else 0
        self.rounds = self._segmentar_rounds()
        self.vencedores = self._vencedores()

        self.fire = self._marcar(self.fire)
        self.hurt = self._marcar(self.hurt)
        self.death = self._marcar(self.death)

    def _segmentar_rounds(self):
        b = evento(self.parser, "begin_new_match")
        inicio = int(b["tick"].max()) if len(b) else 0
        fe = sorted(int(t) for t in evento(self.parser, "round_freeze_end")["tick"] if t > inicio)
        re_ = sorted(int(t) for t in evento(self.parser, "round_end")["tick"])
        segs = []
        for i, f in enumerate(fe):
            prox = fe[i + 1] if i + 1 < len(fe) else self.ultimo_tick
            fins = [t for t in re_ if f < t <= prox]
            segs.append((i + 1, f, fins[0] if fins else prox))
        if not segs:  # demo sem eventos de round: trata tudo como um round
            segs = [(1, inicio, self.ultimo_tick)]
        return segs

    def _vencedores(self):
        """{round: time vencedor (2 = T, 3 = CT)}; vazio se a demo nao trouxe o vencedor."""
        out = {}
        try:
            re_ = evento(self.parser, "round_end")
            if "winner" not in re_.columns:
                return out
            for rn, _, fim in self.rounds:
                linhas = re_[re_["tick"] == fim]
                if len(linhas):
                    w = linhas.iloc[0]["winner"]
                    w = {"T": 2, "CT": 3}.get(str(w).upper(), w)
                    out[rn] = int(w)
        except Exception:
            pass
        return out

    def _marcar(self, df):
        if not len(df):
            df = df.copy()
            df["rnd"] = []
            return df
        inicios = [s[1] for s in self.rounds]
        rnd = []
        for t in df["tick"]:
            i = bisect.bisect_right(inicios, t) - 1
            rnd.append(self.rounds[i][0] if i >= 0 and t <= self.rounds[i][2] else 0)
        out = df.copy()
        out["rnd"] = rnd
        return out[out["rnd"] > 0].copy()


def achar_jogador(demo: Demo, nome=None, steamid=None):
    info = demo.jogadores
    if steamid:
        sel = info[info["steamid"] == str(steamid)]
    elif nome:
        sel = info[info["name"].str.lower().str.contains(nome.lower(), regex=False, na=False)]
    else:
        return None, info
    if len(sel) == 1:
        r = sel.iloc[0]
        return (r["steamid"], r["name"]), sel
    return None, sel


# --------------------------------------------------------------------------
# Analise
# --------------------------------------------------------------------------
def montar_rajadas(fire_jogador):
    rajadas = []
    atual = None
    for r in fire_jogador.sort_values("tick").itertuples():
        classe = r.classe
        gap = GAP_RAJADA.get(classe, 0)
        if (atual and atual["arma"] == r.arma and atual["rnd"] == r.rnd
                and r.tick - atual["t1"] <= gap):
            atual["t1"] = int(r.tick)
            atual["n"] += 1
            continue
        atual = {
            "arma": r.arma, "classe": classe, "rnd": int(r.rnd),
            "t0": int(r.tick), "t1": int(r.tick), "n": 1,
            "vel": float(r.vel), "scoped": bool(r.user_is_scoped),
            "hits": 0, "dano": 0, "hs": 0, "kill": False, "dist_hits": [],
        }
        rajadas.append(atual)
    return rajadas


def ligar(rajadas, t0s, tick, classe, folga=12):
    i = bisect.bisect_right(t0s, tick) - 1
    for j in range(i, max(i - 4, -1), -1):
        r = rajadas[j]
        if r["classe"] == classe and tick <= r["t1"] + folga:
            return r
    return None


def resumo_classe(rajs):
    if not rajs:
        return None
    tiros = sum(r["n"] for r in rajs)
    hits = sum(r["hits"] for r in rajs)
    com_acerto = sum(1 for r in rajs if r["hits"] > 0)
    out = {
        "rajadas": len(rajs), "tiros": tiros, "acertos": hits,
        "kills": sum(1 for r in rajs if r["kill"]),
        "pct_rajadas_com_acerto": pct(com_acerto, len(rajs)),
        "precisao_bala": pct(hits, tiros),
        "hs_pct_acertos": pct(sum(r["hs"] for r in rajs), hits),
        "pct_scoped": pct(sum(1 for r in rajs if r["scoped"]), len(rajs)),
        "por_velocidade": [], "por_tamanho": [],
    }
    for nome, lo, hi in GRUPOS_VEL:
        g = [r for r in rajs if lo <= r["vel"] < hi]
        out["por_velocidade"].append({
            "grupo": nome, "rajadas": len(g), "share": pct(len(g), len(rajs)),
            "pct_com_acerto": pct(sum(1 for r in g if r["hits"] > 0), len(g)),
        })
    for nome, lo, hi in GRUPOS_TAMANHO:
        g = [r for r in rajs if lo <= r["n"] <= hi]
        t = sum(r["n"] for r in g)
        out["por_tamanho"].append({
            "grupo": nome, "rajadas": len(g), "tiros": t,
            "share_tiros": pct(t, tiros),
            "precisao_bala": pct(sum(r["hits"] for r in g), t),
            "pct_com_acerto": pct(sum(1 for r in g if r["hits"] > 0), len(g)),
        })
    dists = [d for r in rajs for d in r["dist_hits"]]
    out["dist_media_acertos_m"] = media(dists)
    out["dist_acertos"] = dists
    return out


def estatisticas_armas(fa, meus, kills) -> list:
    """
    Dano, tiros, tiros com acerto, kills, headshots e distancia por arma.
    - tiros: eventos weapon_fire; tiros_com_acerto: tiros que acertaram um inimigo
      (cada tiro conta uma vez, mesmo na escopeta, onde um tiro tem varios chumbos)
    - acertos: eventos de dano ao inimigo (chumbos/balas); dano: vida tirada (sem fogo amigo)
    """
    por = {}

    def reg(a):
        return por.setdefault(a, {"arma": a, "tiros": 0, "tiros_com_acerto": 0, "acertos": 0, "dano": 0,
                                  "kills": 0, "hs_kills": 0, "hs_acertos": 0, "_dist": []})

    ticks_por = {}
    for r in fa.itertuples():
        a = _armas.norm_arma(r.weapon)
        reg(a)["tiros"] += 1
        ticks_por.setdefault(a, []).append(int(r.tick))
    for a in ticks_por:
        ticks_por[a].sort()
    marcados = {a: set() for a in ticks_por}

    def resolver(a_evento, tick):
        """Nome verdadeiro da arma: o jogo escreve a M4A1-S como 'm4a1' no evento de dano, por exemplo."""
        a_evento = _armas.norm_arma(a_evento)
        candidatas = _armas.VARIANTES_DANO.get(a_evento, (a_evento,))
        melhor, melhor_t = None, None
        for c in candidatas:
            ts = ticks_por.get(c)
            if not ts:
                continue
            i = bisect.bisect_right(ts, tick) - 1
            if i >= 0 and tick - ts[i] <= 8 and (melhor_t is None or ts[i] > melhor_t):
                melhor, melhor_t = c, ts[i]
        if melhor:
            return melhor
        usadas = [c for c in candidatas if c in ticks_por]   # so uma variante foi usada na partida
        return usadas[0] if len(usadas) == 1 else a_evento

    for r in meus.sort_values("tick").itertuples():
        a = resolver(r.weapon, int(r.tick))
        d = reg(a)
        d["acertos"] += 1
        d["dano"] += int(r.dmg_health)
        if r.hitgroup == "head":
            d["hs_acertos"] += 1
        d["_dist"].append(float(r.dist))
        ts = ticks_por.get(a)
        if ts:
            i = bisect.bisect_right(ts, int(r.tick)) - 1
            if i >= 0 and int(r.tick) - ts[i] <= 8:
                marcados[a].add(i)
    for a, conj in marcados.items():
        por[a]["tiros_com_acerto"] = len(conj)
    for r in kills.itertuples():
        a = resolver(r.weapon, int(r.tick))
        d = reg(a)
        d["kills"] += 1
        if r.headshot:
            d["hs_kills"] += 1
    out = []
    for a, d in por.items():
        dist = d.pop("_dist")
        d["nome"] = _armas.nome_arma(a)
        d["tipo"] = _armas.tipo_arma(a)
        d["classe"] = ARMA_PARA_CLASSE.get(a)
        d["dist_media_m"] = media(dist)
        out.append(d)
    out.sort(key=lambda x: (-x["dano"], -x["kills"], -x["tiros"]))
    return out


# --------------------------------------------------------------------------
# Kills detalhadas (onde e como) e metricas no estilo Scope.gg
# --------------------------------------------------------------------------
def _flag(r, nome):
    v = getattr(r, nome, False)
    try:
        return bool(v) and v == v      # NaN vira False
    except Exception:
        return False


def _num(v):
    try:
        v = float(v)
        return round(v, 1) if v == v else None
    except (TypeError, ValueError):
        return None


def detalhar_kills(demo, sid, kills, meus, aberturas):
    """Uma linha por kill sua: quando, onde, com que arma, a que distancia e como (headshot, parede, fumaca...)."""
    inicio = {rn: ini for rn, ini, _ in demo.rounds}
    golpes = {}
    for r in meus.sort_values("tick").itertuples():
        golpes.setdefault((int(r.rnd), str(r.user_steamid)), []).append(int(r.tick))

    def inicio_do_duelo(chave, tick_kill):
        """Primeiro dano da sequencia que terminou na kill (golpes com menos de 1,5 s entre si)."""
        ini = None
        for t in reversed([t for t in golpes.get(chave, []) if t <= tick_kill]):
            if ini is None or ini - t <= int(1.5 * TICKRATE):
                ini = t
            else:
                break
        return ini
    ab_ticks = {int(r.tick) for r in aberturas.values() if str(r.attacker_steamid) == sid}
    out = []
    for r in kills.sort_values("tick").itertuples():
        arma = norm_arma(r.weapon)
        t1 = inicio_do_duelo((int(r.rnd), str(r.user_steamid)), int(r.tick))
        ttk = max(0, int(r.tick) - t1) * 1000.0 / TICKRATE if t1 is not None else None
        out.append({
            "rnd": int(r.rnd), "t": round((int(r.tick) - inicio.get(int(r.rnd), 0)) / TICKRATE, 1),
            "tick": int(r.tick), "arma": arma, "dist": _num(r.dist), "hs": _flag(r, "headshot"),
            "parede": bool(getattr(r, "penetrated", 0) or 0), "sem_mira": _flag(r, "noscope"),
            "fumaca": _flag(r, "thrusmoke"), "cego": _flag(r, "attackerblind"),
            "abertura": int(r.tick) in ab_ticks, "ttk_ms": round(ttk) if ttk is not None else None,
            "vel_voce": _num(r.vel_atacante), "vel_inimigo": _num(r.vel_vitima),
            "vitima": str(getattr(r, "user_name", "") or ""),
            "x": _num(r.attacker_X), "y": _num(r.attacker_Y), "vx": _num(r.user_X), "vy": _num(r.user_Y),
        })
    return out


GRUPOS_ACERTO = {"head": "cabeça", "chest": "peito", "stomach": "barriga", "left_arm": "braço", "right_arm": "braço",
                 "left_leg": "perna", "right_leg": "perna", "neck": "pescoço", "generic": "granada/outro", "gear": "colete"}


def detalhar_acertos(demo, meus):
    """Dano por round e onde os tiros acertaram (cabeca, peito, perna...), com distancia media do acerto."""
    por_round = {}
    total = {}
    for r in meus.itertuples():
        rn = int(r.rnd)
        g = GRUPOS_ACERTO.get(str(r.hitgroup), str(r.hitgroup))
        d = por_round.setdefault(rn, {"rnd": rn, "dano": 0, "acertos": 0, "locais": {}, "dists": []})
        d["dano"] += int(r.dmg_health)
        d["acertos"] += 1
        d["locais"][g] = d["locais"].get(g, 0) + 1
        dist = _num(getattr(r, "dist", None))
        if dist is not None:
            d["dists"].append(dist)
        total[g] = total.get(g, 0) + 1
    rounds = []
    for rn, _, _ in demo.rounds:
        d = por_round.get(rn) or {"rnd": rn, "dano": 0, "acertos": 0, "locais": {}, "dists": []}
        dists = d.pop("dists")
        d["dist_media"] = round(sum(dists) / len(dists), 1) if dists else None
        rounds.append(d)
    return {"rounds": rounds, "locais": total}


def tipo_de_duelo(m):
    """Rotulo unico do duelo em que voce morreu (usado no mapa e nas somas)."""
    if m.get("abertura"):
        return "Abertura de round"
    if m.get("fora_visao"):
        return "Por trás/lado (fora da mira)"
    if (m.get("dist") or 0) >= 25:
        return "Longa distância"
    if (m.get("dist") or 0) < 8:
        return "Curta distância"
    return "Média distância"


UTILITARIOS = {"hegrenade", "inferno", "molotov", "incgrenade"}


def _vivos(demo, meu_time, rn, tick, n_time):
    """Quantos jogadores vivos (seu time, outro time) imediatamente antes de 'tick' no round."""
    d = demo.death
    d = d[(d["rnd"] == rn) & (d["tick"] < tick)]
    meus_mortos = int((d["user_team_num"] == meu_time).sum())
    deles_mortos = int((d["user_team_num"] != meu_time).sum())
    return max(0, n_time[meu_time] - meus_mortos), max(0, n_time[5 - meu_time] - deles_mortos)


def nota_de_impacto(demo, sid, kills, mortes, aberturas, meus, rounds_info):
    """Nota 0-100 por round: dano, kills, abertura, utilitario, kills em desvantagem numerica e resultado do round.
    Heuristica propria (nao e a nota de nenhum site): serve para comparar os seus rounds entre si."""
    info = demo.jogadores
    meu_time = None
    sel = info[info["steamid"] == str(sid)]
    if len(sel):
        meu_time = int(sel.iloc[0]["team_number"])
    n_time = {}
    for t in (2, 3):
        n_time[t] = max(1, min(5, int((info["team_number"] == t).sum())))
    util = {}
    for r in meus.itertuples():
        if norm_arma(r.weapon) in UTILITARIOS:
            util[int(r.rnd)] = util.get(int(r.rnd), 0) + int(r.dmg_health)
    ass = {}
    if "assister_steamid" in demo.death.columns:
        a = demo.death[(demo.death["assister_steamid"].astype(str) == sid)
                       & (demo.death["attacker_team_num"] != demo.death["user_team_num"])]
        for r in a.itertuples():
            ass[int(r.rnd)] = ass.get(int(r.rnd), 0) + 1
    kills_por = {}
    for r in kills.itertuples():
        kills_por.setdefault(int(r.rnd), []).append(int(r.tick))
    out = []
    for ri in rounds_info:
        rn = ri["rnd"]
        pontos, tags = 0.0, []
        dano = ri["dano"]
        pontos += min(dano, 150) / 100 * 25
        if dano:
            tags.append(f"{dano} de dano")
        nk = ri["kills"]
        pontos += nk * 14
        if nk:
            tags.append(f"{nk} kill" + ("s" if nk != 1 else ""))
        ab = aberturas.get(rn)
        if ab is not None and str(ab.attacker_steamid) == sid:
            pontos += 10
            tags.append("abriu o round")
        elif ab is not None and str(ab.user_steamid) == sid:
            pontos -= 8
            tags.append("morreu na abertura")
        if meu_time is not None:
            for t in kills_por.get(rn, []):
                eu, eles = _vivos(demo, meu_time, rn, t, n_time)
                if eles > eu:
                    pontos += 6
                    tags.append(f"kill em desvantagem ({eu}x{eles})")
        u = util.get(rn, 0)
        if u:
            pontos += min(u, 60) / 60 * 8
            tags.append(f"{u} de dano de granada")
        na = ass.get(rn, 0)
        if na:
            pontos += 4 * na
            tags.append(f"{na} assistência" + ("s" if na != 1 else ""))
        venceu = None
        if meu_time is not None and rn in demo.vencedores:
            venceu = demo.vencedores[rn] == meu_time
            if venceu:
                pontos += 8
                tags.append("round ganho")
                if not ri["morreu"]:
                    pontos += 4
                    tags.append("sobreviveu")
        if ri["morreu"] and not dano and not nk:
            pontos -= 6
            tags.append("morreu sem causar dano")
        out.append({"rnd": rn, "nota": int(max(0, min(100, round(pontos)))), "tags": tags, "venceu": venceu,
                    "util": u})
    return out


def momentos_para_rever(demo, detalhes_mortes, rajadas, ticks_tiros, nota_por_round, kills_round=None):
    """Rounds com erro de verdade, cada um com o tick para pular direto na demo (5 s antes do lance)."""
    inicio = {rn: ini for rn, ini, _ in demo.rounds}
    out = []

    def add(rn, tick, tipo, texto, gravidade):
        out.append({"rnd": rn, "tick": int(tick), "ir_para": max(0, int(tick) - 5 * TICKRATE),
                    "t": round((int(tick) - inicio.get(rn, 0)) / TICKRATE, 1), "tipo": tipo, "texto": texto, "gravidade": gravidade})
    for m in detalhes_mortes:
        tick = m.get("tick")
        if tick is None:
            continue
        lo = bisect.bisect_left(ticks_tiros, tick - int(4 * TICKRATE))
        hi = bisect.bisect_right(ticks_tiros, tick)
        if hi - lo == 0:
            ja = (kills_round or {}).get(m["rnd"], 0)
            extra = f" Você já tinha {ja} kill(s) no round." if ja else ""
            add(m["rnd"], tick, "sem_atirar", f"Morreu sem atirar nos 4 s antes (a {m['dist']:.0f} m, {_nome_curto(m['arma'])}).{extra}",
                1 if ja else 3)
        elif m.get("vel_voce", 0) >= VEL_CORRENDO and m.get("vel_inimigo", 999) < VEL_PARADO:
            add(m["rnd"], tick, "correndo", "Morreu correndo contra um inimigo parado: pare para atirar.", 2)
        elif m.get("cego"):
            add(m["rnd"], tick, "cego", "Morreu cego por flash.", 1)
        elif m.get("abertura") and m.get("fora_visao"):
            add(m["rnd"], tick, "abertura", "Abertura perdida com o inimigo fora da sua mira.", 2)
    for r in rajadas:
        if r["classe"] in ("rifle", "smg", "metralhadora") and r["n"] >= 10 and not r["kill"]:
            add(r["rnd"], r["t0"], "rajada", f"Rajada de {r['n']} balas sem kill ({_nome_curto(r['arma'])}): solte o gatilho e recomece.", 2)
    out.sort(key=lambda x: (-x["gravidade"], x["rnd"], x["tick"]))
    notas = {n["rnd"]: n["nota"] for n in nota_por_round}
    for x in out:
        x["nota_round"] = notas.get(x["rnd"])
    return out


def _nome_curto(a):
    return str(a).replace("weapon_", "")



def metricas_scope(kills_det, meus, classes, geral):
    """Metricas que o jogo nao mostra: HS por acerto, tempo para matar, kills parado etc."""
    armas_gun = [k for k in kills_det if ARMA_PARA_CLASSE.get(k["arma"])]
    rifle = [k for k in armas_gun if ARMA_PARA_CLASSE.get(k["arma"]) == "rifle"]

    def mediana(lista):
        v = [x for x in lista if x is not None]
        return round(statistics.median(v)) if v else None

    gun = meus[meus["classe"].notna()] if len(meus) else meus
    hits = len(gun)
    hs_hits = int((gun["hitgroup"] == "head").sum()) if hits else 0
    g_rifle = gun[gun["classe"] == "rifle"] if hits else gun
    n = len(kills_det)
    return {
        "hs_acertos_pct": pct(hs_hits, hits),
        "hs_acertos_pct_rifle": pct(int((g_rifle["hitgroup"] == "head").sum()), len(g_rifle)) if len(g_rifle) else None,
        "ttk_ms": mediana([k["ttk_ms"] for k in armas_gun]),
        "ttk_rifle_ms": mediana([k["ttk_ms"] for k in rifle]),
        "kills_parado_pct": pct(sum(1 for k in kills_det if (k["vel_voce"] or 0) < VEL_PARADO), n),
        "kills_correndo_pct": pct(sum(1 for k in kills_det if (k["vel_voce"] or 0) >= VEL_CORRENDO), n),
        "kills_parede": sum(1 for k in kills_det if k["parede"]),
        "kills_fumaca": sum(1 for k in kills_det if k["fumaca"]),
        "kills_sem_mira": sum(1 for k in kills_det if k["sem_mira"]),
        "kills_cego": sum(1 for k in kills_det if k["cego"]),
        "dist_media_kill_m": media([k["dist"] for k in kills_det if k["dist"] is not None]),
        "kills_longe_pct": pct(sum(1 for k in kills_det if (k["dist"] or 0) >= 20), n),
        "precisao_rifle": (classes.get("rifle") or {}).get("precisao_bala"),
        "dano_por_round": geral.get("adr"),
    }


def analisar_jogador(demo: Demo, sid: str, nome: str) -> dict:
    n_rounds = len(demo.rounds)

    # --- tiros e rajadas -----------------------------------------------
    f = demo.fire[demo.fire["user_steamid"].astype(str) == sid].copy()
    f["arma"] = f["weapon"].map(norm_arma)
    f["classe"] = f["arma"].map(ARMA_PARA_CLASSE)
    f = f[f["classe"].notna()].copy()
    f["vel"] = vel_xy(f, "user")
    rajadas = montar_rajadas(f)
    t0s = [r["t0"] for r in rajadas]
    ticks_tiros = sorted(int(t) for t in f["tick"])

    # --- acertos (so inimigos) ------------------------------------------
    h = demo.hurt
    meus = h[(h["attacker_steamid"].astype(str) == sid)
             & (h["attacker_team_num"] != h["user_team_num"])].copy()
    meus["classe"] = meus["weapon"].map(classe_da_arma)
    meus["dist"] = dist_metros(meus) if len(meus) else []
    for r in meus.sort_values("tick").itertuples():
        if not isinstance(r.classe, str):
            continue
        raj = ligar(rajadas, t0s, int(r.tick), r.classe)
        if raj is not None:
            raj["hits"] += 1
            raj["dano"] += int(r.dmg_health)
            raj["hs"] += 1 if r.hitgroup == "head" else 0
            raj["dist_hits"].append(float(r.dist))

    # --- mortes e kills ---------------------------------------------------
    d = demo.death
    inimigo = d["attacker_team_num"] != d["user_team_num"]
    com_atacante = d["attacker_steamid"].notna() & (d["attacker_steamid"].astype(str) != d["user_steamid"].astype(str))
    # mortes sem inimigo (queda, suicidio, "world") nao entram na analise de duelos
    suicidios = int(((d["user_steamid"].astype(str) == sid) & ~(inimigo & com_atacante)).sum())
    d = d[inimigo & com_atacante].copy()
    d["dist"] = dist_metros(d) if len(d) else []
    d["vel_atacante"] = vel_xy(d, "attacker") if len(d) else []
    d["vel_vitima"] = vel_xy(d, "user") if len(d) else []

    # abertura de cada round = primeira morte entre times
    aberturas = {}
    for r in d.sort_values("tick").itertuples():
        aberturas.setdefault(int(r.rnd), r)

    kills = d[d["attacker_steamid"].astype(str) == sid].copy()
    mortes = d[d["user_steamid"].astype(str) == sid].copy()
    assists = int((d["assister_steamid"].astype(str) == sid).sum()) if "assister_steamid" in d else 0
    # (assister nao foi pedido como propriedade; coluna existe nos eventos de morte)

    for r in kills.itertuples():
        c = classe_da_arma(r.weapon)
        if isinstance(c, str):
            raj = ligar(rajadas, t0s, int(r.tick), c)
            if raj is not None:
                raj["kill"] = True

    # --- dano por round (limite de 100 por vitima) ----------------------
    dano_por = {}
    for r in meus.itertuples():
        chave = (int(r.rnd), str(r.user_steamid))
        dano_por[chave] = min(100, dano_por.get(chave, 0) + int(r.dmg_health))
    dano_round = {rn: 0 for rn, _, _ in demo.rounds}
    for (rn, _), v in dano_por.items():
        dano_round[rn] += v
    kills_round = {rn: 0 for rn, _, _ in demo.rounds}
    for r in kills.itertuples():
        kills_round[int(r.rnd)] += 1
    morte_round = {int(r.rnd): (int(r.tick)) for r in mortes.itertuples()}

    rounds_info = []
    for rn, ini, _ in demo.rounds:
        rounds_info.append({
            "rnd": rn, "dano": dano_round[rn], "kills": kills_round[rn],
            "morreu": rn in morte_round,
            "morreu_em_s": round((morte_round[rn] - ini) / TICKRATE, 1) if rn in morte_round else None,
        })

    for rn, ini, fim in demo.rounds:
        rounds_info[rn - 1]["tick_ini"], rounds_info[rn - 1]["tick_fim"] = int(ini), int(fim)

    # --- como voce morre ---------------------------------------------------
    detalhes_mortes = []
    inicio_round = {rn: ini for rn, ini, _ in demo.rounds}
    for r in mortes.itertuples():
        t = int(r.tick)
        lo = bisect.bisect_left(ticks_tiros, t - int(1.5 * TICKRATE))
        hi = bisect.bisect_right(ticks_tiros, t)
        tiros_antes = hi - lo
        bearing = math.degrees(math.atan2(r.attacker_Y - r.user_Y, r.attacker_X - r.user_X))
        diff = abs((bearing - r.user_yaw + 180) % 360 - 180)
        ab = aberturas.get(int(r.rnd))
        detalhes_mortes.append({
            "rnd": int(r.rnd), "tick": t, "arma": norm_arma(r.weapon), "dist": float(r.dist),
            "vel_voce": float(r.vel_vitima), "vel_inimigo": float(r.vel_atacante),
            "tiros_antes": tiros_antes, "fora_visao": diff > FORA_DE_VISAO_GRAUS,
            "angulo": round(float(diff), 1),
            "cego": float(r.user_flash_duration) > 1.0,
            "abertura": bool(ab is not None and int(ab.tick) == t),
            "hs": bool(r.headshot),
            "t": round((t - inicio_round.get(int(r.rnd), 0)) / TICKRATE, 1),
            "parede": bool(getattr(r, "penetrated", 0) or 0), "fumaca": _flag(r, "thrusmoke"),
            "atacante": str(getattr(r, "attacker_name", "") or ""),
            "x": _num(r.user_X), "y": _num(r.user_Y), "ax": _num(r.attacker_X), "ay": _num(r.attacker_Y),
        })
    for m in detalhes_mortes:
        m["tipo"] = tipo_de_duelo(m)
    nm = len(detalhes_mortes)
    mortes_res = {
        "total": nm,
        "pct_correndo": pct(sum(1 for m in detalhes_mortes if m["vel_voce"] >= VEL_CORRENDO), nm),
        "pct_parado": pct(sum(1 for m in detalhes_mortes if m["vel_voce"] < VEL_PARADO), nm),
        "pct_inimigo_parado": pct(sum(1 for m in detalhes_mortes if m["vel_inimigo"] < VEL_PARADO), nm),
        "pct_voce_correndo_inimigo_parado": pct(sum(1 for m in detalhes_mortes
                                                    if m["vel_voce"] >= VEL_CORRENDO and m["vel_inimigo"] < VEL_PARADO), nm),
        "pct_sem_atirar": pct(sum(1 for m in detalhes_mortes if m["tiros_antes"] == 0), nm),
        "pct_fora_visao": pct(sum(1 for m in detalhes_mortes if m["fora_visao"]), nm),
        "pct_cego": pct(sum(1 for m in detalhes_mortes if m["cego"]), nm),
        "pct_abertura": pct(sum(1 for m in detalhes_mortes if m["abertura"]), nm),
        "dist_media_m": media([m["dist"] for m in detalhes_mortes]),
        "por_arma": {},
    }
    for m in detalhes_mortes:
        c = ARMA_PARA_CLASSE.get(m["arma"], "outra")
        mortes_res["por_arma"][c] = mortes_res["por_arma"].get(c, 0) + 1

    # --- aberturas de round (duelos de abertura) ---------------------------
    ab_ganhas = sum(1 for r in aberturas.values() if str(r.attacker_steamid) == sid)
    ab_perdidas = sum(1 for r in aberturas.values() if str(r.user_steamid) == sid)

    kills_res = {
        "total": len(kills),
        "hs_pct": pct(int(kills["headshot"].sum()) if len(kills) else 0, len(kills)),
        "dist_media_m": media(list(kills["dist"])) if len(kills) else None,
        "pct_voce_correndo": pct(int((kills["vel_atacante"] >= VEL_CORRENDO).sum()), len(kills)) if len(kills) else None,
    }

    dano_total = sum(dano_round.values())
    danos = [r["dano"] for r in rounds_info]
    cv = None
    if len(danos) >= 5 and statistics.mean(danos) > 0:
        cv = round(statistics.pstdev(danos) / statistics.mean(danos), 2)
    geral = {
        "kills": len(kills), "mortes": nm, "assistencias": assists, "suicidios": suicidios,
        "kd": round(len(kills) / nm, 2) if nm else float(len(kills)),
        "adr": round(dano_total / n_rounds, 1) if n_rounds else 0.0,
        "hs_pct": kills_res["hs_pct"], "rounds": n_rounds,
    }

    por_classe = {}
    for c in CLASSES:
        rs = [r for r in rajadas if r["classe"] == c]
        s = resumo_classe(rs)
        if s:
            por_classe[c] = s

    fa = demo.fire[demo.fire["user_steamid"].astype(str) == sid]
    try:
        data_demo = datetime.fromtimestamp(demo.caminho.stat().st_mtime).strftime("%Y-%m-%d %H:%M")
    except OSError:
        data_demo = datetime.now().strftime("%Y-%m-%d %H:%M")
    kills_det = detalhar_kills(demo, sid, kills, meus, aberturas)
    try:
        impacto = nota_de_impacto(demo, sid, kills, mortes, aberturas, meus, rounds_info)
    except Exception:
        impacto = []
    for ri, im in zip(rounds_info, impacto):
        ri["impacto"] = im["nota"]
    try:
        momentos = momentos_para_rever(demo, detalhes_mortes, rajadas, ticks_tiros, impacto, kills_round)
    except Exception:
        momentos = []
    res = {
        "meta": {"demo": demo.caminho.name, "mapa": demo.mapa, "jogador": nome, "steamid": sid,
                 "analisado_em": datetime.now().strftime("%Y-%m-%d %H:%M"), "data_demo": data_demo},
        "geral": geral,
        "armas": estatisticas_armas(fa, meus, kills),
        "classes": por_classe,
        "mortes": mortes_res,
        "kills": kills_res,
        "abertura": {"ganhas": ab_ganhas, "perdidas": ab_perdidas,
                     "winrate": pct(ab_ganhas, ab_ganhas + ab_perdidas)},
        "rounds": rounds_info,
        "constancia": {"cv_dano": cv,
                       "pct_rounds_dano_baixo": pct(sum(1 for x in danos if x < 40), len(danos))},
        "detalhes_mortes": detalhes_mortes,
        "kills_det": kills_det,
        "acertos_det": detalhar_acertos(demo, meus),
        "impacto": {"rounds": impacto, "media": media([i["nota"] for i in impacto])},
        "momentos": momentos,
        "scope": metricas_scope(kills_det, meus, por_classe, geral),
    }
    return res


# --------------------------------------------------------------------------
# Dicas
# --------------------------------------------------------------------------
def _get(grupos, nome, chave):
    for g in grupos:
        if g["grupo"] == nome:
            return g.get(chave)
    return None


def gerar_dicas(res: dict):
    dicas, fortes = [], []
    cl = res["classes"]
    m = res["mortes"]

    # --- rifle: movimento --------------------------------------------------
    r = cl.get("rifle")
    if r and r["rajadas"] >= MIN_AMOSTRA:
        pv = r["por_velocidade"]
        parado = _get(pv, "Parado", "pct_com_acerto")
        correndo = _get(pv, "Correndo", "pct_com_acerto")
        sh_corr = _get(pv, "Correndo", "share")
        n_corr = _get(pv, "Correndo", "rajadas") or 0
        n_par = _get(pv, "Parado", "rajadas") or 0
        if sh_corr is not None and sh_corr >= 15 and n_corr >= 5:
            if parado is not None and correndo is not None and correndo < parado - 8 and n_par >= 5:
                dicas.append({
                    "prioridade": 1, "titulo": "Você atira de rifle correndo",
                    "evidencia": (f"{sh_corr:.0f}% das suas rajadas de rifle começam com você correndo. "
                                  f"Nelas você acerta ao menos uma bala em {correndo:.0f}% das vezes, "
                                  f"contra {parado:.0f}% quando está parado."),
                    "acao": ("Faça o counter-strafe: toque a tecla contrária (A/D) para frear, espere o personagem "
                             "parar e só então clique. Treine isso no DM com a regra: nunca atirar em movimento."),
                })
            elif sh_corr >= 25:
                dicas.append({
                    "prioridade": 2, "titulo": "Muitos tiros de rifle em movimento",
                    "evidencia": f"{sh_corr:.0f}% das suas rajadas de rifle começam com você correndo.",
                    "acao": "Mesmo quando o resultado ainda não piora, o hábito é arriscado: pratique frear antes do primeiro tiro.",
                })
        elif parado is not None and parado >= 60 and n_par >= 8:
            fortes.append(f"Com o rifle, quando você atira parado você acerta ao menos uma bala em {parado:.0f}% das rajadas.")

        # spray
        pt = r["por_tamanho"]
        sh_long = _get(pt, "7+ balas (spray)", "share_tiros")
        pr_long = _get(pt, "7+ balas (spray)", "precisao_bala")
        t_short = sum(g["tiros"] for g in pt if g["grupo"] in ("1 bala (tap)", "2-3 balas"))
        h_short = sum(round((g["precisao_bala"] or 0) * g["tiros"] / 100) for g in pt
                      if g["grupo"] in ("1 bala (tap)", "2-3 balas"))
        pr_short = pct(h_short, t_short)
        if sh_long is not None and sh_long >= 20 and pr_long is not None and pr_short is not None and pr_long < pr_short - 5:
            dicas.append({
                "prioridade": 1, "titulo": "Rajadas longas desperdiçam balas",
                "evidencia": (f"{sh_long:.0f}% das suas balas de rifle saem em rajadas de 7+ tiros e acertam "
                              f"{pr_long:.0f}%; em rajadas de 1 a 3 tiros o aproveitamento é {pr_short:.0f}%."),
                "acao": ("Em média e longa distância use tap ou bursts de 2-3 balas e solte o gatilho para "
                         "resetar a mira. Spray completo só em curta distância."),
            })

    # --- AWP ---------------------------------------------------------------
    a = cl.get("awp")
    if a and a["rajadas"] >= 6:
        noscope = 100 - (a["pct_scoped"] or 0)
        em_mov = sum(g["rajadas"] for g in a["por_velocidade"] if g["grupo"] != "Parado")
        pct_mov = pct(em_mov, a["rajadas"])
        if noscope >= 20 or (pct_mov is not None and pct_mov >= 25):
            partes = []
            if noscope >= 20:
                partes.append(f"{noscope:.0f}% dos seus tiros de AWP foram sem mira")
            if pct_mov is not None and pct_mov >= 25:
                partes.append(f"{pct_mov:.0f}% começaram com você se mexendo")
            dicas.append({
                "prioridade": 2, "titulo": "AWP: tiros sem mira ou em movimento",
                "evidencia": "; ".join(partes) + f" (acerto geral: {a['pct_rajadas_com_acerto']:.0f}%).",
                "acao": ("A AWP perdoa pouco: pare, mire (scope) e só então atire. Depois de dar peek, "
                         "se não acertar, recue ou troque de ângulo em vez de repetir o duelo."),
            })
        elif a["pct_rajadas_com_acerto"] is not None and a["pct_rajadas_com_acerto"] >= 55:
            fortes.append(f"AWP: você acerta {a['pct_rajadas_com_acerto']:.0f}% dos tiros.")

    # --- escopeta ----------------------------------------------------------
    e = cl.get("escopeta")
    if e and e["acertos"] >= 6:
        dists = e["dist_acertos"]
        longe = pct(sum(1 for x in dists if x > DIST_ESCOPETA_LONGE), len(dists))
        if longe is not None and longe >= 40:
            dicas.append({
                "prioridade": 2, "titulo": "Escopeta: duelos longe demais",
                "evidencia": f"{longe:.0f}% dos seus acertos de escopeta foram a mais de {DIST_ESCOPETA_LONGE:.0f} m "
                             f"(média {e['dist_media_acertos_m']:.0f} m).",
                "acao": "Segure cantos fechados e deixe o inimigo chegar perto; mire no centro do corpo.",
            })

    # --- mortes -------------------------------------------------------------
    if m["total"] >= 8:
        if (m["pct_correndo"] or 0) >= 40:
            extra = ""
            if (m["pct_voce_correndo_inimigo_parado"] or 0) >= 20:
                extra = (f" Em {m['pct_voce_correndo_inimigo_parado']:.0f}% das mortes você corria "
                         "e o inimigo estava parado segurando o ângulo.")
            dicas.append({
                "prioridade": 1, "titulo": "Você morre correndo/pinando",
                "evidencia": f"{m['pct_correndo']:.0f}% das suas mortes aconteceram com você correndo.{extra}",
                "acao": ("Antes de dar peek, pergunte: tenho informação ou utilitária? Prefira segurar o ângulo, "
                         "dar peek curto (strafe) e deixar o inimigo vir até a sua mira."),
            })
        if (m["pct_sem_atirar"] or 0) >= 30:
            dicas.append({
                "prioridade": 1, "titulo": "Você morre sem chegar a atirar",
                "evidencia": f"{m['pct_sem_atirar']:.0f}% das suas mortes ocorreram sem nenhum tiro seu nos 1,5 s anteriores.",
                "acao": ("Isso costuma ser falta de informação ou susto: use som e utilitária antes de entrar, "
                         "pré-mire os ângulos comuns e mantenha a mira na altura da cabeça."),
            })
        if (m["pct_fora_visao"] or 0) >= 30:
            dicas.append({
                "prioridade": 2, "titulo": "Mortes por inimigos fora da sua mira",
                "evidencia": f"Em {m['pct_fora_visao']:.0f}% das mortes o inimigo estava a mais de "
                             f"{FORA_DE_VISAO_GRAUS:.0f}° da sua mira (flancos ou costas).",
                "acao": "Cheque flancos, jogue mais perto de um companheiro e use o som para antecipar de onde vem o inimigo.",
            })
        if (m["pct_cego"] or 0) >= 15:
            dicas.append({
                "prioridade": 3, "titulo": "Mortes enquanto cego",
                "evidencia": f"{m['pct_cego']:.0f}% das mortes aconteceram com você cego por flash.",
                "acao": "Vire de costas ou saia da linha de visão quando ouvir/ver o flash; evite segurar o ângulo já cego.",
            })

    ab = res["abertura"]
    if ab["ganhas"] + ab["perdidas"] >= 5:
        if ab["winrate"] is not None and ab["winrate"] < 45:
            dicas.append({
                "prioridade": 2, "titulo": "Duelos de abertura",
                "evidencia": f"Você venceu {ab['ganhas']} e perdeu {ab['perdidas']} duelos de abertura de round ({ab['winrate']:.0f}%).",
                "acao": "Evite ser o primeiro a se expor sem suporte: peça flash, jogue trade com um companheiro.",
            })
        elif ab["winrate"] is not None and ab["winrate"] >= 55:
            fortes.append(f"Duelos de abertura: {ab['ganhas']} vitórias e {ab['perdidas']} derrotas ({ab['winrate']:.0f}%).")

    c = res["constancia"]
    if c["pct_rounds_dano_baixo"] is not None and c["pct_rounds_dano_baixo"] >= 40:
        dicas.append({
            "prioridade": 3, "titulo": "Constância entre rounds",
            "evidencia": f"Em {c['pct_rounds_dano_baixo']:.0f}% dos rounds você causou menos de 40 de dano.",
            "acao": ("Reveja os rounds do gráfico abaixo: o que se repete nos rounds de baixo dano? "
                     "Se for tilt depois de morrer cedo, respire e reinicie o foco a cada round."),
        })

    g = res["geral"]
    if g["adr"] >= 80:
        fortes.append(f"ADR de {g['adr']:.0f}: você causa bastante dano por round.")
    if g["hs_pct"] is not None and g["hs_pct"] >= 50 and g["kills"] >= 5:
        fortes.append(f"{g['hs_pct']:.0f}% das suas kills foram headshot.")

    dicas.sort(key=lambda d: d["prioridade"])
    return dicas, fortes


# --------------------------------------------------------------------------
# Historico (evolucao entre partidas)
# --------------------------------------------------------------------------
def entrada_historico(res):
    cl = res["classes"].get("rifle")
    parado = correndo = spray = None
    if cl:
        parado = _get(cl["por_velocidade"], "Parado", "pct_com_acerto")
        correndo = _get(cl["por_velocidade"], "Correndo", "pct_com_acerto")
        spray = _get(cl["por_tamanho"], "7+ balas (spray)", "share_tiros")
    return {
        "demo": res["meta"]["demo"], "data": res["meta"]["analisado_em"], "mapa": res["meta"]["mapa"],
        "data_demo": res["meta"].get("data_demo"),
        "jogador": res["meta"]["jogador"], "steamid": res["meta"]["steamid"],
        "kd": res["geral"]["kd"], "adr": res["geral"]["adr"], "hs": res["geral"]["hs_pct"],
        "rifle_acerto_parado": parado, "rifle_acerto_correndo": correndo,
        "spray_pct": spray, "mortes_correndo": res["mortes"]["pct_correndo"],
        "morreu_sem_atirar": res["mortes"]["pct_sem_atirar"],
        "abertura_wr": res["abertura"]["winrate"],
    }


def atualizar_historico(caminho: Path, entrada: dict):
    caminho.parent.mkdir(parents=True, exist_ok=True)
    dados = []
    if caminho.exists():
        try:
            dados = json.loads(caminho.read_text(encoding="utf-8"))
        except Exception:
            dados = []
    dados = [d for d in dados if not (d.get("demo") == entrada["demo"] and d.get("steamid") == entrada["steamid"])]
    dados.append(entrada)
    caminho.write_text(json.dumps(dados, ensure_ascii=False, indent=2), encoding="utf-8")
    return [d for d in dados if d.get("steamid") == entrada["steamid"]]


# --------------------------------------------------------------------------
# Relatorio HTML
# --------------------------------------------------------------------------
CSS = """
:root{--bg:#0f1218;--card:#181d27;--card2:#202736;--tx:#e8ecf3;--mut:#93a0b5;--ac:#4cc2ff;--ok:#4cd68a;--ruim:#ff6b6b;--warn:#ffc857}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--tx);font:15px/1.5 system-ui,Segoe UI,Roboto,sans-serif}
.wrap{max-width:1000px;margin:0 auto;padding:24px 16px 60px}
h1{font-size:24px;margin:0 0 4px}h2{font-size:18px;margin:32px 0 12px;border-left:4px solid var(--ac);padding-left:10px}
.sub{color:var(--mut)}.cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(130px,1fr));gap:10px;margin:16px 0}
.card{background:var(--card);border-radius:10px;padding:12px 14px}.card b{display:block;font-size:24px}.card span{color:var(--mut);font-size:12px}
.dica{background:var(--card);border-radius:10px;padding:14px 16px;margin:10px 0;border-left:4px solid var(--warn)}
.dica.p1{border-color:var(--ruim)}.dica.p3{border-color:var(--ac)}.dica h3{margin:0 0 6px;font-size:16px}
.dica p{margin:4px 0}.ev{color:var(--mut)}.forte{background:var(--card);border-left:4px solid var(--ok);border-radius:10px;padding:8px 14px;margin:8px 0}
table{width:100%;border-collapse:collapse;background:var(--card);border-radius:10px;overflow:hidden;margin:8px 0}
th,td{padding:8px 10px;text-align:left;border-bottom:1px solid var(--card2)}th{color:var(--mut);font-weight:600;font-size:13px}
.bar{background:var(--card2);border-radius:4px;height:10px;min-width:60px}.bar i{display:block;height:10px;border-radius:4px;background:var(--ac)}
.nota{color:var(--mut);font-size:13px}svg{max-width:100%;height:auto;background:var(--card);border-radius:10px}
"""


def fmt(v, suf="%", casas=0):
    if v is None:
        return "—"
    return f"{v:.{casas}f}{suf}"


def barra(v):
    v = 0 if v is None else max(0, min(100, v))
    return f'<div class="bar"><i style="width:{v}%"></i></div>'


def tabela_velocidade(c):
    linhas = "".join(
        f"<tr><td>{html.escape(g['grupo'])}</td><td>{g['rajadas']}</td><td>{fmt(g['share'])}</td>"
        f"<td>{fmt(g['pct_com_acerto'])}</td><td>{barra(g['pct_com_acerto'])}</td></tr>"
        for g in c["por_velocidade"])
    return ("<table><tr><th>Ao iniciar a rajada</th><th>Rajadas</th><th>% das rajadas</th>"
            "<th>Rajadas com acerto</th><th></th></tr>" + linhas + "</table>")


def tabela_tamanho(c):
    linhas = "".join(
        f"<tr><td>{html.escape(g['grupo'])}</td><td>{g['rajadas']}</td><td>{fmt(g['share_tiros'])}</td>"
        f"<td>{fmt(g['precisao_bala'])}</td><td>{barra(g['precisao_bala'])}</td></tr>"
        for g in c["por_tamanho"])
    return ("<table><tr><th>Tamanho da rajada</th><th>Rajadas</th><th>% das balas</th>"
            "<th>Balas que acertam</th><th></th></tr>" + linhas + "</table>")


def grafico_rounds(rounds):
    if not rounds:
        return ""
    w, h, pad = 960, 220, 30
    mx = max(100, max(r["dano"] for r in rounds))
    bw = (w - 2 * pad) / len(rounds)
    partes = [f'<svg viewBox="0 0 {w} {h}" role="img" aria-label="Dano por round">']
    marcas = sorted({0, 50, 100, int(mx)} if mx > 100 else {0, 50, 100})
    for y in marcas:
        yy = h - pad - (h - 2 * pad) * y / mx
        partes.append(f'<line x1="{pad}" x2="{w - pad}" y1="{yy:.1f}" y2="{yy:.1f}" stroke="#2a3346"/>'
                      f'<text x="4" y="{yy + 4:.1f}" fill="#93a0b5" font-size="11">{y}</text>')
    for i, r in enumerate(rounds):
        bh = (h - 2 * pad) * r["dano"] / mx
        x = pad + i * bw + bw * 0.15
        cor = "#ff6b6b" if r["morreu"] else "#4cc2ff"
        partes.append(f'<rect x="{x:.1f}" y="{h - pad - bh:.1f}" width="{bw * 0.7:.1f}" height="{max(bh, 1):.1f}" fill="{cor}" rx="2"/>')
        if len(rounds) <= 40:
            partes.append(f'<text x="{x + bw * 0.35:.1f}" y="{h - 10}" fill="#93a0b5" font-size="10" text-anchor="middle">{r["rnd"]}</text>')
    partes.append("</svg>")
    return "".join(partes)


def tabela_historico(hist):
    if len(hist) < 2:
        return ""
    cab = ("<tr><th>Partida</th><th>Mapa</th><th>K/D</th><th>ADR</th><th>HS%</th>"
           "<th>Rifle parado</th><th>Rifle correndo</th><th>Spray 7+</th><th>Mortes correndo</th>"
           "<th>Morreu sem atirar</th></tr>")
    linhas = ""
    for d in hist[-10:]:
        linhas += (f"<tr><td>{html.escape(str(d['demo']))}</td><td>{html.escape(str(d['mapa']))}</td>"
                   f"<td>{d['kd']}</td><td>{fmt(d['adr'], '')}</td><td>{fmt(d['hs'])}</td>"
                   f"<td>{fmt(d['rifle_acerto_parado'])}</td><td>{fmt(d['rifle_acerto_correndo'])}</td>"
                   f"<td>{fmt(d['spray_pct'])}</td><td>{fmt(d['mortes_correndo'])}</td>"
                   f"<td>{fmt(d['morreu_sem_atirar'])}</td></tr>")
    return ("<h2>Evolução entre partidas</h2><table>" + cab + linhas + "</table>"
            '<p class="nota">Procure tendências: os números "Rifle correndo", "Mortes correndo" e '
            '"Morreu sem atirar" devem cair com o tempo; "Rifle parado" deve subir.</p>')


def secao_armas(res):
    lista = res.get("armas") or []
    rk = [a for a in _armas.ordenar(_armas.ranking(lista), "dano") if a["tiros"] or a["dano"] or a["kills"]]
    if not rk:
        return ""
    mx = max((a["dano"] for a in rk), default=0) or 1
    linhas = ""
    for a in rk:
        arma_fogo = a["tipo"] == "arma"
        linhas += (f"<tr><td>{html.escape(a['nome'])}</td><td>{a['dano']}</td>"
                   f"<td>{barra(100.0 * a['dano'] / mx)}</td>"
                   f"<td>{a['tiros'] if a['tipo'] != 'utilidade' else '—'}</td>"
                   f"<td>{fmt(a['precisao']) if arma_fogo else '—'}</td>"
                   f"<td>{fmt(a['dano_por_tiro'], '', 1) if arma_fogo else '—'}</td>"
                   f"<td>{a['kills']}</td><td>{fmt(a['hs_pct']) if arma_fogo else '—'}</td>"
                   f"<td>{fmt(a['dist_media_m'], ' m')}</td>"
                   f"<td>{fmt(a['nota'], '', 0) if a['nota'] is not None else '—'}</td></tr>")
    return ("<h2>Dano por arma</h2><table><tr><th>Arma</th><th>Dano</th><th></th><th>Tiros</th>"
            "<th>Tiros que acertam</th><th>Dano/tiro</th><th>Kills</th><th>HS dos acertos</th>"
            "<th>Dist. média</th><th>Nota</th></tr>" + linhas + "</table>"
            "<p class='nota'>Dano = vida tirada de inimigos (sem fogo amigo). \"Tiros que acertam\" conta cada tiro uma vez. "
            "A nota (0-100) compara as suas armas entre si e só existe para armas com 40+ tiros; "
            "ela fica confiável no Hub, somando várias partidas.</p>")


def gerar_html(res, dicas, fortes, hist):
    esc = html.escape
    g, m, meta = res["geral"], res["mortes"], res["meta"]
    p = []
    p.append(f"<!doctype html><html lang='pt-BR'><head><meta charset='utf-8'>"
             f"<meta name='viewport' content='width=device-width,initial-scale=1'>"
             f"<title>CS2 Coach - {esc(meta['jogador'])}</title><style>{CSS}</style></head><body><div class='wrap'>")
    p.append(f"<h1>Relatório de {esc(meta['jogador'])}</h1>"
             f"<div class='sub'>{esc(meta['mapa'])} · {esc(meta['demo'])} · {g['rounds']} rounds · analisado em {esc(meta['analisado_em'])}</div>")
    p.append("<div class='cards'>"
             f"<div class='card'><b>{g['kills']}/{g['mortes']}</b><span>Kills / Mortes (K/D {g['kd']})"
             + (f" · +{g['suicidios']} por queda/suicídio" if g.get("suicidios") else "") + "</span></div>"
             f"<div class='card'><b>{g['adr']:.0f}</b><span>ADR (dano por round)</span></div>"
             f"<div class='card'><b>{fmt(g['hs_pct'])}</b><span>Headshot nas kills</span></div>"
             f"<div class='card'><b>{fmt(res['abertura']['winrate'])}</b><span>Duelos de abertura "
             f"({res['abertura']['ganhas']}V/{res['abertura']['perdidas']}D)</span></div></div>")

    p.append("<h2>O que mais pesa na sua partida</h2>")
    if dicas:
        for d in dicas[:6]:
            p.append(f"<div class='dica p{d['prioridade']}'><h3>{esc(d['titulo'])}</h3>"
                     f"<p class='ev'>{esc(d['evidencia'])}</p><p><b>O que treinar:</b> {esc(d['acao'])}</p></div>")
    else:
        p.append("<div class='dica p3'><h3>Nenhum problema grande nesta partida</h3>"
                 "<p class='ev'>Com poucos dados os padrões ficam fracos; analise várias partidas para "
                 "ver o que se repete.</p></div>")
    for f in fortes:
        p.append(f"<div class='forte'>✔ {esc(f)}</div>")

    p.append(secao_armas(res))

    rifle = res["classes"].get("rifle")
    if rifle:
        p.append("<h2>Rifle</h2>"
                 f"<p>{rifle['tiros']} tiros em {rifle['rajadas']} rajadas · "
                 f"{fmt(rifle['precisao_bala'])} das balas acertam · "
                 f"{fmt(rifle['hs_pct_acertos'])} dos acertos na cabeça.</p>")
        p.append("<p class='nota'>Parado = abaixo de 40 u/s · Andando = 40 a 110 u/s · Correndo = acima de 110 u/s "
                 "(velocidade no instante do primeiro tiro).</p>")
        p.append(tabela_velocidade(rifle))
        p.append(tabela_tamanho(rifle))

    awp = res["classes"].get("awp")
    if awp:
        mov = sum(x["rajadas"] for x in awp["por_velocidade"] if x["grupo"] != "Parado")
        p.append("<h2>AWP</h2>"
                 f"<p>{awp['tiros']} tiros · {awp['acertos']} acertos · {awp['kills']} kills · "
                 f"{fmt(awp['pct_rajadas_com_acerto'])} dos tiros acertam · "
                 f"{fmt(awp['pct_scoped'])} com mira (scope) · {mov} tiros começaram com você se mexendo.</p>")

    esc_ = res["classes"].get("escopeta")
    if esc_:
        p.append("<h2>Escopeta</h2>"
                 f"<p>{esc_['tiros']} tiros · {esc_['acertos']} acertos · {esc_['kills']} kills · "
                 f"distância média dos acertos: {fmt(esc_['dist_media_acertos_m'], ' m')}.</p>")

    outras = [c for c in ("smg", "pistola", "sniper", "metralhadora") if c in res["classes"]]
    if outras:
        linhas = "".join(
            f"<tr><td>{NOME_CLASSE[c]}</td><td>{res['classes'][c]['tiros']}</td>"
            f"<td>{res['classes'][c]['acertos']}</td><td>{res['classes'][c]['kills']}</td>"
            f"<td>{fmt(res['classes'][c]['pct_rajadas_com_acerto'])}</td></tr>" for c in outras)
        p.append("<h2>Outras armas</h2><table><tr><th>Arma</th><th>Tiros</th><th>Acertos</th>"
                 "<th>Kills</th><th>Rajadas com acerto</th></tr>" + linhas + "</table>")

    p.append("<h2>Como você morre</h2>")
    if m["total"]:
        p.append("<table><tr><th>Situação na hora da morte</th><th>% das mortes</th><th></th></tr>"
                 f"<tr><td>Você estava correndo</td><td>{fmt(m['pct_correndo'])}</td><td>{barra(m['pct_correndo'])}</td></tr>"
                 f"<tr><td>Você correndo e o inimigo parado</td><td>{fmt(m['pct_voce_correndo_inimigo_parado'])}</td><td>{barra(m['pct_voce_correndo_inimigo_parado'])}</td></tr>"
                 f"<tr><td>Você não deu nenhum tiro (1,5 s antes)</td><td>{fmt(m['pct_sem_atirar'])}</td><td>{barra(m['pct_sem_atirar'])}</td></tr>"
                 f"<tr><td>Inimigo a mais de {FORA_DE_VISAO_GRAUS:.0f}° da sua mira</td><td>{fmt(m['pct_fora_visao'])}</td><td>{barra(m['pct_fora_visao'])}</td></tr>"
                 f"<tr><td>Você estava cego (flash)</td><td>{fmt(m['pct_cego'])}</td><td>{barra(m['pct_cego'])}</td></tr>"
                 f"<tr><td>Foi a morte de abertura do round</td><td>{fmt(m['pct_abertura'])}</td><td>{barra(m['pct_abertura'])}</td></tr>"
                 "</table>")
        p.append(f"<p class='nota'>Distância média das mortes: {fmt(m['dist_media_m'], ' m')}.</p>")
    else:
        p.append("<p class='nota'>Nenhuma morte registrada para esse jogador.</p>")

    p.append("<h2>Constância: dano por round</h2>")
    p.append(grafico_rounds(res["rounds"]))
    p.append("<p class='nota'>Azul = você sobreviveu ao round · Vermelho = você morreu. "
             "Dano limitado a 100 por inimigo.</p>")

    p.append(tabela_historico(hist))
    p.append("<p class='nota'>Todos os números vêm do arquivo de demo da partida (leitura oficial, sem tocar no jogo). "
             "Os limites de velocidade e distância são aproximações; use os dados como pista, não como sentença.</p>")
    p.append("</div></body></html>")
    return "".join(p)


# --------------------------------------------------------------------------
# Linha de comando
# --------------------------------------------------------------------------
def listar(demo: Demo):
    print(f"\nJogadores em {demo.caminho.name} ({demo.mapa}):")
    for i, r in enumerate(demo.jogadores.itertuples(), 1):
        print(f"  {i:2d}. {r.name}   (steamid {r.steamid})")


class JogadorNaoEncontrado(Exception):
    def __init__(self, jogadores, ambiguo=False):
        super().__init__("jogador ambíguo" if ambiguo else "jogador não encontrado")
        self.jogadores = jogadores      # lista de (steamid, nome)
        self.ambiguo = ambiguo


def steamid_padrao():
    """SteamID64 do usuario: o ajuste salvo no Hub ou, se nao houver, o detectado pela pasta userdata do Steam."""
    v = armazem.ajuste_get("meu_steamid")
    if v:
        return v
    try:
        import config_cs
        return config_cs.detectar_steamid()
    except Exception:
        return None


def guardar_partida(res, dicas, fortes, hash_arquivo_):
    """Salva no armazem local e devolve (id, html do relatorio com o historico atualizado)."""
    pid = armazem.salvar_partida(res, dicas, fortes, "", hash_arquivo_, entrada_historico(res))
    html_txt = gerar_html(res, dicas, fortes, armazem.historico(res["meta"]["steamid"]))
    armazem.atualizar_html(pid, html_txt)
    return pid, html_txt


def analisar_arquivo(caminho, steamid=None, nome=None, refazer=False):
    """
    Analisa uma demo e guarda no armazem. Devolve (id, res, dicas, fortes, ja_existia).
    Usado pelo Hub. Levanta JogadorNaoEncontrado se nao achar (ou se o nick for ambiguo).
    """
    caminho = Path(caminho)
    demo = Demo(caminho)
    alvo, sel = achar_jogador(demo, nome, steamid)
    if alvo is None:
        base = sel if len(sel) else demo.jogadores
        raise JogadorNaoEncontrado([(r.steamid, r.name) for r in base.itertuples()], ambiguo=len(sel) > 1)
    sid, nome_j = alvo
    hash_ = armazem.hash_arquivo(caminho)
    if not refazer and armazem.partida_existe(hash_, sid):
        return None, None, None, None, True
    res = analisar_jogador(demo, sid, nome_j)
    dicas, fortes = gerar_dicas(res)
    pid, _ = guardar_partida(res, dicas, fortes, hash_)
    return pid, res, dicas, fortes, False


def processar(caminho: Path, args, pasta_saida: Path):
    print(f"\nLendo {caminho.name} ...")
    demo = Demo(caminho)
    if args.listar:
        listar(demo)
        return True
    steam = args.steamid or (None if args.jogador else steamid_padrao())
    alvo, sel = achar_jogador(demo, args.jogador, steam)
    if alvo is None:
        if len(sel) > 1:
            print("Mais de um jogador combina com esse nome; use --steamid:")
            for r in sel.itertuples():
                print(f"  {r.name}  (steamid {r.steamid})")
            return False
        listar(demo)
        if sys.stdin.isatty() and not (args.jogador or args.steamid):
            try:
                n = int(input("\nNúmero do jogador a analisar: "))
                r = demo.jogadores.iloc[n - 1]
                alvo = (r["steamid"], r["name"])
            except Exception:
                return False
        else:
            print("\nJogador não encontrado. Use --jogador \"nick\" ou --steamid.")
            return False
    sid, nome = alvo
    hash_ = armazem.hash_arquivo(caminho)
    if not args.refazer and armazem.partida_existe(hash_, sid):
        print(f"  {nome}: esta demo já foi analisada e está no Hub (use --refazer para analisar de novo).")
        return True
    res = analisar_jogador(demo, sid, nome)
    dicas, fortes = gerar_dicas(res)
    pid, html_txt = guardar_partida(res, dicas, fortes, hash_)
    pasta_saida.mkdir(parents=True, exist_ok=True)
    seguro = "".join(ch if ch.isalnum() else "_" for ch in nome)[:30]
    saida = pasta_saida / f"relatorio_{caminho.stem}_{seguro}.html"
    saida.write_text(html_txt, encoding="utf-8")
    if args.json:
        (pasta_saida / f"relatorio_{caminho.stem}_{seguro}.json").write_text(
            json.dumps(res, ensure_ascii=False, indent=2, default=float), encoding="utf-8")
    g = res["geral"]
    print(f"  {nome}: {g['kills']}/{g['mortes']} (K/D {g['kd']}), ADR {g['adr']:.0f}")
    for d in dicas[:3]:
        print(f"  - {d['titulo']}: {d['evidencia']}")
    print(f"  Relatório salvo em: {saida}  (e guardado no Hub, partida nº {pid})")
    return True


def main():
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="replace")
        except Exception:
            pass
    ap = argparse.ArgumentParser(description="Analisa demos de CS2 e gera um relatorio com dicas.")
    ap.add_argument("demo", help="arquivo .dem ou pasta com varios .dem")
    ap.add_argument("--jogador", help="parte do seu nick no jogo")
    ap.add_argument("--steamid", help="seu SteamID64 (se o nick for ambíguo)")
    ap.add_argument("--listar", action="store_true", help="só lista os jogadores da demo")
    ap.add_argument("--saida", default=None, help="pasta dos relatorios (padrão: ./relatorios)")
    ap.add_argument("--json", action="store_true", help="também salva os números em JSON")
    ap.add_argument("--refazer", action="store_true", help="analisa de novo mesmo que a demo já esteja no Hub")
    args = ap.parse_args()

    base = Path(__file__).resolve().parent
    pasta_saida = Path(args.saida) if args.saida else base / "relatorios"

    alvo = Path(args.demo)
    demos = sorted(alvo.glob("*.dem")) if alvo.is_dir() else [alvo]
    if not demos or not all(d.exists() for d in demos):
        print("Nenhuma demo encontrada em:", alvo)
        sys.exit(1)
    ok = 0
    for d in demos:
        try:
            ok += 1 if processar(d, args, pasta_saida) else 0
        except Exception as exc:
            print(f"  Erro ao analisar {d.name}: {exc}")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
