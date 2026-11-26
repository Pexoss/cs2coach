# -*- coding: utf-8 -*-
"""
Leitor das configuracoes do CS2 (somente leitura) + sugestoes de alteracao.

Le, sem modificar nada, os arquivos que o proprio jogo grava:
  Steam\\userdata\\<conta>\\730\\local\\cfg\\
      cs2_user_convars_*.vcfg    (sensibilidade, mira, volume, fps_max...)
      cs2_user_keys_*.vcfg       (teclas)
      cs2_machine_convars*.vcfg  (configuracoes da maquina)
      cs2_video.txt              (resolucao, V-Sync, Reflex...)
  Steam\\steamapps\\common\\Counter-Strike Global Offensive\\game\\csgo\\cfg\\
      autoexec.cfg, config.cfg   (se voce usa)

As sugestoes sao pistas baseadas em praticas comuns, nao regras: teste uma mudanca por vez
e compare no Hub as suas partidas antes e depois.
"""
from __future__ import annotations

import os
import re
import sys
from datetime import datetime
from pathlib import Path

ID64_BASE = 76561197960265728
PASTA_JOGO = Path("steamapps") / "common" / "Counter-Strike Global Offensive" / "game" / "csgo" / "cfg"


def conta_para_steamid64(conta_id) -> str:
    return str(ID64_BASE + int(conta_id))


# --------------------------------------------------------------------------
# Onde estao os arquivos
# --------------------------------------------------------------------------
def bases_steam() -> list:
    bases = []
    if sys.platform.startswith("win"):
        try:
            import winreg  # type: ignore
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam") as k:
                bases.append(Path(winreg.QueryValueEx(k, "SteamPath")[0]))
        except Exception:
            pass
        for var in ("PROGRAMFILES(X86)", "PROGRAMFILES"):
            v = os.environ.get(var)
            if v:
                bases.append(Path(v) / "Steam")
        bases += [Path("C:/Program Files (x86)/Steam"), Path("D:/Steam"), Path("E:/Steam")]
    else:
        home = Path.home()
        bases += [home / ".steam" / "steam", home / ".local" / "share" / "Steam",
                  home / "Library" / "Application Support" / "Steam"]
    unicos, vistos = [], set()
    for b in bases:
        if str(b) not in vistos and b.exists():
            vistos.add(str(b))
            unicos.append(b)
    return unicos


def bibliotecas_steam() -> list:
    libs = list(bases_steam())
    for b in list(libs):
        vdf = b / "steamapps" / "libraryfolders.vdf"
        if vdf.exists():
            try:
                for m in re.finditer(r'"path"\s+"([^"]+)"', vdf.read_text(encoding="utf-8", errors="ignore")):
                    libs.append(Path(m.group(1).replace("\\\\", "\\")))
            except OSError:
                pass
    unicos, vistos = [], set()
    for l in libs:
        if str(l) not in vistos:
            vistos.add(str(l))
            unicos.append(l)
    return unicos


def _mtime_pasta(p: Path) -> float:
    try:
        return max((f.stat().st_mtime for f in p.iterdir() if f.is_file()), default=p.stat().st_mtime)
    except OSError:
        return 0.0


def achar_userdata() -> list:
    """Contas do Steam com pasta de configuracao do CS2, a mais recente primeiro."""
    achadas = []
    for b in bases_steam():
        ud = b / "userdata"
        if not ud.is_dir():
            continue
        for d in ud.iterdir():
            if d.is_dir() and d.name.isdigit() and d.name != "0":
                cfg = d / "730" / "local" / "cfg"
                if cfg.is_dir():
                    achadas.append({"conta": d.name, "steamid64": conta_para_steamid64(d.name),
                                    "pasta": cfg, "modificado": _mtime_pasta(cfg)})
    achadas.sort(key=lambda x: -x["modificado"])
    return achadas


def detectar_steamid():
    """SteamID64 da conta usada mais recentemente neste PC (ou None)."""
    a = achar_userdata()
    return a[0]["steamid64"] if a else None


def achar_pasta_jogo_cfg():
    for lib in bibliotecas_steam():
        p = lib / PASTA_JOGO
        if p.is_dir():
            return p
    return None


def conta_da_pasta(pasta) -> str | None:
    """Extrai o numero da conta de um caminho ...\\userdata\\<conta>\\730\\local\\cfg."""
    partes = [x for x in re.split(r"[\\/]", str(pasta)) if x]
    for i, parte in enumerate(partes):
        if parte.lower() == "userdata" and i + 1 < len(partes) and partes[i + 1].isdigit():
            return partes[i + 1]
    return None


# --------------------------------------------------------------------------
# Leitura dos formatos
# --------------------------------------------------------------------------
_TOKEN = re.compile(r'//[^\n]*|"((?:[^"\\]|\\.)*)"|([{}])')


def parse_kv(texto: str) -> dict:
    """Parser do formato KeyValues da Valve:  "chave" "valor"  e  "chave" { ... }."""
    toks = []
    for m in _TOKEN.finditer(texto):
        if m.group(0).startswith("//"):
            continue
        toks.append(("s", m.group(1)) if m.group(1) is not None else ("b", m.group(2)))
    pos = 0

    def bloco(raiz=False):
        nonlocal pos
        d = {}
        while pos < len(toks):
            tipo, val = toks[pos]
            if tipo == "b":
                pos += 1
                if val == "}":
                    return d
                continue
            chave = val
            pos += 1
            if pos >= len(toks):
                break
            tipo2, val2 = toks[pos]
            if tipo2 == "b" and val2 == "{":
                pos += 1
                d[chave] = bloco()
            elif tipo2 == "s":
                d[chave] = val2
                pos += 1
        return d

    return bloco(raiz=True)


def _folhas(d: dict):
    for k, v in d.items():
        if isinstance(v, dict):
            yield from _folhas(v)
        else:
            yield k, v


def _secoes(d: dict, nome: str):
    """Todos os sub-blocos cujo nome (sem diferenciar maiusculas) e `nome`."""
    for k, v in d.items():
        if isinstance(v, dict):
            if k.lower() == nome:
                yield v
            yield from _secoes(v, nome)


def _pares(d: dict, secao: str) -> dict:
    blocos = list(_secoes(d, secao))
    if blocos:
        return {k: v for b in blocos for k, v in _folhas(b)}
    return dict(_folhas(d))


def _dividir_comandos(linha: str) -> list:
    """Separa por ';' fora de aspas."""
    out, atual, aspas = [], [], False
    for ch in linha:
        if ch == '"':
            aspas = not aspas
        if ch == ";" and not aspas:
            out.append("".join(atual))
            atual = []
        else:
            atual.append(ch)
    out.append("".join(atual))
    return [c.strip() for c in out if c.strip()]


def _tokens_cmd(cmd: str) -> list:
    return [a or b for a, b in re.findall(r'"([^"]*)"|(\S+)', cmd)]


def parse_cfg(texto: str):
    """Le um .cfg de linhas de comando. Devolve (convars, binds)."""
    convars, binds = {}, {}
    for linha in texto.splitlines():
        linha = re.sub(r"//.*$", "", linha).strip()
        if not linha:
            continue
        for cmd in _dividir_comandos(linha):
            t = _tokens_cmd(cmd)
            if not t:
                continue
            nome = t[0].lower()
            if nome == "bind" and len(t) >= 3:
                binds[t[1]] = " ".join(t[2:])
            elif nome in ("alias", "exec", "echo", "unbind", "unbindall", "host_writeconfig", "say", "playdemo",
                          "connect", "map", "disconnect", "toggle", "incrementvar", "cycle"):
                continue
            elif len(t) >= 2 and re.fullmatch(r"[a-z_][a-z0-9_.]*", nome):
                convars[nome] = t[1]
    return convars, binds


def _ler_texto(p: Path) -> str:
    for enc in ("utf-8-sig", "utf-8", "latin-1"):
        try:
            return p.read_text(encoding=enc)
        except UnicodeDecodeError:
            continue
        except OSError:
            return ""
    return ""


def _mais_recente(pasta: Path, padrao: str):
    cands = [p for p in pasta.glob(padrao) if p.is_file() and not p.name.lower().endswith((".bak", ".old"))]
    return max(cands, key=lambda p: p.stat().st_mtime, default=None)


def ler_configuracoes(pasta_usuario=None, pasta_jogo=None) -> dict:
    """
    Le tudo e devolve um dicionario:
      convars / video / teclas / autoexec: {nome: {"valor": ..., "origem": arquivo}}
      arquivos: lista dos arquivos lidos;  avisos: o que nao foi encontrado
    Sem argumentos, procura sozinho (conta Steam mais recente).
    """
    avisos = []
    conta = None
    if pasta_usuario:
        pasta_usuario = Path(pasta_usuario)
        conta = conta_da_pasta(pasta_usuario)
    else:
        achadas = achar_userdata()
        if achadas:
            pasta_usuario, conta = achadas[0]["pasta"], achadas[0]["conta"]
            if len(achadas) > 1:
                avisos.append(f"Há {len(achadas)} contas do Steam neste PC; usei a mais recente ({conta}). "
                              "Você pode escolher a pasta certa em Configurações > Ajustes.")
        else:
            avisos.append("Não encontrei a pasta userdata do Steam. Informe o caminho em Ajustes "
                          "(algo como ...\\Steam\\userdata\\<número>\\730\\local\\cfg).")
    pasta_jogo = Path(pasta_jogo) if pasta_jogo else achar_pasta_jogo_cfg()

    conf = {"lido_em": datetime.now().strftime("%Y-%m-%d %H:%M:%S"), "conta": conta,
            "steamid64": conta_para_steamid64(conta) if conta else None,
            "pasta_usuario": str(pasta_usuario) if pasta_usuario else None,
            "pasta_jogo": str(pasta_jogo) if pasta_jogo else None,
            "arquivos": [], "convars": {}, "video": {}, "teclas": {}, "autoexec": {}, "avisos": avisos}

    def registrar(p: Path):
        conf["arquivos"].append({"nome": p.name, "pasta": str(p.parent),
                                 "modificado": datetime.fromtimestamp(p.stat().st_mtime).strftime("%Y-%m-%d %H:%M")})
        return datetime.fromtimestamp(p.stat().st_mtime).strftime("%Y-%m-%d %H:%M:%S")

    if pasta_usuario and Path(pasta_usuario).is_dir():
        pu = Path(pasta_usuario)
        for padrao, destino in (("cs2_machine_convars*.vcfg", "convars"), ("cs2_user_convars*.vcfg", "convars")):
            arq = _mais_recente(pu, padrao)
            if arq:
                mod = registrar(arq)
                for k, v in _pares(parse_kv(_ler_texto(arq)), "convars").items():
                    conf[destino][k.lower()] = {"valor": v, "origem": arq.name, "mod": mod}
        arq = _mais_recente(pu, "cs2_user_keys*.vcfg")
        if arq:
            registrar(arq)
            for k, v in _pares(parse_kv(_ler_texto(arq)), "bindings").items():
                conf["teclas"][k] = {"valor": v, "origem": arq.name}
        arq = pu / "cs2_video.txt"
        if arq.exists():
            registrar(arq)
            for k, v in _folhas(parse_kv(_ler_texto(arq))):
                conf["video"][k.lower()] = {"valor": v, "origem": arq.name}
        for nome in ("autoexec.cfg", "config.cfg"):
            arq = pu / nome
            if arq.exists():
                mod = registrar(arq)
                cv, bd = parse_cfg(_ler_texto(arq))
                for k, v in cv.items():
                    conf["autoexec"][k] = {"valor": v, "origem": nome, "mod": mod}
                for k, v in bd.items():
                    conf["teclas"].setdefault(k, {"valor": v, "origem": nome})
        if not conf["arquivos"]:
            avisos.append(f"A pasta {pu} não tem os arquivos de configuração do CS2 esperados.")
    elif pasta_usuario:
        avisos.append(f"A pasta {pasta_usuario} não existe.")

    if pasta_jogo and Path(pasta_jogo).is_dir():
        for nome in ("autoexec.cfg",):
            arq = Path(pasta_jogo) / nome
            if arq.exists():
                mod = registrar(arq)
                cv, bd = parse_cfg(_ler_texto(arq))
                for k, v in cv.items():
                    conf["autoexec"][k] = {"valor": v, "origem": "jogo/" + nome, "mod": mod}
                for k, v in bd.items():
                    conf["teclas"].setdefault(k, {"valor": v, "origem": "jogo/" + nome})
    return conf


# --------------------------------------------------------------------------
# Consulta e resumo
# --------------------------------------------------------------------------
def obter(conf: dict, nome: str, padrao=None):
    """Valor efetivo: vale o que foi gravado por ultimo. O autoexec roda ao abrir o jogo, mas uma mudanca feita
    depois nas opcoes do jogo (arquivo salvo mais tarde) passa a valer por cima dele."""
    nome = nome.lower()
    melhor, melhor_mod = None, None
    for grupo in ("autoexec", "convars", "video"):
        v = (conf.get(grupo) or {}).get(nome)
        if v is None:
            continue
        mod = (v.get("mod") or "") if isinstance(v, dict) else ""
        if melhor is None or mod > melhor_mod:        # empate: o primeiro grupo (autoexec) vence
            melhor, melhor_mod = v, mod
    if melhor is None:
        return padrao
    return melhor["valor"] if isinstance(melhor, dict) else melhor


def _num(v):
    try:
        return float(str(v).replace(",", "."))
    except (TypeError, ValueError):
        return None


def resumo(conf: dict, ajustes: dict) -> dict:
    """Numeros-chave: eDPI, distancia de 360 graus, resolucao, taxa de atualizacao, FPS maximo etc."""
    r = {}
    sens = _num(obter(conf, "sensitivity"))
    r["sens_origem"] = "arquivos do jogo"
    if sens is None and _num(ajustes.get("sens")):
        sens = _num(ajustes.get("sens"))          # nao achei nos arquivos: usa o que voce informou em Ajustes
        r["sens_origem"] = "Ajustes (não achei nos arquivos do jogo)"
    dpi = _num(ajustes.get("dpi"))
    yaw = _num(obter(conf, "m_yaw")) or 0.022
    r["sensibilidade"] = sens
    r["dpi"] = dpi
    if sens and dpi:
        r["edpi"] = round(sens * dpi)
        r["cm_360"] = round(2.54 * 360.0 / (yaw * sens * dpi), 1)
    r["zoom_ratio"] = obter(conf, "zoom_sensitivity_ratio", obter(conf, "zoom_sensitivity_ratio_mouse"))
    zr = _num(r["zoom_ratio"])
    if zr is not None and sens:
        r["sens_zoom"] = round(sens * zr, 3)
        if dpi:
            r["edpi_zoom"] = round(sens * zr * dpi)
    w, h = _num(obter(conf, "setting.defaultres")), _num(obter(conf, "setting.defaultresheight"))
    if w and h:
        r["resolucao"] = f"{int(w)}x{int(h)}"
    num, den = _num(obter(conf, "setting.refreshrate_numerator")), _num(obter(conf, "setting.refreshrate_denominator"))
    if num:
        hz = num / den if den else num
        if 20 <= hz <= 600:
            r["taxa_jogo_hz"] = round(hz)
    r["hz_monitor"] = _num(ajustes.get("hz"))
    r["fps_max"] = _num(obter(conf, "fps_max"))
    r["vsync"] = obter(conf, "setting.mat_vsync")
    r["reflex"] = obter(conf, "setting.r_low_latency")
    r["mira_estilo"] = obter(conf, "cl_crosshairstyle")
    r["mira_tamanho"] = obter(conf, "cl_crosshairsize")
    r["viewmodel_fov"] = obter(conf, "viewmodel_fov")
    r["volume"] = obter(conf, "volume")
    return r


# --------------------------------------------------------------------------
# Sugestoes
# --------------------------------------------------------------------------
def _tip(i, prio, cat, titulo, atual, sugestao, porque, como=None):
    return {"id": i, "prioridade": prio, "categoria": cat, "titulo": titulo, "atual": atual,
            "sugestao": sugestao, "porque": porque, "como": como}


def sugestoes(conf: dict, ajustes: dict, hist=None, armas=None):
    """Devolve (sugestoes, pontos_ok, resumo). So comenta o que encontrou no arquivo.
    Com hist (historico das partidas) e armas (ranking) tambem cruza a configuracao com o seu desempenho."""
    r = resumo(conf, ajustes)
    tips, ok = [], []

    edpi = r.get("edpi")
    if edpi:
        cm = r.get("cm_360")
        base = f"eDPI {edpi} (sens {r['sensibilidade']} × {int(r['dpi'])} DPI) = {cm} cm para girar 360°"
        if edpi > 1400:
            alvo = round(1000.0 / r["dpi"], 2)
            tips.append(_tip("sens_alta", 2, "Mouse", "Sensibilidade alta", base,
                             "Testar 800–1200 de eDPI por uma semana",
                             "Sensibilidade alta torna mais difícil estabilizar a mira e fazer correções finas; "
                             "a maioria dos profissionais joga entre 600 e 1200. Mude só isso e compare no Hub.",
                             f"sensitivity {alvo}"))
        elif edpi < 400:
            tips.append(_tip("sens_baixa", 3, "Mouse", "Sensibilidade muito baixa", base,
                             "Testar 600–1000 de eDPI",
                             "Com sensibilidade muito baixa é preciso mover muito o braço para girar, o que atrapalha "
                             "reagir a inimigos fora da mira. Se você tem muito espaço no mousepad, pode ser intencional.",
                             f"sensitivity {round(800.0 / r['dpi'], 2)}"))
        else:
            ok.append(f"{base}: dentro da faixa comum (600–1200). Evite trocar de sensibilidade com frequência; "
                      "a constância ajuda a memória muscular.")

    accel = _num(obter(conf, "m_customaccel"))
    if accel not in (None, 0.0):
        tips.append(_tip("aceleracao", 1, "Mouse", "Aceleração do mouse ligada", f"m_customaccel {obter(conf, 'm_customaccel')}",
                         "Desligar a aceleração",
                         "Com aceleração, o mesmo movimento de mão gira a mira de formas diferentes conforme a velocidade, "
                         "o que prejudica a constância da mira.", "m_customaccel 0"))

    fps = r.get("fps_max")
    hz = r.get("hz_monitor")
    if fps is not None:
        if fps == 0:
            ok.append("fps_max 0 (sem limite): bom para reduzir a latência.")
        elif hz and fps < hz:
            tips.append(_tip("fps_baixo", 1, "Desempenho", "FPS máximo abaixo da taxa do monitor", f"fps_max {int(fps)} (monitor {int(hz)} Hz)",
                             "Usar fps_max 0 (sem limite)",
                             "Limitar o FPS abaixo da taxa do monitor desperdiça os Hz do monitor e aumenta o atraso da imagem.",
                             "fps_max 0"))

    vs = str(r.get("vsync") or "").lower()
    if vs in ("1", "true", "2"):
        tips.append(_tip("vsync", 1, "Vídeo", "V-Sync ligado", f"mat_vsync {r['vsync']}",
                         "Desligar o V-Sync",
                         "O V-Sync adiciona atraso entre o movimento do mouse e a tela. Se houver rasgos na imagem, "
                         "prefira G-Sync/FreeSync (se o monitor suportar).", "Vídeo avançado > Aguardar sincronização vertical: Desativado"))
    elif vs in ("0", "false"):
        ok.append("V-Sync desligado: bom para reduzir a latência.")

    rf = str(r.get("reflex") or "")
    if rf == "0":
        tips.append(_tip("reflex", 2, "Vídeo", "NVIDIA Reflex desligado", "setting.r_low_latency 0",
                         "Ligar o NVIDIA Reflex (se sua placa de vídeo for NVIDIA)",
                         "O Reflex reduz o atraso do sistema entre o clique e a tela. Em placas que não são NVIDIA a opção não existe.",
                         "Vídeo avançado > NVIDIA Reflex Low Latency: Ativado (ou Ativado + Boost)"))
    elif rf in ("1", "2"):
        ok.append("NVIDIA Reflex ligado.")

    taxa = r.get("taxa_jogo_hz")
    if taxa and hz and taxa + 1 < hz:
        tips.append(_tip("taxa_hz", 1, "Vídeo", "O jogo não está usando a taxa do monitor", f"Jogo em {taxa} Hz, monitor de {int(hz)} Hz",
                         f"Escolher {int(hz)} Hz no menu de vídeo do CS2 e nas configurações de tela do Windows",
                         "Com taxa menor, você perde fluidez e vê o inimigo mais tarde.", None))
    elif taxa and hz and abs(taxa - hz) <= 1:
        ok.append(f"O jogo está em {taxa} Hz, igual ao monitor.")

    estilo = str(r.get("mira_estilo") or "")
    if estilo in ("0", "3"):
        tips.append(_tip("mira_dinamica", 3, "Mira", "Mira dinâmica (abre ao andar e atirar)", f"cl_crosshairstyle {estilo}",
                         "Experimentar uma mira estática",
                         "Uma mira que se abre e fecha pode distrair; muitos jogadores preferem estática para manter o foco "
                         "no ponto de mira. É preferência pessoal: teste e veja se a precisão muda.", "cl_crosshairstyle 4"))

    pv = str(obter(conf, "r_player_visibility_mode") or "")
    if pv == "0":
        tips.append(_tip("contraste", 3, "Vídeo", "Contraste dos jogadores desligado", "r_player_visibility_mode 0",
                         "Experimentar 'Aumentar contraste dos jogadores'",
                         "Realça a silhueta dos jogadores e pode ajudar a notar inimigos mais cedo, principalmente "
                         "em locais escuros. O efeito varia por mapa; teste e veja se incomoda.",
                         "Vídeo > Aumentar contraste dos jogadores: Ativado"))

    if hist is not None:
        dt, dok = sugestoes_dados(conf, r, hist, armas or [])
        tips += dt
        ok += dok
        if any(t["id"] == "dados_sens" for t in dt):     # evita dizer "tudo certo" e "reduza" ao mesmo tempo
            ok = [o for o in ok if not o.startswith("eDPI")]
    tips.sort(key=lambda t: (t["prioridade"], 0 if t.get("dados") else 1))
    return tips, ok, r


def _media_hist(hist, chave, n=10):
    v = [h[chave] for h in hist[-n:] if isinstance(h.get(chave), (int, float))]
    return (sum(v) / len(v), len(v)) if v else (None, 0)


def sugestoes_dados(conf: dict, r: dict, hist: list, armas: list):
    """Cruza as configuracoes com as suas ultimas partidas. Precisa de pelo menos 3 partidas."""
    tips, ok = [], []
    if len(hist) < 3:
        return tips, ok
    n = min(len(hist), 10)
    base = f"Baseado nas suas últimas {n} partidas"

    def dado(i, prio, cat, titulo, atual, sug, porque, como=None):
        t = _tip(i, prio, cat, titulo, atual, sug, porque, como)
        t["dados"] = True
        t["base"] = base
        return t

    parado, _ = _media_hist(hist, "rifle_acerto_parado")
    edpi = r.get("edpi")
    if parado is not None and edpi:
        if parado < 40 and edpi >= 1000:
            tips.append(dado("dados_sens", 2, "Mouse", "Errando tiros parado com sensibilidade na faixa alta",
                             f"Rifle parado: {parado:.0f}% dos tiros acertam · eDPI {edpi}",
                             "Experimentar reduzir a sensibilidade em cerca de 10–15% por 1–2 semanas",
                             "Quando você erra mesmo parado e a sensibilidade é alta, é comum a mira passar do alvo "
                             "(o mouse anda demais num micro-ajuste). Reduzir um pouco costuma dar mais controle. "
                             "O Hub vai comparar as suas partidas antes e depois da mudança.",
                             f"sensitivity {round(r['sensibilidade'] * 0.87, 2)}"))
        elif parado >= 55:
            ok.append(f"Acerto de rifle parado em {parado:.0f}% nas últimas partidas: a sua sensibilidade (eDPI {edpi}) "
                      "parece não estar atrapalhando a precisão. Evite mexer nela.")
        elif parado < 40:
            tips.append(dado("dados_sens2", 3, "Mouse", "Acerto parado baixo",
                             f"Rifle parado: {parado:.0f}% dos tiros acertam · eDPI {edpi}",
                             "Antes de mexer na sensibilidade, treinar posicionamento da mira e tiros em toques",
                             "Com a sua sensibilidade na faixa comum, o problema tende a estar mais na técnica "
                             "(altura da mira, parar antes de atirar) do que no ajuste do mouse.", None))

    sem_atirar, _ = _media_hist(hist, "morreu_sem_atirar")
    if sem_atirar is not None and sem_atirar >= 25:
        pv = str(obter(conf, "r_player_visibility_mode") or "")
        extra = " O contraste dos jogadores está desligado nas suas configurações." if pv == "0" else ""
        tips.append(dado("dados_semtiro", 2 if pv == "0" else 3, "Vídeo", "Muitas mortes sem conseguir atirar",
                         f"{sem_atirar:.0f}% das suas mortes acontecem sem você disparar nada.{extra}",
                         "Testar o contraste dos jogadores e conferir o brilho; treinar mirar nos cantos antes de virar",
                         "Morrer sem atirar costuma ser ver o inimigo tarde ou se assustar. Ajuda realçar a silhueta dos "
                         "jogadores e deixar a mira já posicionada no ângulo.",
                         "Vídeo > Aumentar contraste dos jogadores: Ativado"))

    spray, _ = _media_hist(hist, "spray_pct")
    estilo = str(r.get("mira_estilo") or "")
    if spray is not None and spray >= 35 and estilo in ("0", "3"):
        tips.append(dado("dados_mira", 3, "Mira", "Muito spray com mira que se abre",
                         f"{spray:.0f}% das suas balas saem em sequências longas · mira dinâmica (estilo {estilo})",
                         "Experimentar mira estática enquanto treina rajadas curtas",
                         "A mira dinâmica abre quando você atira e pode esconder o ponto real do tiro; ficar com a mira "
                         "parada ajuda a ver se o seu controle está melhorando.", "cl_crosshairstyle 4"))

    awp = next((a for a in armas if a.get("arma") == "awp"), None)
    if awp and (awp.get("tiros_demo") or 0) >= 40 and awp.get("precisao") is not None:
        zr = _num(r.get("zoom_ratio"))
        if awp["precisao"] < 35 and zr:
            tips.append(dado("dados_awp", 3, "Mouse", "Precisão baixa com a AWP",
                             f"AWP: {awp['precisao']:.0f}% dos tiros acertam ({awp['tiros_demo']} tiros) · razão do zoom {zr}",
                             f"Como experimento, testar a razão do zoom em {round(zr * 0.85, 2)} (cerca de 15% menor)",
                             "Se a luneta parece rápida demais para ajustar fino, reduzir a razão do zoom dá mais controle. "
                             "Se você acerta pouco por causa de tiros em movimento ou sem parar, isso não resolve; "
                             "nesse caso treine parar antes de atirar.", f"zoom_sensitivity_ratio {round(zr * 0.85, 2)}"))
        elif awp["precisao"] >= 55:
            ok.append(f"AWP com {awp['precisao']:.0f}% dos tiros acertando: a razão do zoom atual está funcionando bem.")

    return tips, ok


# --------------------------------------------------------------------------
# Mudou algo? Compara leituras e o desempenho antes e depois
# --------------------------------------------------------------------------
CHAVES_INTERESSE = (
    "sensitivity", "zoom_sensitivity_ratio", "zoom_sensitivity_ratio_mouse", "m_yaw", "m_customaccel", "fps_max",
    "viewmodel_fov", "setting.defaultres", "setting.defaultresheight", "setting.mat_vsync", "setting.r_low_latency",
    "setting.refreshrate_numerator", "r_player_visibility_mode",
)


def _plano_interesse(conf: dict) -> dict:
    out = {}
    for k in CHAVES_INTERESSE:
        v = obter(conf, k)
        if v is not None:
            out[k] = v
    for grupo in ("convars", "autoexec"):
        for k, v in (conf.get(grupo) or {}).items():
            if k.startswith("cl_crosshair") or k.startswith("cl_fixedcrosshairgap"):
                out[k] = v["valor"] if isinstance(v, dict) else v
    return out


def mudancas(leituras: list) -> list:
    """
    leituras: configuracoes lidas em ordem do mais antigo para o mais novo.
    Devolve [{"de": desde_quando_a_antiga_valia, "para": desde_quando_a_nova_vale, "ate": quando_a_nova_deixou_de_valer,
              "itens": [(chave, antes, depois)]}].
    """
    out = []
    for n, (a, b) in enumerate(zip(leituras, leituras[1:])):
        pa, pb = _plano_interesse(a), _plano_interesse(b)
        itens = [(k, pa.get(k), pb.get(k)) for k in sorted(set(pa) | set(pb)) if pa.get(k) != pb.get(k)]
        if itens:
            prox = leituras[n + 2] if n + 2 < len(leituras) else None
            out.append({"de": a.get("vigente_desde") or a.get("lido_em"), "para": b.get("vigente_desde") or b.get("lido_em"),
                        "ate": (prox.get("vigente_desde") or prox.get("lido_em")) if prox else None, "itens": itens})
    return out


def diferencas(conf_a: dict, conf_b: dict) -> list:
    """O que mudou entre duas configuracoes: [(chave, antes, depois)]."""
    pa, pb = _plano_interesse(conf_a), _plano_interesse(conf_b)
    return [(k, pa.get(k), pb.get(k)) for k in sorted(set(pa) | set(pb)) if pa.get(k) != pb.get(k)]


def _media(vals):
    vals = [v for v in vals if isinstance(v, (int, float))]
    return round(sum(vals) / len(vals), 2) if vals else None


def _resumo_partidas(parts: list) -> dict:
    return {"n": len(parts), "adr": _media([p.get("adr") for p in parts]), "kd": _media([p.get("kd") for p in parts]),
            "hs": _media([p.get("hs") for p in parts]),
            "rifle_parado": _media([p.get("rifle_acerto_parado") for p in parts]),
            "ids": [p["id"] for p in parts if p.get("id") is not None]}


def antes_depois(mud: list, hist: list, minimo: int = 2) -> list:
    """
    Para cada mudanca de configuracao compara as partidas jogadas COM a configuracao antiga (entre "de" e "para")
    com as jogadas COM a nova (de "para" ate a mudanca seguinte). Cada partida pode trazer "momento" (inicio da
    sessao ao vivo ligada); sem ele usa a data da demo, que e aproximada.
    """
    def quando(p):
        return ((p.get("momento") or p.get("data_demo") or ""))[:16]

    out = []
    for m in mud:
        de, para, ate = (m["de"] or "")[:16], (m["para"] or "")[:16], (m["ate"] or "")[:16] or None
        antes = [p for p in hist if quando(p) and de <= quando(p) < para]
        depois = [p for p in hist if quando(p) >= para and (ate is None or quando(p) < ate)]
        out.append({**m, "antes": _resumo_partidas(antes), "depois": _resumo_partidas(depois),
                    "confiavel": len(antes) >= minimo and len(depois) >= minimo})
    return out


# --------------------------------------------------------------------------
# Glossario: o que significa cada configuracao (em portugues simples)
# --------------------------------------------------------------------------
GLOSSARIO = {
    "sensitivity": ("Sensibilidade do mouse", "Quanto a mira gira para cada movimento do mouse. Maior = gira mais rápido."),
    "zoom_sensitivity_ratio": ("Sensibilidade com luneta (AWP, scout, AUG...)", "Multiplica a sensibilidade quando você olha pela luneta. 1.0 = igual à base; menor = mira mais lenta e precisa."),
    "zoom_sensitivity_ratio_mouse": ("Sensibilidade com luneta (mouse)", "Mesma ideia da razão do zoom: quanto a mira gira quando você usa a luneta."),
    "m_yaw": ("Giro por movimento", "Valor interno do jogo (0.022 é o padrão). Não precisa mexer."),
    "m_pitch": ("Giro vertical", "Controla o movimento da mira para cima e para baixo. Padrão 0.022."),
    "m_customaccel": ("Aceleração do mouse", "0 = desligada (recomendado). Ligada, a mira gira diferente conforme a velocidade da mão."),
    "m_rawinput": ("Entrada direta do mouse", "1 = usa o mouse sem interferência do Windows (recomendado)."),
    "fps_max": ("Limite de FPS", "0 = sem limite. Um número limita os quadros por segundo."),
    "viewmodel_fov": ("Campo de visão da arma", "Tamanho com que a arma aparece na tela. Só visual."),
    "viewmodel_offset_x": ("Posição da arma (lado)", "Move a arma para os lados na tela. Só visual."),
    "viewmodel_offset_y": ("Posição da arma (frente)", "Move a arma para frente/trás na tela. Só visual."),
    "viewmodel_offset_z": ("Posição da arma (altura)", "Sobe ou desce a arma na tela. Só visual."),
    "viewmodel_presetpos": ("Posição pré-definida da arma", "1, 2 ou 3 escolhem modelos prontos de posição."),
    "cl_bob_lower_amt": ("Balanço da arma ao andar", "Quanto a arma balança ao se mover. Menor = menos distração."),
    "cl_bobamt_lat": ("Balanço lateral", "Balanço da arma para os lados ao andar."),
    "cl_bobamt_vert": ("Balanço vertical", "Balanço da arma para cima e baixo ao andar."),
    "cl_crosshairstyle": ("Estilo da mira", "0 e 1: padrão dinâmico, 2: clássico, 3: clássico dinâmico, 4: estático (não abre), 5: clássico estático."),
    "cl_crosshairsize": ("Tamanho da mira", "Comprimento das linhas da mira."),
    "cl_crosshairthickness": ("Espessura da mira", "Grossura das linhas."),
    "cl_crosshairgap": ("Abertura da mira", "Espaço vazio no centro da mira."),
    "cl_fixedcrosshairgap": ("Abertura da mira estática", "Espaço no centro quando a mira é estática."),
    "cl_crosshairdot": ("Ponto central", "1 = mostra um ponto no meio da mira."),
    "cl_crosshair_drawoutline": ("Contorno da mira", "1 = desenha uma borda preta para destacar a mira."),
    "cl_crosshair_outlinethickness": ("Espessura do contorno", "Grossura da borda da mira."),
    "cl_crosshaircolor": ("Cor da mira", "0 vermelho, 1 verde, 2 amarelo, 3 azul, 4 ciano, 5 personalizada."),
    "cl_crosshaircolor_r": ("Cor da mira (vermelho)", "Parte vermelha da cor personalizada (0–255)."),
    "cl_crosshaircolor_g": ("Cor da mira (verde)", "Parte verde da cor personalizada (0–255)."),
    "cl_crosshaircolor_b": ("Cor da mira (azul)", "Parte azul da cor personalizada (0–255)."),
    "cl_crosshairalpha": ("Transparência da mira", "255 = totalmente visível."),
    "cl_crosshair_t": ("Mira sem linha de cima", "1 = tira a linha de cima (mira em T)."),
    "cl_crosshair_sniper_width": ("Largura da mira da AWP", "Espessura da linha da mira quando a luneta está aberta."),
    "cl_crosshair_recoil": ("Mira acompanha o recuo", "1 = a mira se move junto com o recuo da arma."),
    "cl_crosshairgap_useweaponvalue": ("Abertura vem da arma", "1 = a abertura muda conforme a arma."),
    "cl_radar_scale": ("Tamanho do radar", "Quanto do mapa o radar mostra. Menor = mais perto."),
    "cl_hud_radar_scale": ("Tamanho do radar na tela", "Tamanho do desenho do radar."),
    "cl_radar_always_centered": ("Radar centralizado em você", "1 = você fica sempre no meio do radar."),
    "cl_radar_rotate": ("Radar gira com você", "1 = o radar gira para a direção em que você olha."),
    "cl_hud_color": ("Cor do HUD", "Cor dos números e ícones da tela."),
    "cl_showloadout": ("Mostrar armas na tela", "1 = mostra as armas que você tem no canto."),
    "cl_autohelp": ("Dicas automáticas do jogo", "0 = desliga as dicas de iniciante."),
    "cl_teamid_overhead_always": ("Nome dos aliados acima da cabeça", "1 = mostra sempre."),
    "cl_use_opens_buy_menu": ("Tecla usar abre compra", "1 = a tecla de usar também abre o menu de compra."),
    "cl_silencer_mode": ("Silenciador da M4A1-S", "Modo do silenciador (se fica ou não parte da arma)."),
    "volume": ("Volume geral", "Volume do jogo (0 a 1)."),
    "snd_headphone_pan_exponent": ("Posicionamento do som no fone", "Ajusta como o som se divide entre os lados do fone."),
    "snd_menumusic_volume": ("Volume da música do menu", "Volume da música no menu."),
    "snd_roundstart_volume": ("Volume da música de início", "Música tocada no começo do round."),
    "snd_mvp_volume": ("Volume da música de MVP", "Música do melhor jogador."),
    "snd_tensecondwarning_volume": ("Volume do aviso dos 10 segundos", "Som do aviso da bomba."),
    "voice_scale": ("Volume das vozes", "Volume dos outros jogadores no microfone."),
    "voice_enable": ("Chat de voz", "1 = ouve os jogadores."),
    "mm_dedicated_search_maxping": ("Ping máximo na busca", "Limite de ping aceito ao procurar partida."),
    "r_player_visibility_mode": ("Contraste dos jogadores", "0 = desligado, 1 = realça a silhueta dos jogadores."),
    "r_drawtracers_firstperson": ("Rastro das balas", "1 = mostra o rastro dos seus tiros."),
    "net_graph": ("Mostrador de FPS e ping", "Mostra informações de desempenho na tela."),
    "cq_netgraph": ("Mostrador de FPS e ping", "1 = mostra FPS e ping na tela."),
    "bind": ("Tecla configurada", "Uma tecla associada a uma ação."),
    "setting.defaultres": ("Largura da tela", "Resolução horizontal do jogo."),
    "setting.defaultresheight": ("Altura da tela", "Resolução vertical do jogo."),
    "setting.fullscreen": ("Tela cheia", "1 = tela cheia, 0 = janela."),
    "setting.coop_fullscreen": ("Tela cheia", "1 = tela cheia."),
    "setting.nowindowborder": ("Janela sem borda", "1 = janela sem borda."),
    "setting.aspectratiomode": ("Proporção da tela", "0 = 4:3, 1 = 16:9, 2 = 16:10."),
    "setting.refreshrate_numerator": ("Taxa de atualização do monitor", "Quantos Hz o jogo está usando."),
    "setting.refreshrate_denominator": ("Taxa de atualização (divisor)", "Detalhe interno da taxa em Hz."),
    "setting.mat_vsync": ("Sincronização vertical (V-Sync)", "0 = desligado (recomendado para menos atraso), 1 = ligado."),
    "setting.r_low_latency": ("NVIDIA Reflex", "0 = desligado, 1 = ligado, 2 = ligado + boost. Reduz o atraso em placas NVIDIA."),
    "setting.msaa_samples": ("Suavização de bordas (MSAA)", "Quantidade de suavização. 0 = sem; mais = bordas mais lisas, porém menos FPS."),
    "setting.videocfg_shadow_quality": ("Qualidade das sombras", "Maior = sombras mais bonitas e mais pesadas."),
    "setting.videocfg_texture_detail": ("Detalhe das texturas", "Qualidade das superfícies."),
    "setting.videocfg_particle_detail": ("Detalhe das partículas", "Fumaça, fogo e faíscas."),
    "setting.videocfg_ao_detail": ("Oclusão de ambiente", "Sombras suaves nos cantos. Pesa no FPS."),
    "setting.videocfg_hdr_detail": ("Faixa dinâmica (HDR)", "Qualidade de brilho e contraste."),
    "setting.videocfg_fsr_detail": ("Escalonamento FSR", "Tecnologia que aumenta FPS reduzindo a imagem interna."),
    "setting.monitor_index": ("Monitor usado", "Qual monitor mostra o jogo."),
    "setting.shaderquality": ("Qualidade dos efeitos", "Qualidade dos efeitos visuais."),
}

COMANDOS = {
    "+jump": "Pular", "+duck": "Agachar", "+use": "Usar", "+reload": "Recarregar", "+attack": "Atirar",
    "+attack2": "Atirar (botão direito/zoom)", "+sprint": "Andar devagar (shift)", "+left": "Girar para a esquerda",
    "+right": "Girar para a direita", "+forward": "Andar para frente", "+back": "Andar para trás",
    "+moveleft": "Andar para a esquerda", "+moveright": "Andar para a direita", "+lookatweapon": "Inspecionar arma",
    "drop": "Largar arma", "buymenu": "Abrir menu de compra", "slot1": "Arma principal", "slot2": "Pistola",
    "slot3": "Faca", "slot4": "Granadas", "slot5": "Bomba", "slot6": "Granadas (fumaça/flash)",
    "lastinv": "Última arma usada", "invnext": "Próxima arma", "invprev": "Arma anterior",
    "radio": "Menu de rádio", "player_ping": "Marcar posição (ping)", "toggleconsole": "Abrir o console",
    "+voicerecord": "Falar no microfone", "messagemode": "Chat para todos", "messagemode2": "Chat do time",
    "+showscores": "Mostrar placar", "autobuy": "Compra automática", "rebuy": "Comprar de novo",
    "+spray_menu": "Pichação", "buy_menu": "Menu de compra", "+jumpthrow": "Pular e arremessar",
}


def explicar(chave: str):
    """Devolve (nome amigavel, explicacao) em portugues ou (None, None) se nao conheco."""
    k = chave.lower()
    g = GLOSSARIO.get(k)
    if g:
        return g
    if k.startswith("cl_crosshair"):
        return ("Mira", "Ajuste visual da mira.")
    if k.startswith("viewmodel_"):
        return ("Posição/visual da arma", "Ajuste de como a arma aparece na tela.")
    if k.startswith("snd_") or k.startswith("voice_"):
        return ("Som", "Ajuste de volume ou áudio.")
    if k.startswith("setting.videocfg"):
        return ("Qualidade gráfica", "Ajuste de qualidade/detalhe gráfico. Mais detalhe = mais bonito, porém menos FPS.")
    if k.startswith("cl_hud") or k.startswith("hud_"):
        return ("Tela (HUD)", "Ajuste dos elementos mostrados na tela.")
    if k.startswith("cl_radar"):
        return ("Radar", "Ajuste do radar.")
    return (None, None)


def explicar_tecla(comando: str) -> str:
    c = str(comando or "").strip().strip('"')
    primeiro = c.split()[0] if c.split() else c
    return COMANDOS.get(primeiro, COMANDOS.get(c, ""))
