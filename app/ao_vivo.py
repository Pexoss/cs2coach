#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
CS2 Coach - painel ao vivo (Game State Integration)

O CS2 tem um recurso oficial chamado Game State Integration (GSI): o proprio
jogo envia, a cada instante, vida, municao, kills, flash, dinheiro etc. para um
programa local. Isso NAO le a memoria do jogo e NAO afeta o VAC.

Este programa recebe esses dados e mostra, numa pagina do navegador, avisos
em tempo real e um resumo da sessao:
  - rajadas longas de rifle/SMG
  - mortes sem ter chegado a atirar (susto / falta de informacao)
  - mortes cego por flash e mortes muito cedo no round
  - sequencia de mortes (tilt) e rounds solidos

Uso:
  1) python ao_vivo.py --instalar      (cria o arquivo de configuracao do jogo)
  2) python ao_vivo.py                 (deixa rodando; abra http://127.0.0.1:3000)
  3) abra o CS2 e jogue (reinicie o jogo se ele ja estava aberto)

O painel detecta sozinho o modo (Competitivo/Premier, Wingman, Casual, Demolicao,
Mata-mata, Corrida Armamentista, Zona de Perigo e mapas de treino da oficina) e
adapta os cartoes, tabelas e dicas. Se a deteccao errar, escolha o modo no seletor
da pagina.

Limites do GSI: durante a partida ele nao informa a posicao dos inimigos nem a
sua mira. Para analise de movimento/mira/distancia use o analisar_demo.py
depois da partida.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import threading
import time
from collections import deque
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

BASE = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE))
import caminhos  # noqa: E402

PASTA_SESSOES = caminhos.pasta_dados() / "sessoes"

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
AUTOMATICAS = {"rifle", "smg", "metralhadora"}
GAP_RAJADA_S = 0.35          # pausa que encerra uma rajada
JANELA_SEM_ATIRAR_S = 1.5    # janela para "morreu sem atirar"
MORTE_CEDO_S = 20.0          # morte nos primeiros segundos do round
COOLDOWN_ALERTA_S = 20.0


def norm_arma(nome) -> str:
    return str(nome or "").lower().replace("weapon_", "").strip()


def categoria_rajada(n: int) -> str:
    if n <= 1:
        return "tap"
    if n <= 3:
        return "curta"
    if n <= 6:
        return "media"
    return "longa"


# --------------------------------------------------------------------------
# Modos de jogo (detectados sozinhos a partir dos dados do jogo)
# --------------------------------------------------------------------------
# id: (nome exibido, layout)   layout: "rounds" | "vidas" | "treino"
MODOS = {
    "competitivo": ("Competitivo / Premier", "rounds"),
    "wingman": ("Braço Direito (Wingman 2v2)", "rounds"),
    "casual": ("Casual", "rounds"),
    "demolicao": ("Demolição", "rounds"),
    "dm": ("Mata-mata (DM)", "vidas"),
    "arms": ("Corrida Armamentista", "vidas"),
    "perigo": ("Zona de Perigo", "vidas"),
    "treino": ("Treino / mapa da oficina", "treino"),
}
MODO_POR_GSI = {
    "competitive": "competitivo", "scrimcomp5v5": "competitivo", "scrimcomp2v2": "wingman",
    "casual": "casual", "deathmatch": "dm", "gungameprogressive": "arms",
    "gungametrbomb": "demolicao", "survival": "perigo",
    "training": "treino", "cooperative": "treino", "coopmission": "treino",
}
PALAVRAS_TREINO = ("aim", "recoil", "training", "treino", "yprac", "prefire", "crashz",
                   "1v1", "kz_", "surf_", "bhop", "botz", "flick", "tracking", "spray")
MORTE_CEDO_VIDA_S = 8.0      # DM/Arms Race: morte logo depois de nascer


def detectar_modo(mapa: dict, rnd: dict) -> str:
    gm = str(mapa.get("mode") or "").lower()
    nome = str(mapa.get("name") or "").lower()
    ultimo = nome.split("/")[-1]
    oficina = "workshop" in nome or any(p in ultimo for p in PALAVRAS_TREINO)
    if gm in ("competitive", "scrimcomp5v5", "scrimcomp2v2") and "workshop" not in nome:
        return MODO_POR_GSI[gm]
    if oficina:
        return "treino"
    if gm in MODO_POR_GSI:
        return MODO_POR_GSI[gm]
    if rnd.get("phase") and mapa.get("round") is not None:
        return "casual"
    return "treino"


# --------------------------------------------------------------------------
# Estado e logica do coach
# --------------------------------------------------------------------------
class Coach:
    def __init__(self, token: str):
        self.token = token
        self.lock = threading.RLock()
        self.alertas = deque(maxlen=60)
        self._ultimo_alerta = {}
        self.sessao_inicio = datetime.now()
        self.pacotes = 0
        self.recusados = 0
        self.ultimo_raw = None
        self.iniciado_em = time.time()
        self.modo_forcado = None
        self.modo_det = None
        self._zerar_partida()

    @property
    def modo(self) -> str:
        return self.modo_forcado or self.modo_det or "casual"

    @property
    def layout(self) -> str:
        return MODOS[self.modo][1]

    # ---- estado ----------------------------------------------------------
    def _zerar_partida(self):
        self.mapa = None
        self.fase_mapa = None
        self.fase_round = None
        self.round_num = None
        self.placar = None
        self.nome = None
        self.steamid = None
        self.ultimo_pacote = None
        self.kills = 0
        self.mortes = 0
        self.assistencias = 0
        self.hs_kills = 0
        self.rajadas_cont = {}   # classe -> {tap, curta, media, longa, tiros}
        self.por_arma = {}       # arma -> {tiros, kills}
        self.rounds = []         # resumo por round (modos com rounds)
        self.vidas = []          # resumo por vida (DM / Arms Race)
        self.minutos = {}        # minuto -> {kills, tiros, longas} (DM / treino)
        self.t_jogo0 = None
        self.tiros_total = 0
        self.n_vida = 0
        self.mortes_seguidas = 0
        self.mortes_sem_atirar = 0
        self.mortes_cego = 0
        self.mortes_cedo = 0
        self.eventos = []
        self._zerar_round()
        self._ultima_arma = None
        self._ultima_municao = None
        self._vida_ant = None
        self._flash_ant = 0
        self._kills_ant = None
        self._salvo = False
        self._tiros_recentes = deque(maxlen=200)  # (t_monotonico, n)
        self._rajada = None

    def _zerar_round(self):
        """Zera os contadores do segmento atual (round ou vida)."""
        self.r_kills = 0
        self.r_tiros = 0
        self.r_rajadas_longas = 0
        self.r_rajadas = 0
        self.r_morreu = False
        self.r_dano = None
        self.t_vida = None
        self._r_finalizado = False
        self._hs_round_ant = 0

    def definir_modo(self, novo: str) -> bool:
        with self.lock:
            if novo == "auto":
                alvo = None
            elif novo in MODOS:
                alvo = novo
            else:
                return False
            if alvo != self.modo_forcado:
                self._fechar_rajada()
                self._encerrar_partida()
                self.modo_forcado = alvo
                self.alertas.clear()
                ultimo, mapa, nome = self.ultimo_pacote, self.mapa, self.nome
                self._zerar_partida()
                self.ultimo_pacote, self.mapa, self.nome = ultimo, mapa, nome  # segue conectado
            return True

    # ---- alertas ---------------------------------------------------------
    def alerta(self, tipo, texto, sev="info", cooldown=COOLDOWN_ALERTA_S):
        agora = time.monotonic()
        if cooldown and agora - self._ultimo_alerta.get(tipo, -1e9) < cooldown:
            return
        self._ultimo_alerta[tipo] = agora
        self.alertas.appendleft({"hora": datetime.now().strftime("%H:%M:%S"), "sev": sev, "texto": texto,
                                 "round": self.round_num if self.layout == "rounds" else None})
        self._log("alerta", {"tipo": tipo, "texto": texto, "sev": sev})

    def _log(self, tipo, dados):
        self.eventos.append({"t": datetime.now().isoformat(timespec="seconds"), "tipo": tipo,
                             "round": self.round_num, "modo": self.modo, **dados})

    def _bucket(self):
        agora = time.monotonic()
        if self.t_jogo0 is None:
            self.t_jogo0 = agora
        i = int((agora - self.t_jogo0) // 60)
        return self.minutos.setdefault(i, {"kills": 0, "tiros": 0, "longas": 0})

    # ---- rajadas ---------------------------------------------------------
    def _registrar_tiros(self, arma, n, agora):
        classe = ARMA_PARA_CLASSE.get(arma)
        if classe is None:
            return
        self._tiros_recentes.append((agora, n))
        self.r_tiros += n
        self.tiros_total += n
        self._bucket()["tiros"] += n
        self.por_arma.setdefault(arma, {"tiros": 0, "kills": 0})["tiros"] += n
        gap = GAP_RAJADA_S if classe in AUTOMATICAS else 0.0
        b = self._rajada
        if b and b["arma"] == arma and agora - b["t_ult"] <= gap:
            b["n"] += n
            b["t_ult"] = agora
        else:
            self._fechar_rajada()
            self._rajada = {"arma": arma, "classe": classe, "n": n, "t_ult": agora}

    def _fechar_rajada(self):
        b = self._rajada
        if not b:
            return
        self._rajada = None
        classe, n = b["classe"], b["n"]
        c = self.rajadas_cont.setdefault(
            classe, {"tap": 0, "curta": 0, "media": 0, "longa": 0, "tiros": 0, "tiros_longos": 0})
        c[categoria_rajada(n)] += 1
        c["tiros"] += n
        if n >= 7:
            c["tiros_longos"] += n
        self.r_rajadas += 1
        if classe in AUTOMATICAS and n >= 7:
            self.r_rajadas_longas += 1
            self._bucket()["longas"] += 1
            if self.layout != "treino":  # no treino o spray costuma ser proposital
                self.alerta("spray", f"Rajada de {n} balas ({b['arma']}). Em média/longa distância prefira bursts de 2-3 balas.",
                            "aviso")

    def fechar_rajadas_inativas(self):
        with self.lock:
            b = self._rajada
            if b and time.monotonic() - b["t_ult"] > GAP_RAJADA_S + 0.05:
                self._fechar_rajada()

    def registrar_pacote(self, p: dict):
        with self.lock:
            self.pacotes += 1
            self.ultimo_raw = p
            if self.pacotes == 1:
                print("✔ Primeiro pacote do jogo recebido! A integração está funcionando.")

    def _em_jogo(self, fase_round) -> bool:
        if self.fase_mapa == "gameover":
            return False
        lay = self.layout
        if lay == "rounds":
            return self.fase_mapa == "live" and fase_round == "live"
        if lay == "vidas":
            return self.fase_mapa != "warmup"
        return True

    # ---- processamento de um pacote GSI -------------------------------
    def processar(self, p: dict):
        with self.lock:
            agora = time.monotonic()
            prov = p.get("provider", {}) or {}
            mapa = p.get("map", {}) or {}
            rnd = p.get("round", {}) or {}
            jog = p.get("player", {}) or {}

            self.modo_det = detectar_modo(mapa, rnd)
            lay = self.layout
            nome_mapa = mapa.get("name")
            num_round = mapa.get("round")
            # nova partida: mapa mudou ou (modos com round) round voltou para tras
            if (self.mapa and nome_mapa and nome_mapa != self.mapa) or (
                    lay == "rounds" and self.round_num is not None and num_round is not None
                    and num_round < self.round_num):
                self._encerrar_partida()
                self._zerar_partida()

            self.ultimo_pacote = time.time()
            self.mapa = nome_mapa or self.mapa
            self.fase_mapa = mapa.get("phase", self.fase_mapa)
            if lay == "rounds" and mapa:
                self.placar = (f"CT {mapa.get('team_ct', {}).get('score', 0)} x "
                               f"{mapa.get('team_t', {}).get('score', 0)} T")

            meu = prov.get("steamid")
            if jog.get("steamid") and meu and jog.get("steamid") != meu:
                # o jogo passou a mostrar outro jogador: se voce estava vivo, voce morreu
                if (lay != "treino" and self._em_jogo(rnd.get("phase", self.fase_round))
                        and self._vida_ant and self._vida_ant > 0):
                    self._vida_ant = 0
                    self._morte(agora)
                return
            if jog.get("name"):
                self.nome = jog["name"]
            self.steamid = meu or self.steamid

            nova_fase = rnd.get("phase", self.fase_round)
            if lay == "rounds":
                if num_round is not None and num_round != self.round_num:
                    if self.round_num is not None:
                        self._finalizar_round()
                    self.round_num = num_round
                    self._zerar_round()
                    self._ultima_arma = None
                    self._ultima_municao = None
                if nova_fase != self.fase_round:
                    if nova_fase == "live":
                        self._tiros_recentes.clear()
                        self._rajada = None
                        self.t_vida = agora
                        self._ultima_arma, self._ultima_municao = None, None
                        self._vida_ant = None
                    if nova_fase == "over":
                        self._fechar_rajada()
                        self._finalizar_round()
                    self.fase_round = nova_fase
            else:
                self.fase_round = nova_fase
            if self.fase_mapa == "gameover":
                self._fechar_rajada()
                self._encerrar_partida()
                return

            estado = jog.get("state", {}) or {}
            ms = jog.get("match_stats", {}) or {}
            vida = estado.get("health")
            ativo = self._em_jogo(self.fase_round)
            vivo = vida is None or vida > 0
            if ativo and vivo and self.t_jogo0 is None:
                self.t_jogo0 = agora
            if (lay == "vidas" and ativo and vida is not None and vida > 0
                    and (self._vida_ant is None or self._vida_ant == 0)):
                self._iniciar_vida(agora)   # renasceu: zera os contadores antes de ler a arma

            # --- arma ativa e tiros (variacao da municao no pente) -----------
            ativa, municao = None, None
            for w in (jog.get("weapons", {}) or {}).values():
                if w.get("state") == "active":
                    ativa = norm_arma(w.get("name"))
                    municao = w.get("ammo_clip")
                    break
            if ativo and vivo and ativa is not None and municao is not None:
                if self._ultima_arma == ativa and self._ultima_municao is not None:
                    delta = self._ultima_municao - municao
                    if 0 < delta <= 12:
                        self._registrar_tiros(ativa, delta, agora)
            if ativa is not None:
                if ativa != self._ultima_arma:
                    self._fechar_rajada()
                self._ultima_arma, self._ultima_municao = ativa, municao

            # --- kills ---------------------------------------------------------
            k = ms.get("kills")
            if k is not None:
                if self._kills_ant is not None and k > self._kills_ant:
                    novas = k - self._kills_ant
                    self.r_kills += novas
                    self._bucket()["kills"] += novas
                    if self._ultima_arma:
                        self.por_arma.setdefault(self._ultima_arma, {"tiros": 0, "kills": 0})["kills"] += novas
                    hs_round = estado.get("round_killhs", 0) or 0
                    if hs_round > self._hs_round_ant:
                        self.hs_kills += hs_round - self._hs_round_ant
                    self._log("kill", {"arma": self._ultima_arma})
                self._kills_ant = k
                self.kills = k
            self._hs_round_ant = estado.get("round_killhs", 0) or 0
            self.mortes = ms.get("deaths", self.mortes)
            self.assistencias = ms.get("assists", self.assistencias)
            if "round_totaldmg" in estado:
                self.r_dano = estado.get("round_totaldmg")

            # --- morte / renascimento ---------------------------------------------
            flash = estado.get("flashed", 0) or 0
            if vida is not None:
                if ativo and self._vida_ant is not None and self._vida_ant > 0 and vida == 0:
                    self._morte(agora)
                self._vida_ant = vida
                self._flash_ant = flash if vida > 0 else self._flash_ant

    # ---- eventos de alto nivel -------------------------------------------
    def _iniciar_vida(self, agora):
        self._zerar_round()
        self.t_vida = agora
        self.n_vida += 1
        self._tiros_recentes.clear()
        self._rajada = None
        self._ultima_arma, self._ultima_municao = None, None

    def _morte(self, agora):
        if self.layout == "treino":
            return
        self.r_morreu = True
        tiros = sum(n for t, n in self._tiros_recentes if agora - t <= JANELA_SEM_ATIRAR_S)
        self._fechar_rajada()
        desde = (agora - self.t_vida) if self.t_vida else None
        rounds = self.layout == "rounds"
        limite = MORTE_CEDO_S if rounds else MORTE_CEDO_VIDA_S
        self._log("morte", {"tiros_antes": tiros, "flash": self._flash_ant,
                            "desde_inicio_s": round(desde, 1) if desde is not None else None})
        if tiros == 0:
            self.mortes_sem_atirar += 1
            self.alerta("sem_atirar", "Você morreu sem atirar. Antes de entrar, use som/utilitária e pré-mire o ângulo."
                        if rounds else "Você morreu sem atirar. Olhe para onde o inimigo pode vir e mantenha a mira na altura da cabeça.",
                        "ruim", 0)
        if self._flash_ant and self._flash_ant > 50:
            self.mortes_cego += 1
            self.alerta("cego", "Morte enquanto cego por flash: vire de costas ou saia da linha de visão.", "aviso", 0)
        if desde is not None and desde < limite:
            self.mortes_cedo += 1
            if rounds:
                self.alerta("cedo", f"Morte aos {desde:.0f}s do round. Evite ser o primeiro a se expor sem suporte.",
                            "aviso", 0)
            else:
                self.alerta("cedo", f"Morte {desde:.0f}s depois de nascer. Olhe os spawns e use o som antes de sair correndo.",
                            "aviso", 0)
        if not rounds:
            self._finalizar_vida(desde)

    def _finalizar_vida(self, desde):
        self.vidas.append({"vida": self.n_vida, "kills": self.r_kills,
                           "duracao": round(desde) if desde is not None else None,
                           "tiros": self.r_tiros, "rajadas_longas": self.r_rajadas_longas})
        if self.r_kills == 0:
            self.mortes_seguidas += 1
        else:
            self.mortes_seguidas = 0
        if self.mortes_seguidas >= 4:
            self.alerta("tilt", f"{self.mortes_seguidas} vidas seguidas sem kill. Respire, jogue mais devagar "
                                "e cheque os ângulos antes de peekar.", "ruim", 30)
        elif self.r_kills >= 4:
            self.alerta("vida_boa", f"Vida forte: {self.r_kills} kills sem morrer.", "bom", 10)

    def _finalizar_round(self):
        if self._r_finalizado or self.round_num is None:
            return
        self._r_finalizado = True
        if self.r_morreu:
            self.mortes_seguidas += 1
        else:
            self.mortes_seguidas = 0
        self.rounds.append({"round": self.round_num, "kills": self.r_kills, "morreu": self.r_morreu,
                            "tiros": self.r_tiros, "rajadas": self.r_rajadas,
                            "rajadas_longas": self.r_rajadas_longas, "dano": self.r_dano})
        if self.mortes_seguidas >= 3:
            self.alerta("tilt", f"{self.mortes_seguidas} rounds seguidos morrendo. Respire fundo, solte o mouse "
                                "e reinicie o foco no próximo round.", "ruim", 0)
        elif not self.r_morreu and self.r_kills >= 2:
            self.alerta("round_bom", f"Round sólido: {self.r_kills} kills e sobreviveu.", "bom", 0)
        if self.r_rajadas_longas >= 3:
            self.alerta("spray_round", f"{self.r_rajadas_longas} rajadas longas neste round. Tente tap/burst na distância.",
                        "aviso", 0)

    def _encerrar_partida(self):
        if self._salvo or not (self.rounds or self.vidas or self.minutos or self.eventos):
            return
        self._salvo = True
        try:
            self.salvar()
        except Exception as exc:  # nao derruba o servidor
            print("Aviso: não consegui salvar a sessão:", exc)

    # ---- resumo, cartoes, tabelas e dicas ------------------------------------
    def _minutos_jogo(self):
        return (time.monotonic() - self.t_jogo0) / 60.0 if self.t_jogo0 is not None else 0.0

    def municao_infinita(self) -> bool:
        return self.layout == "treino" and self.kills >= 3 and self.tiros_total == 0

    def _rifle(self):
        rifle = {"tap": 0, "curta": 0, "media": 0, "longa": 0, "tiros": 0, "tiros_longos": 0}
        for classe in AUTOMATICAS:
            c = self.rajadas_cont.get(classe)
            if c:
                for k in rifle:
                    rifle[k] += c[k]
        return rifle

    def _cards(self):
        kd = round(self.kills / self.mortes, 2) if self.mortes else float(self.kills)
        lay = self.layout
        if lay == "rounds":
            return [[f"{self.kills}/{self.mortes}", f"Kills / Mortes (K/D {kd})"],
                    [self.r_kills, "Kills no round"],
                    [self.mortes_sem_atirar, "Mortes sem atirar"],
                    [self.mortes_cedo, f"Mortes cedo (<{MORTE_CEDO_S:.0f}s)"],
                    [self.mortes_seguidas, "Rounds seguidos morrendo"]]
        if lay == "vidas":
            mins = self._minutos_jogo()
            kpm = round(self.kills / mins, 1) if mins >= 1 else "—"
            return [[f"{self.kills}/{self.mortes}", f"Kills / Mortes (K/D {kd})"],
                    [kpm, "Kills por minuto"],
                    [self.r_kills, "Kills nesta vida"],
                    [self.mortes_sem_atirar, "Mortes sem atirar"],
                    [self.mortes_cedo, f"Mortes logo ao nascer (<{MORTE_CEDO_VIDA_S:.0f}s)"],
                    [self.mortes_seguidas, "Vidas seguidas sem kill"]]
        rifle = self._rifle()
        tpk = round(self.tiros_total / self.kills, 1) if self.kills and self.tiros_total else "—"
        pl = round(100.0 * rifle["tiros_longos"] / rifle["tiros"]) if rifle["tiros"] else "—"
        return [[self.kills, "Kills"], [self.tiros_total if not self.municao_infinita() else "—", "Tiros contados"],
                [tpk, "Tiros por kill"], [f"{pl}%" if pl != "—" else pl, "Balas em rajadas 7+"],
                [round(self._minutos_jogo()), "Minutos de treino"]]

    def _tabelas(self):
        lay = self.layout
        out = []
        if lay == "rounds":
            linhas = [[x["round"], x["kills"], "sim" if x["morreu"] else "não", x["tiros"],
                       x["rajadas_longas"], "—" if x["dano"] is None else x["dano"]]
                      for x in reversed(self.rounds[-30:])]
            out.append({"titulo": "Rounds", "colunas": ["Round", "Kills", "Morreu", "Tiros", "Rajadas 7+", "Dano"],
                        "linhas": linhas})
        elif lay == "vidas":
            linhas = [[x["vida"], x["kills"], "—" if x["duracao"] is None else f"{x['duracao']}s",
                       x["tiros"], x["rajadas_longas"]] for x in reversed(self.vidas[-20:])]
            out.append({"titulo": "Vidas (da mais recente para a mais antiga)",
                        "colunas": ["Vida", "Kills", "Duração", "Tiros", "Rajadas 7+"], "linhas": linhas})
        else:
            linhas = []
            for i in sorted(self.minutos, reverse=True)[:15]:
                m = self.minutos[i]
                tpk = f"{m['tiros'] / m['kills']:.1f}" if m["kills"] and m["tiros"] else "—"
                linhas.append([i + 1, m["kills"], m["tiros"] if not self.municao_infinita() else "—", tpk, m["longas"]])
            out.append({"titulo": "Por minuto de treino",
                        "colunas": ["Min", "Kills", "Tiros", "Tiros/kill", "Rajadas 7+"], "linhas": linhas})
        if self.modo in ("treino", "arms") and self.por_arma:
            armas = sorted(self.por_arma.items(), key=lambda kv: -(kv[1]["tiros"] + kv[1]["kills"]))[:8]
            out.append({"titulo": "Por arma", "colunas": ["Arma", "Tiros", "Kills", "Tiros/kill"],
                        "linhas": [[a, v["tiros"], v["kills"],
                                    f"{v['tiros'] / v['kills']:.1f}" if v["kills"] and v["tiros"] else "—"]
                                   for a, v in armas]})
        return out

    def resumo(self):
        rifle = self._rifle()
        n_raj = rifle["tap"] + rifle["curta"] + rifle["media"] + rifle["longa"]
        return {
            "modo": self.modo, "modo_nome": MODOS[self.modo][0], "layout": self.layout,
            "modo_forcado": self.modo_forcado,
            "mapa": self.mapa, "placar": self.placar, "round": self.round_num, "fase_round": self.fase_round,
            "fase_mapa": self.fase_mapa, "jogador": self.nome,
            "kills": self.kills, "mortes": self.mortes, "assistencias": self.assistencias,
            "hs_kills": self.hs_kills,
            "kd": round(self.kills / self.mortes, 2) if self.mortes else float(self.kills),
            "mortes_sem_atirar": self.mortes_sem_atirar, "mortes_cego": self.mortes_cego,
            "mortes_cedo": self.mortes_cedo, "mortes_seguidas": self.mortes_seguidas,
            "rajadas_auto": rifle, "n_rajadas_auto": n_raj,
            "pct_tiros_rajada_longa": round(100.0 * rifle["tiros_longos"] / rifle["tiros"], 1) if rifle["tiros"] else None,
            "por_arma": self.por_arma, "rounds": self.rounds[-30:], "vidas": self.vidas[-30:],
            "minutos": [{"min": i + 1, **self.minutos[i]} for i in sorted(self.minutos)],
            "duracao_min": round(self._minutos_jogo(), 1), "tiros_total": self.tiros_total,
            "round_atual": {"kills": self.r_kills, "tiros": self.r_tiros, "rajadas_longas": self.r_rajadas_longas,
                            "morreu": self.r_morreu, "dano": self.r_dano},
            "municao_infinita": self.municao_infinita(),
            "cards": self._cards(), "tabelas": self._tabelas(),
            "dicas": self.dicas(),
        }

    def dicas(self):
        d = []
        lay = self.layout
        r = self.resumo_basico()
        if lay == "treino":
            if self.municao_infinita():
                d.append("Este mapa parece ter munição infinita: o jogo não informa os tiros nesse caso, "
                         "então só consigo contar as kills.")
            elif self.tiros_total >= 60 and r["n_raj"] >= 5:
                pl = 100 * r["longa"] / r["n_raj"]
                if pl >= 50:
                    d.append(f"{pl:.0f}% das suas rajadas passam de 7 balas. Se o treino é de controle de spray, "
                             "ótimo; se é de precisão, pratique tap e bursts de 2-3 balas.")
                else:
                    d.append("Você está treinando principalmente tap/bursts, bom para duelos de média e longa distância.")
            if self.kills >= 10 and self.tiros_total:
                d.append(f"{self.tiros_total / self.kills:.1f} tiros por kill. Compare esse número minuto a minuto: "
                         "ele deve cair conforme sua mira melhora.")
            if not d:
                d.append("Continue treinando; a análise melhora com mais tiros e kills.")
            return d
        if r["n_raj"] >= 8 and r["longa"] / r["n_raj"] >= 0.3:
            d.append(f"{100 * r['longa'] / r['n_raj']:.0f}% das suas rajadas automáticas passam de 7 balas. "
                     "Treine tap/burst e solte o gatilho para resetar a mira.")
        piso = 4 if lay == "rounds" else 6
        if self.mortes >= piso and self.mortes_sem_atirar / self.mortes >= 0.3:
            d.append(f"{self.mortes_sem_atirar} de {self.mortes} mortes sem nenhum tiro. "
                     "Use o som e pré-mire os ângulos comuns antes de entrar.")
        if self.mortes >= piso and self.mortes_cedo / self.mortes >= 0.4:
            if lay == "rounds":
                d.append(f"{self.mortes_cedo} de {self.mortes} mortes nos primeiros {MORTE_CEDO_S:.0f}s do round. "
                         "Prefira segurar ângulos e deixar o inimigo vir.")
            else:
                d.append(f"{self.mortes_cedo} de {self.mortes} mortes logo depois de nascer. "
                         "Confira os spawns antes de sair correndo.")
        if self.mortes_cego >= 2:
            d.append(f"{self.mortes_cego} mortes enquanto cego por flash. Saia da linha de visão quando vier o flash.")
        if not d:
            d.append("Sem padrões fortes ainda. Continue jogando; a análise melhora com mais "
                     + ("rounds." if lay == "rounds" else "vidas."))
        return d

    def resumo_basico(self):
        n = longa = 0
        for classe in AUTOMATICAS:
            c = self.rajadas_cont.get(classe)
            if c:
                n += c["tap"] + c["curta"] + c["media"] + c["longa"]
                longa += c["longa"]
        return {"n_raj": n, "longa": longa}

    def estado(self):
        with self.lock:
            r = self.resumo()
            r["alertas"] = list(self.alertas)[:15]
            r["conectado"] = bool(self.ultimo_pacote and time.time() - self.ultimo_pacote < 15)
            r["pacotes"] = self.pacotes
            r["recusados"] = self.recusados
            r["modos"] = [{"id": k, "nome": v[0]} for k, v in MODOS.items()]
            r["segundos_desde_pacote"] = round(time.time() - self.ultimo_pacote, 1) if self.ultimo_pacote else None
            return r

    def salvar(self):
        PASTA_SESSOES.mkdir(parents=True, exist_ok=True)
        carimbo = self.sessao_inicio.strftime("%Y%m%d_%H%M%S")
        seguro = re.sub(r"[^A-Za-z0-9]+", "_", self.mapa or "partida")
        caminho = PASTA_SESSOES / f"sessao_{carimbo}_{seguro}.json"
        dados = self.resumo()
        dados["eventos"] = self.eventos
        caminho.write_text(json.dumps(dados, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"Sessão salva em {caminho}")
        return caminho


# --------------------------------------------------------------------------
# Servidor HTTP
# --------------------------------------------------------------------------
PAGINA = r"""<!doctype html><html lang="pt-BR"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>CS2 Coach ao vivo</title>
<style>
:root{--bg:#0f1218;--card:#181d27;--card2:#202736;--tx:#e8ecf3;--mut:#93a0b5;--ac:#4cc2ff;--ok:#4cd68a;--ruim:#ff6b6b;--warn:#ffc857}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--tx);font:15px/1.5 system-ui,Segoe UI,Roboto,sans-serif}
.wrap{max-width:900px;margin:0 auto;padding:20px 16px 50px}h1{font-size:22px;margin:0}
.sub{color:var(--mut)}.dot{display:inline-block;width:10px;height:10px;border-radius:50%;margin-right:6px;background:var(--ruim)}
.dot.on{background:var(--ok)}h2{font-size:16px;margin:26px 0 10px;border-left:4px solid var(--ac);padding-left:10px}
.cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(120px,1fr));gap:10px;margin:14px 0}
.card{background:var(--card);border-radius:10px;padding:10px 14px}.card b{display:block;font-size:24px}.card span{color:var(--mut);font-size:12px}
.al{background:var(--card);border-left:4px solid var(--ac);border-radius:8px;padding:8px 12px;margin:6px 0}
.al.ruim{border-color:var(--ruim)}.al.aviso{border-color:var(--warn)}.al.bom{border-color:var(--ok)}.al small{color:var(--mut)}
table{width:100%;border-collapse:collapse;background:var(--card);border-radius:10px;overflow:hidden}
th,td{padding:7px 10px;text-align:left;border-bottom:1px solid var(--card2)}th{color:var(--mut);font-size:13px}
ul{padding-left:20px}.vazio{color:var(--mut)}.ajuda{background:var(--card);border-left:4px solid var(--warn);border-radius:8px;padding:10px 14px;margin:12px 0}.ajuda ol{margin:6px 0 0;padding-left:20px}
select{background:var(--card);color:var(--tx);border:1px solid var(--card2);border-radius:6px;padding:3px 8px;font:inherit}
.modo{margin-top:6px}
</style></head><body><div class="wrap">
<h1>CS2 Coach ao vivo</h1>
<div class="sub"><span id="dot" class="dot"></span><span id="status">aguardando o jogo...</span></div>
<div class="sub modo">Modo: <select id="modo"><option value="auto">Automático</option></select> <span id="modoinfo"></span></div>
<div id="ajuda"></div>
<div class="cards" id="cards"></div>
<h2>Avisos</h2><div id="alertas"><div class="vazio">Nenhum aviso ainda.</div></div>
<h2>Rajadas de rifle/SMG</h2><div id="rajadas"></div>
<h2>Dicas da sessão</h2><ul id="dicas"></ul>
<div id="tabelas"></div>
</div>
<script>
function el(t,c,x){var e=document.createElement(t);if(c)e.className=c;if(x!==undefined)e.textContent=x;return e}
function card(v,l){var d=el('div','card');d.appendChild(el('b','',v));d.appendChild(el('span','',l));return d}
var sel=document.getElementById('modo'),opcoesOk=false;
sel.addEventListener('change',function(){fetch('/modo?m='+encodeURIComponent(sel.value),{cache:'no-store'}).then(tick)});
async function tick(){
  try{
    var r=await (await fetch('/estado',{cache:'no-store'})).json();
    if(!opcoesOk&&r.modos){r.modos.forEach(function(m){var o=el('option','',m.nome);o.value=m.id;sel.appendChild(o)});opcoesOk=true}
    if(document.activeElement!==sel)sel.value=r.modo_forcado||'auto';
    document.getElementById('modoinfo').textContent=r.modo_forcado?'(escolhido por você)':'(detectado: '+r.modo_nome+')';
    document.getElementById('dot').className='dot'+(r.conectado?' on':'');
    var st=(r.conectado?'conectado':'sem dados do jogo')+(r.mapa?' · '+r.mapa:'')+' · '+r.modo_nome;
    if(r.layout==='rounds'){st+=(r.round!=null?' · round '+r.round:'')+(r.placar?' · '+r.placar:'')+(r.fase_round?' · '+r.fase_round:'')}
    document.getElementById('status').textContent=st;
    var aj=document.getElementById('ajuda');aj.replaceChildren();
    if(!r.conectado){var bx=el('div','ajuda');
      if(r.recusados>0){bx.appendChild(el('b','','O jogo está enviando dados, mas com o token errado.'));
        var o1=el('ol');['Rode: python ao_vivo.py --instalar','Reinicie o CS2 e tente de novo.'].forEach(function(s){o1.appendChild(el('li','',s))});bx.appendChild(o1)}
      else if(r.pacotes>0){bx.appendChild(el('b','','Os dados pararam de chegar. Verifique se o CS2 continua aberto (ou aguarde alguns segundos).'))}
      else{bx.appendChild(el('b','','Nenhum dado do jogo chegou ainda. Confira:'));
        var o=el('ol');['Rode: python ao_vivo.py --diagnostico (mostra se o arquivo de configuração está no lugar certo).',
          'O arquivo gamestate_integration_cs2coach.cfg precisa estar em ...\\Counter-Strike Global Offensive\\game\\csgo\\cfg.',
          'Feche e abra o CS2 de novo depois de colocar o arquivo (só ler no início do jogo).',
          'Se o Windows perguntar sobre o Firewall para o Python, permita.',
          'Entre numa partida ou no modo offline com bots: o jogo só envia dados dentro de uma partida.'].forEach(function(s){o.appendChild(el('li','',s))});bx.appendChild(o)}
      aj.appendChild(bx)}
    var c=document.getElementById('cards');c.replaceChildren.apply(c,r.cards.map(function(x){return card(x[0],x[1])}));
    var a=document.getElementById('alertas');a.replaceChildren();
    if(!r.alertas.length)a.appendChild(el('div','vazio','Nenhum aviso ainda.'));
    r.alertas.forEach(function(x){var d=el('div','al '+x.sev);d.appendChild(el('small','',x.hora+(x.round!=null?' · round '+x.round:'')+'  '));d.appendChild(document.createTextNode(x.texto));a.appendChild(d)});
    var ra=r.rajadas_auto,rj=document.getElementById('rajadas');rj.replaceChildren();
    if(!r.n_rajadas_auto){rj.appendChild(el('div','vazio','Sem rajadas registradas.'))}else{
      var t=el('table');var h=el('tr');['1 bala','2-3','4-6','7+ (spray)','Total'].forEach(function(s){h.appendChild(el('th','',s))});t.appendChild(h);
      var l=el('tr');[ra.tap,ra.curta,ra.media,ra.longa,r.n_rajadas_auto].forEach(function(s){l.appendChild(el('td','',s))});t.appendChild(l);rj.appendChild(t)}
    var u=document.getElementById('dicas');u.replaceChildren();r.dicas.forEach(function(s){u.appendChild(el('li','',s))});
    var tb=document.getElementById('tabelas');tb.replaceChildren();
    r.tabelas.forEach(function(tab){
      tb.appendChild(el('h2','',tab.titulo));
      if(!tab.linhas.length){tb.appendChild(el('div','vazio','Ainda sem dados.'));return}
      var t2=el('table');var h2=el('tr');tab.colunas.forEach(function(s){h2.appendChild(el('th','',s))});t2.appendChild(h2);
      tab.linhas.forEach(function(row){var l2=el('tr');row.forEach(function(s){l2.appendChild(el('td','',s))});t2.appendChild(l2)});tb.appendChild(t2)});
  }catch(e){document.getElementById('dot').className='dot';document.getElementById('status').textContent='painel sem conexão com o programa'}
}
tick();setInterval(tick,1000);
</script></body></html>"""


def criar_handler(coach: Coach):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):  # silencioso
            pass

        def _responder(self, codigo, corpo=b"", tipo="text/plain; charset=utf-8"):
            self.send_response(codigo)
            self.send_header("Content-Type", tipo)
            self.send_header("Content-Length", str(len(corpo)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            if corpo:
                self.wfile.write(corpo)

        def do_POST(self):
            try:
                n = int(self.headers.get("Content-Length", 0))
                corpo = self.rfile.read(n) if n else b"{}"
                dados = json.loads(corpo.decode("utf-8"))
            except Exception:
                return self._responder(400, b"json invalido")
            if (dados.get("auth", {}) or {}).get("token") != coach.token:
                coach.recusados += 1
                if coach.recusados in (1, 50):
                    print("⚠ O jogo está enviando dados, mas com um token diferente. Rode de novo "
                          "'python ao_vivo.py --instalar' e reinicie o CS2.")
                return self._responder(401, b"token invalido")
            coach.registrar_pacote(dados)
            try:
                coach.processar(dados)
            except Exception as exc:
                print("Aviso: erro ao processar um pacote:", exc)
            self._responder(200)

        def do_GET(self):
            if self.path.split("?")[0] == "/estado":
                return self._responder(200, json.dumps(coach.estado(), ensure_ascii=False).encode("utf-8"),
                                       "application/json; charset=utf-8")
            if self.path.split("?")[0] == "/modo":
                from urllib.parse import urlparse, parse_qs
                m = (parse_qs(urlparse(self.path).query).get("m") or [""])[0]
                return self._responder(200 if coach.definir_modo(m) else 400, b"ok" if m else b"faltou m")
            if self.path.split("?")[0] == "/ultimo":
                with coach.lock:
                    bruto = coach.ultimo_raw
                return self._responder(200, json.dumps(bruto, ensure_ascii=False, indent=1).encode("utf-8"),
                                       "application/json; charset=utf-8")
            if self.path.split("?")[0] in ("/", "/index.html"):
                return self._responder(200, PAGINA.encode("utf-8"), "text/html; charset=utf-8")
            self._responder(404, b"nao encontrado")

    return Handler


# --------------------------------------------------------------------------
# Instalacao do arquivo de configuracao no CS2
# --------------------------------------------------------------------------
def conteudo_cfg(porta: int, token: str) -> str:
    return f'''"CS2Coach"
{{
    "uri" "http://127.0.0.1:{porta}"
    "timeout" "5.0"
    "buffer" "0.1"
    "throttle" "0.1"
    "heartbeat" "5.0"
    "auth"
    {{
        "token" "{token}"
    }}
    "data"
    {{
        "provider" "1"
        "map" "1"
        "round" "1"
        "player_id" "1"
        "player_state" "1"
        "player_weapons" "1"
        "player_match_stats" "1"
    }}
}}
'''


def bibliotecas_steam():
    bases = []
    if sys.platform.startswith("win"):
        for var in ("PROGRAMFILES(X86)", "PROGRAMFILES"):
            v = os.environ.get(var)
            if v:
                bases.append(Path(v) / "Steam")
        bases += [Path("C:/Program Files (x86)/Steam"), Path("D:/Steam"), Path("E:/Steam")]
    else:
        home = Path.home()
        bases += [home / ".steam" / "steam", home / ".local" / "share" / "Steam"]
    libs = []
    for b in bases:
        if (b / "steamapps").exists():
            libs.append(b)
        vdf = b / "steamapps" / "libraryfolders.vdf"
        if vdf.exists():
            try:
                for m in re.finditer(r'"path"\s+"([^"]+)"', vdf.read_text(encoding="utf-8", errors="ignore")):
                    libs.append(Path(m.group(1).replace("\\\\", "\\")))
            except Exception:
                pass
    vistos, unicos = set(), []
    for l in libs:
        if str(l) not in vistos:
            vistos.add(str(l))
            unicos.append(l)
    return unicos


def achar_pasta_cfg():
    for lib in bibliotecas_steam():
        p = lib / "steamapps" / "common" / "Counter-Strike Global Offensive" / "game" / "csgo" / "cfg"
        if p.exists():
            return p
    return None


def instalar(porta: int, token: str):
    texto = conteudo_cfg(porta, token)
    nome = "gamestate_integration_cs2coach.cfg"
    pasta = achar_pasta_cfg()
    if pasta:
        destino = pasta / nome
        destino.write_text(texto, encoding="utf-8")
        print(f"Arquivo criado em: {destino}")
        print("Reinicie o CS2 se ele já estava aberto.")
    else:
        destino = caminhos.pasta_dados() / nome
        destino.write_text(texto, encoding="utf-8")
        print("Não encontrei a pasta do CS2 automaticamente.")
        print(f"Criei o arquivo aqui: {destino}")
        print("Copie-o para a pasta:  ...\\Steam\\steamapps\\common\\Counter-Strike Global Offensive\\game\\csgo\\cfg")
        print("(Steam > clique direito no CS2 > Gerenciar > Explorar arquivos locais > game > csgo > cfg)")


def diagnostico(porta: int, token: str):
    import socket
    print("=== Diagnóstico do CS2 Coach ===\n")
    print(f"1) Python {sys.version.split()[0]} em {sys.platform}")
    # porta livre?
    s = socket.socket()
    try:
        s.bind(("127.0.0.1", porta))
        print(f"2) Porta {porta}: livre (ok). Lembre de rodar 'python ao_vivo.py' antes de abrir o jogo.")
    except OSError:
        print(f"2) Porta {porta}: em uso. Se for o próprio ao_vivo.py rodando, tudo certo; senão use --porta 3001 "
              "(e rode --instalar com a mesma porta).")
    finally:
        s.close()
    libs = bibliotecas_steam()
    if not libs:
        print("3) Não achei a pasta do Steam automaticamente.")
    achou = False
    for lib in libs:
        cfgdir = lib / "steamapps" / "common" / "Counter-Strike Global Offensive" / "game" / "csgo" / "cfg"
        if not cfgdir.exists():
            continue
        achou = True
        arq = cfgdir / "gamestate_integration_cs2coach.cfg"
        print(f"3) Pasta do CS2 encontrada: {cfgdir}")
        if not arq.exists():
            print("   ✘ O arquivo gamestate_integration_cs2coach.cfg NÃO está lá. Rode: python ao_vivo.py --instalar")
        else:
            txt = arq.read_text(encoding="utf-8", errors="ignore")
            uri = re.search(r'"uri"\s+"([^"]+)"', txt)
            tok = re.search(r'"token"\s+"([^"]+)"', txt)
            print(f"   ✔ Arquivo existe (uri {uri.group(1) if uri else '?'}).")
            if uri and f":{porta}" not in uri.group(1):
                print(f"   ✘ A porta do arquivo é diferente da porta {porta} deste programa.")
            if tok and tok.group(1) != token:
                print("   ✘ O token do arquivo é diferente do token deste programa.")
            print("   Se mudou algo agora, feche e abra o CS2 de novo.")
        antigos = [p.name for p in cfgdir.glob("gamestate_integration_*.cfg")]
        if len(antigos) > 1:
            print(f"   Outros arquivos de integração presentes: {', '.join(antigos)} (não atrapalham).")
    if libs and not achou:
        print("3) Achei o Steam mas não a pasta do CS2. Copie o arquivo gerado por --instalar "
              "para: ...\\Counter-Strike Global Offensive\\game\\csgo\\cfg")
    print("\n4) Teste final: deixe 'python ao_vivo.py' rodando, abra o CS2 e entre numa partida.")
    print("   Quando o primeiro dado chegar, o terminal mostrará: ✔ Primeiro pacote do jogo recebido!")


# --------------------------------------------------------------------------
def main():
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="replace", line_buffering=True)
        except Exception:
            pass
    ap = argparse.ArgumentParser(description="Painel ao vivo do CS2 Coach (Game State Integration).")
    ap.add_argument("--porta", type=int, default=3000)
    ap.add_argument("--host", default="127.0.0.1",
                    help="use 0.0.0.0 para abrir o painel em outro aparelho da sua rede (celular/tablet)")
    ap.add_argument("--token", default="cs2coach")
    ap.add_argument("--instalar", action="store_true", help="cria o arquivo de configuração na pasta do CS2")
    ap.add_argument("--diagnostico", action="store_true", help="verifica por que o jogo não está enviando dados")
    args = ap.parse_args()

    if args.instalar:
        instalar(args.porta, args.token)
        return
    if args.diagnostico:
        diagnostico(args.porta, args.token)
        return

    coach = Coach(args.token)

    def vigia():
        aviso = 0.0
        while True:
            time.sleep(0.2)
            coach.fechar_rajadas_inativas()
            agora = time.time()
            if coach.pacotes == 0 and agora - coach.iniciado_em > 45 and agora - aviso > 45:
                aviso = agora
                print("… ainda sem dados do jogo. Rode 'python ao_vivo.py --diagnostico' em outro terminal.")

    threading.Thread(target=vigia, daemon=True).start()
    servidor = ThreadingHTTPServer((args.host, args.porta), criar_handler(coach))
    print(f"CS2 Coach ao vivo rodando. Abra no navegador: http://127.0.0.1:{args.porta}")
    print("Deixe esta janela aberta enquanto joga. Ctrl+C para encerrar.")
    try:
        servidor.serve_forever()
    except KeyboardInterrupt:
        print("\nEncerrando...")
    finally:
        with coach.lock:
            coach._fechar_rajada()
            coach._encerrar_partida()
        servidor.server_close()


if __name__ == "__main__":
    main()
