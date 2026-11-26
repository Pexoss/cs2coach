# -*- coding: utf-8 -*-
"""
Armazem local do CS2 Coach (SQLite).

Tudo fica em  dados/cs2coach.db  ao lado do programa, no SEU computador: partidas
analisadas, estatisticas por arma, leituras das configuracoes e sessoes do painel ao vivo.
Para fazer backup, copie a pasta de dados (veja o caminho em Ajustes). Para apagar tudo, apague essa pasta.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

BASE = Path(__file__).resolve().parent
import sys as _sys
_sys.path.insert(0, str(BASE))
import armas as armas_mod  # noqa: E402

import caminhos  # noqa: E402

caminhos.migrar_dados_antigos()
PASTA_DADOS = caminhos.pasta_dados()
CAMINHO_DB = PASTA_DADOS / "cs2coach.db"
_lock = threading.RLock()

SCHEMA = """
CREATE TABLE IF NOT EXISTS partidas(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    hash TEXT NOT NULL, steamid TEXT NOT NULL, demo TEXT, mapa TEXT, jogador TEXT,
    data_demo TEXT, analisado_em TEXT,
    rounds INTEGER, kills INTEGER, mortes INTEGER, assistencias INTEGER,
    kd REAL, adr REAL, hs REAL,
    resultado_json TEXT, dicas_json TEXT, fortes_json TEXT, hist_json TEXT, relatorio_html TEXT,
    UNIQUE(hash, steamid)
);
CREATE TABLE IF NOT EXISTS armas_partida(
    partida_id INTEGER NOT NULL REFERENCES partidas(id) ON DELETE CASCADE,
    arma TEXT NOT NULL, nome TEXT, tipo TEXT, classe TEXT,
    tiros INTEGER, tiros_com_acerto INTEGER, acertos INTEGER, dano INTEGER,
    kills INTEGER, hs_kills INTEGER, hs_acertos INTEGER, dist_media REAL,
    PRIMARY KEY(partida_id, arma)
);
CREATE TABLE IF NOT EXISTS sessoes(
    id INTEGER PRIMARY KEY AUTOINCREMENT, arquivo TEXT UNIQUE, mapa TEXT, modo TEXT, modo_nome TEXT,
    iniciado TEXT, kills INTEGER, mortes INTEGER, json TEXT
);
CREATE TABLE IF NOT EXISTS config_leituras(
    id INTEGER PRIMARY KEY AUTOINCREMENT, lido_em TEXT, json TEXT
);
CREATE TABLE IF NOT EXISTS ajustes(chave TEXT PRIMARY KEY, valor TEXT);
CREATE INDEX IF NOT EXISTS idx_partidas_steam ON partidas(steamid, data_demo);
"""


def definir_caminho(caminho) -> None:
    """Usado nos testes para apontar para outro arquivo de banco."""
    global CAMINHO_DB, PASTA_DADOS
    CAMINHO_DB = Path(caminho)
    PASTA_DADOS = CAMINHO_DB.parent


@contextmanager
def _db():
    with _lock:
        PASTA_DADOS.mkdir(parents=True, exist_ok=True)
        con = sqlite3.connect(str(CAMINHO_DB), timeout=30)
        con.row_factory = sqlite3.Row
        try:
            con.execute("PRAGMA foreign_keys=ON")
            con.execute("PRAGMA journal_mode=WAL")
            con.executescript(SCHEMA)
            cols = [r["name"] for r in con.execute("PRAGMA table_info(sessoes)")]
            if "partida_id" not in cols:      # migracao: vinculo sessao ao vivo <-> demo
                con.execute("ALTER TABLE sessoes ADD COLUMN partida_id INTEGER")
            yield con
            con.commit()
        except Exception:
            con.rollback()
            raise
        finally:
            con.close()


def _agora() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def hash_arquivo(caminho) -> str:
    h = hashlib.sha256()
    with open(caminho, "rb") as f:
        for bloco in iter(lambda: f.read(4 * 1024 * 1024), b""):
            h.update(bloco)
    return h.hexdigest()


# --------------------------------------------------------------------------
# Ajustes (steamid, pasta de demos, DPI, Hz do monitor...)
# --------------------------------------------------------------------------
def ajuste_get(chave, padrao=None):
    with _db() as con:
        r = con.execute("SELECT valor FROM ajustes WHERE chave=?", (chave,)).fetchone()
    return r["valor"] if r and r["valor"] not in (None, "") else padrao


def ajuste_set(chave, valor) -> None:
    with _db() as con:
        con.execute("INSERT INTO ajustes(chave, valor) VALUES(?,?) "
                    "ON CONFLICT(chave) DO UPDATE SET valor=excluded.valor", (chave, "" if valor is None else str(valor)))


def ajustes_todos() -> dict:
    with _db() as con:
        return {r["chave"]: r["valor"] for r in con.execute("SELECT chave, valor FROM ajustes")}


# --------------------------------------------------------------------------
# Partidas
# --------------------------------------------------------------------------
def partida_existe(hash_arquivo_, steamid) -> bool:
    with _db() as con:
        return con.execute("SELECT 1 FROM partidas WHERE hash=? AND steamid=?",
                           (hash_arquivo_, str(steamid))).fetchone() is not None


def salvar_partida(res: dict, dicas: list, fortes: list, html_txt: str, hash_arquivo_: str, hist: dict) -> int:
    pid = _salvar_partida(res, dicas, fortes, html_txt, hash_arquivo_, hist)
    try:
        vincular_sessoes()
    except Exception:       # o vinculo e um complemento: nunca impede de guardar a demo
        pass
    return pid


def _salvar_partida(res: dict, dicas: list, fortes: list, html_txt: str, hash_arquivo_: str, hist: dict) -> int:
    meta, g = res["meta"], res["geral"]
    with _db() as con:
        con.execute("DELETE FROM partidas WHERE hash=? AND steamid=?", (hash_arquivo_, str(meta["steamid"])))
        cur = con.execute(
            "INSERT INTO partidas(hash, steamid, demo, mapa, jogador, data_demo, analisado_em, rounds, kills, mortes,"
            " assistencias, kd, adr, hs, resultado_json, dicas_json, fortes_json, hist_json, relatorio_html)"
            " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (hash_arquivo_, str(meta["steamid"]), meta.get("demo"), meta.get("mapa"), meta.get("jogador"),
             meta.get("data_demo") or meta.get("analisado_em"), meta.get("analisado_em"),
             g.get("rounds"), g.get("kills"), g.get("mortes"), g.get("assistencias"), g.get("kd"), g.get("adr"),
             g.get("hs_pct"), json.dumps(res, ensure_ascii=False, default=float),
             json.dumps(dicas, ensure_ascii=False), json.dumps(fortes, ensure_ascii=False),
             json.dumps(hist, ensure_ascii=False, default=float), html_txt))
        pid = cur.lastrowid
        for a in res.get("armas", []):
            con.execute(
                "INSERT INTO armas_partida(partida_id, arma, nome, tipo, classe, tiros, tiros_com_acerto, acertos,"
                " dano, kills, hs_kills, hs_acertos, dist_media) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (pid, a["arma"], a.get("nome"), a.get("tipo"), a.get("classe"), a.get("tiros", 0),
                 a.get("tiros_com_acerto", 0), a.get("acertos", 0), a.get("dano", 0), a.get("kills", 0),
                 a.get("hs_kills", 0), a.get("hs_acertos", 0), a.get("dist_media_m")))
    return pid


_COLUNAS_LISTA = ("id, hash, steamid, demo, mapa, jogador, data_demo, analisado_em, rounds, kills, mortes, "
                  "assistencias, kd, adr, hs")


def listar_partidas(steamid=None, mapa=None, limite=None) -> list:
    sql = f"SELECT {_COLUNAS_LISTA} FROM partidas WHERE 1=1"
    par = []
    if steamid:
        sql += " AND steamid=?"
        par.append(str(steamid))
    if mapa:
        sql += " AND mapa=?"
        par.append(mapa)
    sql += " ORDER BY data_demo DESC, id DESC"
    if limite:
        sql += f" LIMIT {int(limite)}"
    with _db() as con:
        return [dict(r) for r in con.execute(sql, par)]


def mapas_disponiveis(steamid=None) -> list:
    with _db() as con:
        if steamid:
            rows = con.execute("SELECT DISTINCT mapa FROM partidas WHERE steamid=? ORDER BY mapa", (str(steamid),))
        else:
            rows = con.execute("SELECT DISTINCT mapa FROM partidas ORDER BY mapa")
        return [r["mapa"] for r in rows if r["mapa"]]


def obter_partida(pid: int):
    with _db() as con:
        r = con.execute(f"SELECT {_COLUNAS_LISTA}, resultado_json, dicas_json, fortes_json FROM partidas WHERE id=?",
                        (int(pid),)).fetchone()
    if not r:
        return None
    d = dict(r)
    d["resultado"] = json.loads(d.pop("resultado_json") or "{}")
    d["dicas"] = json.loads(d.pop("dicas_json") or "[]")
    d["fortes"] = json.loads(d.pop("fortes_json") or "[]")
    return d


def atualizar_html(pid: int, html_txt: str) -> None:
    with _db() as con:
        con.execute("UPDATE partidas SET relatorio_html=? WHERE id=?", (html_txt, int(pid)))


def html_partida(pid: int):
    with _db() as con:
        r = con.execute("SELECT relatorio_html FROM partidas WHERE id=?", (int(pid),)).fetchone()
    return r["relatorio_html"] if r else None


def excluir_partida(pid: int) -> None:
    with _db() as con:
        con.execute("UPDATE sessoes SET partida_id=NULL WHERE partida_id=?", (int(pid),))
        con.execute("DELETE FROM partidas WHERE id=?", (int(pid),))


def armas_da_partida(pid: int) -> list:
    with _db() as con:
        return [dict(r) for r in con.execute(
            "SELECT arma, nome, tipo, classe, tiros, tiros_com_acerto, acertos, dano, kills, hs_kills, hs_acertos,"
            " dist_media AS dist_media_m FROM armas_partida WHERE partida_id=? ORDER BY dano DESC", (int(pid),))]


MODOS_CONTAM_PADRAO = "competitivo,wingman"


def modos_que_contam() -> set:
    """Modos de jogo ao vivo que entram nos numeros do Hub (os de diversao e o treino ficam de fora)."""
    txt = ajuste_get("modos_contam")
    txt = MODOS_CONTAM_PADRAO if txt is None else txt
    return {m.strip().lower() for m in str(txt).split(",") if m.strip()} - {"treino"}


def armas_agregadas(steamid=None, mapa=None, ultimas=None, ao_vivo=True) -> list:
    """
    Soma das estatisticas por arma de todas (ou das ultimas N) partidas.
    Com ao_vivo=True entram tambem as sessoes do painel ao vivo que NAO tem demo (as que ja tem demo
    ficam so com a demo, para nao contar duas vezes). O ao vivo so conhece tiros e kills por arma;
    por isso cada arma traz tiros_demo/kills_demo (base das porcentagens) e tiros_ao_vivo/kills_ao_vivo.
    """
    filtro, par = "WHERE 1=1", []
    if steamid:
        filtro += " AND p.steamid=?"
        par.append(str(steamid))
    if mapa:
        filtro += " AND p.mapa=?"
        par.append(mapa)
    if ultimas:
        filtro += (" AND p.id IN (SELECT id FROM partidas WHERE 1=1"
                   + (" AND steamid=?" if steamid else "") + (" AND mapa=?" if mapa else "")
                   + f" ORDER BY data_demo DESC, id DESC LIMIT {int(ultimas)})")
        if steamid:
            par.append(str(steamid))
        if mapa:
            par.append(mapa)
    sql = f"""
        SELECT a.arma AS arma, MAX(a.nome) AS nome, MAX(a.tipo) AS tipo, MAX(a.classe) AS classe,
               COUNT(DISTINCT a.partida_id) AS partidas,
               SUM(a.tiros) AS tiros, SUM(a.tiros_com_acerto) AS tiros_com_acerto, SUM(a.acertos) AS acertos,
               SUM(a.dano) AS dano, SUM(a.kills) AS kills, SUM(a.hs_kills) AS hs_kills, SUM(a.hs_acertos) AS hs_acertos,
               SUM(CASE WHEN a.dist_media IS NOT NULL THEN a.dist_media * a.acertos END)
                 / NULLIF(SUM(CASE WHEN a.dist_media IS NOT NULL THEN a.acertos END), 0) AS dist_media_m
        FROM armas_partida a JOIN partidas p ON p.id = a.partida_id {filtro}
        GROUP BY a.arma"""
    with _db() as con:
        linhas = [dict(r) for r in con.execute(sql, par)]
        corte = None
        if ultimas:
            ds = [r["data_demo"] for r in con.execute(
                "SELECT data_demo FROM partidas WHERE 1=1" + (" AND steamid=?" if steamid else "")
                + (" AND mapa=?" if mapa else "") + f" ORDER BY data_demo DESC, id DESC LIMIT {int(ultimas)}",
                ([str(steamid)] if steamid else []) + ([mapa] if mapa else []))]
            if len(ds) >= int(ultimas):
                corte = ds[-1]
        sess = []
        if ao_vivo:
            contam = sorted(modos_que_contam())
            q = ("SELECT iniciado, json FROM sessoes WHERE partida_id IS NULL AND modo IN ("
                 + ",".join("?" * len(contam)) + ")") if contam else "SELECT iniciado, json FROM sessoes WHERE 0"
            pp = list(contam)
            if mapa:
                q += " AND mapa=?"
                pp.append(mapa)
            sess = [dict(r) for r in con.execute(q, pp)]
    por = {}
    for r in linhas:
        r["tiros_demo"], r["kills_demo"] = r["tiros"] or 0, r["kills"] or 0
        r["tiros_ao_vivo"] = r["kills_ao_vivo"] = 0
        por[r["arma"]] = r
    for s in sess:
        if corte and (s["iniciado"] or "") < corte:
            continue
        try:
            pa = (json.loads(s["json"] or "{}").get("por_arma")) or {}
        except ValueError:
            continue
        for arma, v in pa.items():
            a = armas_mod.norm_arma(arma)
            if not a:
                continue
            r = por.get(a)
            if r is None:
                r = por[a] = {"arma": a, "nome": armas_mod.nome_arma(a), "tipo": armas_mod.tipo_arma(a), "classe": None,
                              "partidas": 0, "tiros": 0, "tiros_com_acerto": 0, "acertos": 0, "dano": 0, "kills": 0,
                              "hs_kills": 0, "hs_acertos": 0, "dist_media_m": None, "tiros_demo": 0, "kills_demo": 0,
                              "tiros_ao_vivo": 0, "kills_ao_vivo": 0}
            r["tiros"] = (r["tiros"] or 0) + int(v.get("tiros") or 0)
            r["kills"] = (r["kills"] or 0) + int(v.get("kills") or 0)
            r["tiros_ao_vivo"] += int(v.get("tiros") or 0)
            r["kills_ao_vivo"] += int(v.get("kills") or 0)
            r["partidas"] = (r["partidas"] or 0) + 1
    return list(por.values())


def arma_por_partida(arma: str, steamid=None) -> list:
    sql = ("SELECT p.id AS partida_id, p.data_demo, p.mapa, a.tiros, a.tiros_com_acerto, a.acertos, a.dano, a.kills,"
           " a.hs_acertos FROM armas_partida a JOIN partidas p ON p.id=a.partida_id WHERE a.arma=?")
    par = [arma]
    if steamid:
        sql += " AND p.steamid=?"
        par.append(str(steamid))
    sql += " ORDER BY p.data_demo, p.id"
    with _db() as con:
        return [dict(r) for r in con.execute(sql, par)]


def historico(steamid=None) -> list:
    sql = "SELECT id, hist_json, data_demo FROM partidas"
    par = []
    if steamid:
        sql += " WHERE steamid=?"
        par.append(str(steamid))
    sql += " ORDER BY data_demo, id"
    out = []
    with _db() as con:
        for r in con.execute(sql, par):
            try:
                d = json.loads(r["hist_json"] or "{}")
            except ValueError:
                d = {}
            d["id"] = r["id"]
            d.setdefault("data_demo", r["data_demo"])
            out.append(d)
    return out


def dicas_recorrentes(steamid=None) -> list:
    """Quais dicas aparecem em mais partidas (o que mais se repete)."""
    sql = "SELECT id, data_demo, dicas_json FROM partidas"
    par = []
    if steamid:
        sql += " WHERE steamid=?"
        par.append(str(steamid))
    sql += " ORDER BY data_demo, id"
    agreg, total = {}, 0
    with _db() as con:
        for r in con.execute(sql, par):
            total += 1
            try:
                dicas = json.loads(r["dicas_json"] or "[]")
            except ValueError:
                dicas = []
            for d in dicas:
                a = agreg.setdefault(d["titulo"], {"titulo": d["titulo"], "vezes": 0, "prioridade": d.get("prioridade", 3),
                                                   "evidencia": "", "acao": "", "ultima_partida": None})
                a["vezes"] += 1
                a["prioridade"] = min(a["prioridade"], d.get("prioridade", 3))
                a["evidencia"], a["acao"], a["ultima_partida"] = d.get("evidencia", ""), d.get("acao", ""), r["id"]
    lista = sorted(agreg.values(), key=lambda x: (-x["vezes"], x["prioridade"]))
    for a in lista:
        a["total_partidas"] = total
    return lista


# --------------------------------------------------------------------------
# Configuracoes do CS2 (leituras ao longo do tempo)
# --------------------------------------------------------------------------
def salvar_config(conf: dict):
    """Guarda a leitura. Retorna (id, mudou): so cria linha nova se algo mudou desde a ultima leitura."""
    plano = json.dumps(_plano(conf), sort_keys=True, ensure_ascii=False)
    with _db() as con:
        ult = con.execute("SELECT id, json FROM config_leituras ORDER BY id DESC LIMIT 1").fetchone()
        if ult:
            try:
                if json.dumps(_plano(json.loads(ult["json"])), sort_keys=True, ensure_ascii=False) == plano:
                    return ult["id"], False
            except ValueError:
                pass
        cur = con.execute("INSERT INTO config_leituras(lido_em, json) VALUES(?,?)",
                          (conf.get("lido_em") or _agora(), json.dumps(conf, ensure_ascii=False)))
        return cur.lastrowid, True


def _plano(conf: dict) -> dict:
    """Pares nome->valor de todas as configuracoes (para comparar leituras)."""
    out = {}
    for grupo in ("convars", "video", "teclas", "autoexec"):
        for k, v in (conf.get(grupo) or {}).items():
            out[f"{grupo}:{k}"] = v.get("valor") if isinstance(v, dict) else v
    return out


def ultimas_configs(n: int = 10) -> list:
    with _db() as con:
        rows = con.execute("SELECT id, lido_em, json FROM config_leituras ORDER BY id DESC LIMIT ?", (int(n),)).fetchall()
    out = []
    for r in rows:
        try:
            c = json.loads(r["json"])
        except ValueError:
            continue
        c["id"], c["lido_em"] = r["id"], r["lido_em"]
        c["vigente_desde"] = vigente_desde(c)
        out.append(c)
    return out


def vigente_desde(conf: dict) -> str:
    """Desde quando esta configuracao vale: a data do arquivo de config mais recente (limitada a hora da leitura)."""
    lido = (conf.get("lido_em") or "")[:16]
    mods = [a.get("modificado") for a in conf.get("arquivos", []) if a.get("modificado")]
    vd = max(mods) if mods else lido
    if lido and vd > lido:
        vd = lido
    return vd


def configs_cronologicas() -> list:
    """Todas as leituras guardadas, da mais antiga para a mais nova."""
    return sorted(ultimas_configs(1000), key=lambda c: (c["vigente_desde"], c["id"]))


def escolher_config(cron: list, momento):
    """A configuracao que valia no momento dado (None se nao ha registro tao antigo)."""
    m = (momento or "")[:16]
    achada = None
    for c in cron:
        if c["vigente_desde"] <= m:
            achada = c
    return achada


def momentos_partidas() -> dict:
    """Quando cada partida foi jogada: o inicio da sessao ao vivo ligada (preciso) ou a data da demo (aproximado)."""
    with _db() as con:
        rows = con.execute("SELECT p.id AS id, p.data_demo AS d, s.iniciado AS i FROM partidas p "
                           "LEFT JOIN sessoes s ON s.partida_id = p.id").fetchall()
    return {r["id"]: {"momento": r["i"] or r["d"], "preciso": bool(r["i"])} for r in rows}


def armas_de_partidas(ids) -> list:
    ids = [int(i) for i in ids]
    if not ids:
        return []
    q = ",".join("?" * len(ids))
    with _db() as con:
        return [dict(r) for r in con.execute(
            f"SELECT arma, SUM(tiros) AS tiros, SUM(tiros_com_acerto) AS tiros_com_acerto, SUM(dano) AS dano, "
            f"SUM(kills) AS kills FROM armas_partida WHERE partida_id IN ({q}) GROUP BY arma", ids)]


# --------------------------------------------------------------------------
# Sessoes do painel ao vivo
# --------------------------------------------------------------------------
def importar_sessoes(pasta) -> int:
    pasta = Path(pasta)
    if not pasta.exists():
        return 0
    novas = 0
    with _db() as con:
        for arq in sorted(pasta.glob("sessao_*.json")):
            if con.execute("SELECT 1 FROM sessoes WHERE arquivo=?", (arq.name,)).fetchone():
                continue
            try:
                d = json.loads(arq.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            partes = arq.stem.split("_")  # sessao_YYYYMMDD_HHMMSS_mapa
            try:
                iniciado = datetime.strptime(partes[1] + partes[2], "%Y%m%d%H%M%S").strftime("%Y-%m-%d %H:%M:%S")
            except (IndexError, ValueError):
                iniciado = _agora()
            con.execute("INSERT INTO sessoes(arquivo, mapa, modo, modo_nome, iniciado, kills, mortes, json)"
                        " VALUES(?,?,?,?,?,?,?,?)",
                        (arq.name, d.get("mapa"), d.get("modo"), d.get("modo_nome"), iniciado,
                         d.get("kills"), d.get("mortes"), json.dumps(d, ensure_ascii=False)))
            novas += 1
    vincular_sessoes()
    return novas


def listar_sessoes(limite: int = 100) -> list:
    with _db() as con:
        return [dict(r) for r in con.execute(
            "SELECT id, arquivo, mapa, modo, modo_nome, iniciado, kills, mortes, partida_id FROM sessoes "
            "ORDER BY iniciado DESC, id DESC LIMIT ?", (int(limite),))]


def obter_sessao(sid: int):
    with _db() as con:
        r = con.execute("SELECT id, arquivo, mapa, modo, modo_nome, iniciado, kills, mortes, partida_id, json FROM sessoes WHERE id=?",
                        (int(sid),)).fetchone()
    if not r:
        return None
    d = dict(r)
    d["dados"] = json.loads(d.pop("json") or "{}")
    return d


# --------------------------------------------------------------------------
# Vinculo sessao ao vivo <-> demo
# --------------------------------------------------------------------------
def _dt(s):
    for f in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.strptime(str(s)[:19], f)
        except (TypeError, ValueError):
            continue
    return None


def vincular_sessoes() -> int:
    """
    Liga cada sessao ao vivo sem demo a UMA partida de demo: mesmo mapa e mesmo numero de kills e mortes
    (ou, se a sessao comecou no meio da partida, kills/mortes menores ou iguais e demo logo depois).
    A demo e baixada depois do jogo, entao ela precisa ser posterior ao inicio da sessao.
    Sessao ligada deixa de ser somada nas armas (a demo ja traz tudo) e vira complemento da partida.
    Devolve quantos vinculos novos foram feitos.
    """
    novos = 0
    with _db() as con:
        con.execute("UPDATE sessoes SET partida_id=NULL WHERE partida_id IS NOT NULL "
                    "AND partida_id NOT IN (SELECT id FROM partidas)")
        partidas = [dict(r) for r in con.execute("SELECT id, mapa, data_demo, kills, mortes FROM partidas")]
        usadas = {r[0] for r in con.execute("SELECT partida_id FROM sessoes WHERE partida_id IS NOT NULL")}
        sess = [dict(r) for r in con.execute(
            "SELECT id, mapa, iniciado, kills, mortes FROM sessoes WHERE partida_id IS NULL ORDER BY iniciado")]
        for s in sess:
            if s["kills"] is None or s["mortes"] is None or not s["mapa"]:
                continue
            ini = _dt(s["iniciado"])
            melhor = None
            for p in partidas:
                if p["id"] in usadas or p["mapa"] != s["mapa"]:
                    continue
                d = _dt(p["data_demo"])
                if not ini or not d:
                    continue
                horas = (d - ini).total_seconds() / 3600.0
                exato = s["kills"] == p["kills"] and s["mortes"] == p["mortes"]
                if exato and -3 <= horas <= 24 * 14:
                    chave = (0, abs(horas))
                elif (s["kills"] + s["mortes"] > 0 and s["kills"] <= p["kills"] and s["mortes"] <= p["mortes"]
                      and 0 <= horas <= 8):
                    chave = (1, abs(horas))
                else:
                    continue
                if melhor is None or chave < melhor[0]:
                    melhor = (chave, p["id"])
            if melhor:
                con.execute("UPDATE sessoes SET partida_id=? WHERE id=?", (melhor[1], s["id"]))
                usadas.add(melhor[1])
                novos += 1
    return novos


def desvincular_sessao(sid: int) -> None:
    with _db() as con:
        con.execute("UPDATE sessoes SET partida_id=NULL WHERE id=?", (int(sid),))


def sessao_da_partida(pid: int):
    with _db() as con:
        r = con.execute("SELECT id FROM sessoes WHERE partida_id=?", (int(pid),)).fetchone()
    return obter_sessao(r["id"]) if r else None


def contar_sessoes_sem_demo() -> int:
    contam = sorted(modos_que_contam())
    if not contam:
        return 0
    with _db() as con:
        return con.execute("SELECT COUNT(*) FROM sessoes WHERE partida_id IS NULL AND modo IN ("
                           + ",".join("?" * len(contam)) + ")", contam).fetchone()[0]


# --------------------------------------------------------------------------
# Resultados completos (para evolucao, notas e plano de treino)
# --------------------------------------------------------------------------
def resultados(steamid=None, mapa=None) -> list:
    """Partidas em ordem cronologica, cada uma com o resultado completo ja lido: [{id, mapa, data_demo, res}]."""
    sql = "SELECT id, mapa, data_demo, resultado_json FROM partidas WHERE 1=1"
    par = []
    if steamid:
        sql += " AND steamid=?"
        par.append(str(steamid))
    if mapa:
        sql += " AND mapa=?"
        par.append(mapa)
    sql += " ORDER BY data_demo, id"
    out = []
    with _db() as con:
        for r in con.execute(sql, par):
            try:
                res = json.loads(r["resultado_json"] or "{}")
            except ValueError:
                continue
            out.append({"id": r["id"], "mapa": r["mapa"], "data_demo": r["data_demo"], "res": res})
    return out
