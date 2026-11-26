# -*- coding: utf-8 -*-
"""
Metricas estilo Scope.gg, evolucao por blocos de partidas / por mapa e plano de treino semanal.

Tudo aqui trabalha sobre os resultados que o Hub ja guardou (nada de rede, nada de VAC).
As "notas" comparam cada partida com o SEU proprio historico (percentil), nao com uma tabela de rating:
nao existem referencias oficiais publicas e eu nao vou inventar numeros.
"""
from __future__ import annotations

import json
import statistics
from datetime import date, datetime, timedelta

TAMANHO_BLOCO = 15


def _n(v):
    return v if isinstance(v, (int, float)) and v == v else None


def _grupo(res, classe, lista, nome, chave):
    c = (res.get("classes") or {}).get(classe) or {}
    for g in c.get(lista, []) or []:
        if g.get("grupo") == nome:
            return _n(g.get(chave))
    return None


# chave -> (rotulo, sufixo, "alto"|"baixo" = o que e melhor, funcao(res) -> valor|None)
METRICAS = {
    "kd": ("K/D", "", "alto", lambda r: _n((r.get("geral") or {}).get("kd"))),
    "adr": ("Dano por round (ADR)", "", "alto", lambda r: _n((r.get("geral") or {}).get("adr"))),
    "hs_kills": ("Headshot nas kills", "%", "alto", lambda r: _n((r.get("geral") or {}).get("hs_pct"))),
    "hs_acertos": ("Headshot por acerto", "%", "alto", lambda r: _n((r.get("scope") or {}).get("hs_acertos_pct"))
                   if (r.get("scope") or {}).get("hs_acertos_pct") is not None else
                   _n(((r.get("classes") or {}).get("rifle") or {}).get("hs_pct_acertos"))),
    "ttk": ("Tempo para matar (rifle)", " ms", "baixo", lambda r: _n((r.get("scope") or {}).get("ttk_rifle_ms"))),
    "precisao_rifle": ("Precisão do rifle", "%", "alto",
                       lambda r: _n(((r.get("classes") or {}).get("rifle") or {}).get("precisao_bala"))),
    "abertura": ("Duelos de abertura ganhos", "%", "alto", lambda r: _n((r.get("abertura") or {}).get("winrate"))),
    "kills_parado": ("Kills atirando parado", "%", "alto", lambda r: _n((r.get("scope") or {}).get("kills_parado_pct"))),
    "rifle_correndo": ("Rajadas de rifle correndo", "%", "baixo",
                       lambda r: _grupo(r, "rifle", "por_velocidade", "Correndo", "share")),
    "rifle_parado": ("Rifle: acerta parado", "%", "alto",
                     lambda r: _grupo(r, "rifle", "por_velocidade", "Parado", "pct_com_acerto")),
    "spray_longo": ("Tiros em spray longo (rifle)", "%", "baixo",
                    lambda r: _grupo(r, "rifle", "por_tamanho", "7+ balas (spray)", "share_tiros")),
    "sem_atirar": ("Mortes sem atirar", "%", "baixo", lambda r: _n((r.get("mortes") or {}).get("pct_sem_atirar"))),
    "fora_visao": ("Mortes fora da sua visão", "%", "baixo", lambda r: _n((r.get("mortes") or {}).get("pct_fora_visao"))),
    "cego": ("Mortes cego por flash", "%", "baixo", lambda r: _n((r.get("mortes") or {}).get("pct_cego"))),
}
# metricas mostradas no bloco "estilo Scope" da partida
SCOPE_LISTA = ["hs_acertos", "ttk", "precisao_rifle", "kills_parado", "rifle_parado", "rifle_correndo", "abertura", "adr"]


def valor(res, chave):
    try:
        return METRICAS[chave][3](res)
    except Exception:
        return None


def media_metrica(resultados, chave):
    vs = [v for v in (valor(r["res"], chave) for r in resultados) if v is not None]
    return round(statistics.fmean(vs), 1) if vs else None


def percentil(v, historico, melhor):
    """Quanto da sua historia essa partida supera (0-100), respeitando o sentido (mais baixo pode ser melhor)."""
    h = [x for x in historico if x is not None]
    if v is None or len(h) < 5:
        return None
    if melhor == "alto":
        pior = sum(1 for x in h if x < v) + 0.5 * sum(1 for x in h if x == v)
    else:
        pior = sum(1 for x in h if x > v) + 0.5 * sum(1 for x in h if x == v)
    return round(100 * pior / len(h))


def notas_partida(todos, pid):
    """Para cada metrica: valor desta partida, media das suas partidas e nota (percentil no seu historico)."""
    alvo = next((r for r in todos if r["id"] == pid), None)
    if not alvo:
        return []
    out = []
    for chave in SCOPE_LISTA:
        rot, suf, melhor, _ = METRICAS[chave]
        v = valor(alvo["res"], chave)
        hist = [valor(r["res"], chave) for r in todos if r["id"] != pid]
        h = [x for x in hist if x is not None]
        out.append({"chave": chave, "rotulo": rot, "sufixo": suf, "melhor": melhor, "valor": v,
                    "media": round(statistics.fmean(h), 1) if h else None, "n": len(h),
                    "nota": percentil(v, hist, melhor)})
    return out


# ---------------------------------------------------------------- evolucao
def blocos(todos, tamanho=TAMANHO_BLOCO):
    """Divide em blocos de `tamanho` partidas, do mais antigo ao mais novo (o ultimo pode ficar incompleto)."""
    out = []
    for i in range(0, len(todos), tamanho):
        grupo = todos[i:i + tamanho]
        kills = sum((r["res"].get("geral") or {}).get("kills", 0) for r in grupo)
        mortes = sum((r["res"].get("geral") or {}).get("mortes", 0) for r in grupo)
        ab_g = sum((r["res"].get("abertura") or {}).get("ganhas", 0) for r in grupo)
        ab_p = sum((r["res"].get("abertura") or {}).get("perdidas", 0) for r in grupo)
        d = {"de": i + 1, "ate": i + len(grupo), "n": len(grupo), "completo": len(grupo) == tamanho,
             "inicio": grupo[0]["data_demo"], "fim": grupo[-1]["data_demo"],
             "kd": round(kills / mortes, 2) if mortes else float(kills)}
        for chave in METRICAS:
            if chave in ("kd", "abertura"):
                continue
            d[chave] = media_metrica(grupo, chave)
        d["abertura"] = round(100 * ab_g / (ab_g + ab_p)) if (ab_g + ab_p) else None
        out.append(d)
    return out


def por_mapa(todos):
    mapas = {}
    for r in todos:
        mapas.setdefault(r["mapa"] or "?", []).append(r)
    out = []
    for nome, grupo in mapas.items():
        kills = sum((r["res"].get("geral") or {}).get("kills", 0) for r in grupo)
        mortes = sum((r["res"].get("geral") or {}).get("mortes", 0) for r in grupo)
        ab_g = sum((r["res"].get("abertura") or {}).get("ganhas", 0) for r in grupo)
        ab_p = sum((r["res"].get("abertura") or {}).get("perdidas", 0) for r in grupo)
        out.append({"mapa": nome, "partidas": len(grupo), "kd": round(kills / mortes, 2) if mortes else float(kills),
                    "adr": media_metrica(grupo, "adr"), "hs": media_metrica(grupo, "hs_kills"),
                    "abertura": round(100 * ab_g / (ab_g + ab_p)) if (ab_g + ab_p) else None,
                    "sem_atirar": media_metrica(grupo, "sem_atirar")})
    out.sort(key=lambda x: -x["partidas"])
    return out


# ---------------------------------------------------------------- plano de treino
# chave -> limite a partir do qual vira ponto fraco (mesmos limites das dicas das partidas) e o treino indicado
FRACOS = {
    "rifle_parado": {
        "limite": 60, "titulo": "Acertar o rifle parado", "mapa": "Aim Botz (Oficina)", "minutos": 10,
        "como": "Bots parados, só rifle: pare (counter-strafe), mire na cabeça, 1 a 3 balas por alvo. Sem correr atirando.",
        "motivo": "Você acerta ao menos uma bala em {v:.0f}% das rajadas de rifle mesmo parado (meta: {lim:.0f}%+).",
    },
    "rifle_correndo": {
        "limite": 15, "titulo": "Frear antes de atirar", "mapa": "Aim Botz ou Deathmatch", "minutos": 10,
        "como": "Regra única: tocar a tecla contrária (A/D), esperar parar e só então clicar. Dá 5 minutos de bots e 5 de DM.",
        "motivo": "{v:.0f}% das suas rajadas de rifle começam com você correndo (ideal: abaixo de {lim:.0f}%).",
    },
    "spray_longo": {
        "limite": 20, "titulo": "Controle de spray e bursts", "mapa": "Recoil Master (Oficina)", "minutos": 10,
        "como": "Treine o padrão da AK e da M4 em rajadas de 3 a 5 balas; só use spray longo de perto.",
        "motivo": "{v:.0f}% dos seus tiros de rifle são spray longo (ideal: abaixo de {lim:.0f}%).",
    },
    "sem_atirar": {
        "limite": 30, "titulo": "Pré-mira e ângulos", "mapa": "5E Prac / Yprac (Oficina)", "minutos": 10,
        "como": "Passe pelos ângulos do seu mapa com a mira já na altura da cabeça e dê prefire nos pontos comuns.",
        "motivo": "{v:.0f}% das suas mortes acontecem sem você dar um tiro (ideal: abaixo de {lim:.0f}%).",
    },
    "fora_visao": {
        "limite": 40, "titulo": "Informação antes de peekar", "mapa": "Yprac (Oficina)", "minutos": 8,
        "como": "Treine usar flash/som para tirar a surpresa e limpar um ângulo por vez em vez de entrar de uma vez.",
        "motivo": "{v:.0f}% das suas mortes vêm de inimigos fora do seu campo de visão (ideal: abaixo de {lim:.0f}%).",
    },
    "cego": {
        "limite": 12, "titulo": "Flash: virar de costas e jogar a sua", "mapa": "Yprac (Oficina)", "minutos": 8,
        "como": "Treine as flashes do seu mapa e o hábito de virar para fora quando o inimigo tem flash.",
        "motivo": "{v:.0f}% das suas mortes acontecem enquanto você está cego (ideal: abaixo de {lim:.0f}%).",
    },
    "abertura": {
        "limite": 42, "titulo": "Duelos de abertura", "mapa": "AIM Map 1v1 / 5E Prac", "minutos": 10,
        "como": "Duelos 1v1 de entrada e prefire de entrada. Quando abrir sem suporte, troque o ângulo em vez de insistir.",
        "motivo": "Você ganha só {v:.0f}% dos duelos de abertura (meta: {lim:.0f}%+).",
        "baixo_ruim": True,
    },
}
ALTO_E_BOM = {"rifle_parado", "abertura"}
AQUECIMENTO = {"id": "aquecimento", "titulo": "Aquecimento antes de jogar", "mapa": "Aim Botz (Oficina)", "minutos": 5,
               "como": "5 minutos: flicks e tracking nos bots com o seu rifle, sem pressa. Foco em mira na altura da cabeça.",
               "motivo": "Mira e mão ficam mais firmes quando você esquenta antes da primeira partida.", "metrica": None}


def _gravidade(chave, v):
    lim = FRACOS[chave]["limite"]
    if v is None:
        return 0.0
    if chave in ALTO_E_BOM:
        return (lim / v) if v > 0 else 3.0
    return (v / lim) if lim else 0.0


def gerar_plano(todos, ultimas=10, hoje=None):
    """Monta o plano da semana a partir das ultimas partidas. Devolve um dicionario pronto para guardar."""
    hoje = hoje or date.today()
    recentes = todos[-ultimas:]
    itens = []
    pontos = []
    for chave, cfg in FRACOS.items():
        vals = [valor(r["res"], chave) for r in recentes]
        vals = [x for x in vals if x is not None]
        if len(vals) < 3:
            continue
        v = statistics.fmean(vals)
        g = _gravidade(chave, v)
        if g > 1.0:
            pontos.append((g, chave, v))
    pontos.sort(reverse=True)
    for rank, (g, chave, v) in enumerate(pontos[:3]):
        cfg = FRACOS[chave]
        lim = cfg["limite"]
        alvo = min(lim, v * 1.15) if chave in ALTO_E_BOM else max(lim, v * 0.85)
        itens.append({"id": chave, "titulo": cfg["titulo"], "mapa": cfg["mapa"], "minutos": cfg["minutos"],
                      "como": cfg["como"], "motivo": cfg["motivo"].format(v=v, lim=lim), "metrica": chave,
                      "baseline": round(v, 1), "alvo": round(alvo, 1),
                      "dias": [(rank * 2 + k) % 7 for k in (0, 2, 4)]})
    itens.insert(0, dict(AQUECIMENTO, dias=list(range(7))))
    return {"criado": hoje.isoformat(), "valido_ate": (hoje + timedelta(days=7)).isoformat(),
            "n_partidas_base": len(recentes), "itens": itens, "feito": {}}


def avaliar_plano(plano, todos):
    """Compara cada ponto trabalhado com as partidas jogadas DEPOIS que o plano foi criado."""
    depois = [r for r in todos if str(r["data_demo"] or "")[:10] >= plano["criado"]]
    out = []
    for it in plano["itens"]:
        ch = it.get("metrica")
        if not ch:
            continue
        atual = media_metrica(depois, ch) if len(depois) >= 2 else None
        melhor = METRICAS[ch][2]
        mudou = None if atual is None else round(atual - it["baseline"], 1)
        bom = None if mudou is None or mudou == 0 else ((mudou > 0) == (melhor == "alto"))
        out.append({"id": it["id"], "baseline": it["baseline"], "alvo": it["alvo"], "atual": atual,
                    "mudou": mudou, "melhorou": bom, "n": len(depois),
                    "bateu_alvo": None if atual is None else
                    (atual >= it["alvo"] if melhor == "alto" else atual <= it["alvo"])})
    return out


def itens_de_hoje(plano, hoje=None):
    hoje = hoje or date.today()
    return [it for it in plano["itens"] if hoje.weekday() in it.get("dias", [])]
