# -*- coding: utf-8 -*-
"""
Radar dos mapas, lido dos arquivos do PROPRIO jogo instalado no PC da pessoa (nada vem junto com o programa e nada sai
do computador): o CS2 guarda as imagens de radar dentro de pak01_dir.vpk. Aqui a gente:
  1. abre o indice do VPK (so a parte do indice, sem ler o jogo inteiro);
  2. acha 'panorama/images/overheadmaps/<mapa>_radar*.vtex_c' e 'resource/overviews/<mapa>.txt';
  3. converte a textura para PNG e guarda em  <pasta de dados>/mapas/<mapa>.png  (+ <mapa>.json com a posicao/escala).
Se algo der errado o Hub segue funcionando com o mapa de pontos e mostra o motivo em Ajustes.
"""
from __future__ import annotations

import io
import json
import re
import struct
import threading
from pathlib import Path

_LOCK = threading.Lock()
_INDICE = {"vpk": None, "mtime": None, "itens": {}}
_TENTATIVAS = {}          # mapa -> mensagem do ultimo erro (nao tenta de novo a cada pagina)

FORMATOS_BLOCO = {1: ("DXT1", 8), 2: ("DXT5", 16), 20: ("BC7", 16), 21: ("ATI2", 16), 27: ("ATI1", 8)}


# --------------------------------------------------------------------------
# VPK
# --------------------------------------------------------------------------
def _cstr(buf, pos):
    fim = buf.index(b"\x00", pos)
    return buf[pos:fim].decode("utf-8", "replace"), fim + 1


def ler_indice_vpk(caminho: Path, prefixos=("panorama/images/overheadmaps/", "resource/overviews/")) -> dict:
    """{caminho_interno: (indice_do_arquivo, deslocamento, tamanho, bytes_preload)} so dos prefixos pedidos."""
    with open(caminho, "rb") as f:
        cab = f.read(12)
        assinatura, versao, tam_arvore = struct.unpack("<III", cab)
        if assinatura != 0x55AA1234 or versao not in (1, 2):
            raise ValueError("arquivo VPK desconhecido")
        tam_cab = 12
        if versao == 2:
            f.read(16)
            tam_cab = 28
        arvore = f.read(tam_arvore)
    itens, pos = {}, 0
    while True:
        ext, pos = _cstr(arvore, pos)
        if not ext:
            break
        while True:
            pasta, pos = _cstr(arvore, pos)
            if not pasta:
                break
            while True:
                nome, pos = _cstr(arvore, pos)
                if not nome:
                    break
                _crc, pre, ind, off, tam, _term = struct.unpack_from("<IHHIIH", arvore, pos)
                pos += 18
                preload = arvore[pos:pos + pre]
                pos += pre
                pasta_ = "" if pasta == " " else pasta
                interno = (pasta_ + "/" if pasta_ else "") + nome + "." + ext
                interno = interno.replace("\\", "/").lower()
                if interno.startswith(prefixos):
                    itens[interno] = (ind, off, tam, preload)
    return {"itens": itens, "inicio_dados": tam_cab + tam_arvore}


def _ler_arquivo(vpk: Path, indice: dict, interno: str) -> bytes:
    ind, off, tam, pre = indice["itens"][interno]
    if ind == 0x7FFF:                                    # dados dentro do proprio _dir.vpk
        with open(vpk, "rb") as f:
            f.seek(indice["inicio_dados"] + off)
            return pre + f.read(tam)
    parte = vpk.with_name(vpk.name.replace("_dir.vpk", f"_{ind:03d}.vpk"))
    with open(parte, "rb") as f:
        f.seek(off)
        return pre + f.read(tam)


# --------------------------------------------------------------------------
# Textura (vtex_c)
# --------------------------------------------------------------------------
def _lz4_bloco(src: bytes, tam_saida: int) -> bytes:
    """Descompacta um bloco LZ4 (sem cabecalho de quadro)."""
    out = bytearray()
    i, n = 0, len(src)
    while i < n:
        tok = src[i]
        i += 1
        lit = tok >> 4
        if lit == 15:
            while True:
                b = src[i]
                i += 1
                lit += b
                if b != 255:
                    break
        out += src[i:i + lit]
        i += lit
        if i >= n:
            break
        desloc = src[i] | (src[i + 1] << 8)
        i += 2
        ml = tok & 15
        if ml == 15:
            while True:
                b = src[i]
                i += 1
                ml += b
                if b != 255:
                    break
        ml += 4
        ini = len(out) - desloc
        if desloc <= 0 or ini < 0:
            raise ValueError("LZ4 invalido")
        for k in range(ml):
            out.append(out[ini + k])
    if len(out) != tam_saida:
        raise ValueError("tamanho LZ4 inesperado")
    return bytes(out)


def _tamanho_mip(fmt, w, h):
    if fmt in FORMATOS_BLOCO:
        return max(1, (w + 3) // 4) * max(1, (h + 3) // 4) * FORMATOS_BLOCO[fmt][1]
    if fmt in (4, 28):
        return w * h * 4
    raise ValueError(f"formato de textura {fmt} nao suportado")


def decodificar_vtex(dados: bytes):
    """Devolve uma imagem PIL (RGBA) com o maior mip da textura."""
    from PIL import Image
    _tam, _hv, _v, off_blocos, n_blocos = struct.unpack_from("<IHHII", dados, 0)
    pos = 8 + off_blocos
    blocos = {}
    for _ in range(n_blocos):
        tipo = dados[pos:pos + 4].decode("latin1")
        off, tam = struct.unpack_from("<II", dados, pos + 4)
        blocos[tipo] = (pos + 4 + off, tam)
        pos += 12
    if "DATA" not in blocos:
        raise ValueError("textura sem bloco DATA")
    d0, dtam = blocos["DATA"]
    _ver, _flags = struct.unpack_from("<HH", dados, d0)
    w, h, _prof = struct.unpack_from("<HHH", dados, d0 + 20)
    fmt, mips = struct.unpack_from("<BB", dados, d0 + 26)
    extra_off, extra_n = struct.unpack_from("<II", dados, d0 + 36)
    tamanhos = [_tamanho_mip(fmt, max(1, w >> i), max(1, h >> i)) for i in range(mips)]
    total = sum(tamanhos)
    fim_cab = max(v[0] + v[1] for k, v in blocos.items() if k in ("RED2", "RERL", "REDI", "NTRO", "DATA", "STAT"))
    regiao = dados[len(dados) - total:] if len(dados) - total >= fim_cab else None
    bruto = None
    if regiao is not None:
        # mips guardados do menor para o maior: o maior (mip 0) fica no fim
        bruto = regiao[total - tamanhos[0]:]
        if len(bruto) != tamanhos[0]:
            bruto = None
    if bruto is None:
        bruto = _mip0_comprimido(dados, d0, extra_off, extra_n, tamanhos, fim_cab)
    if fmt in (4, 28):
        img = Image.frombytes("RGBA", (w, h), bruto)
        if fmt == 28:
            r, g, b, a = img.split()
            img = Image.merge("RGBA", (b, g, r, a))
        return img
    return _bc_para_imagem(fmt, w, h, bruto)


def _mip0_comprimido(dados, d0, extra_off, extra_n, tamanhos, fim_cab, extra_campo=36):
    """Texturas com mips em LZ4: a lista de tamanhos comprimidos fica nos dados extras (tipo 3)."""
    for i in range(extra_n):
        campo = d0 + extra_campo + extra_off + i * 12
        tipo, off, tam = struct.unpack_from("<III", dados, campo)
        if tipo != 3:
            continue
        p = campo + 4 + off
        _u, n = struct.unpack_from("<II", dados, p)
        comp = list(struct.unpack_from(f"<{n}I", dados, p + 8))
        if n != len(tamanhos) or fim_cab + sum(comp) > len(dados):
            continue
        ini = len(dados) - sum(comp)
        for ordem in (list(range(n - 1, -1, -1)), list(range(n))):      # menor->maior, ou ao contrario
            try:
                pedacos, q = {}, ini
                for k in ordem:
                    pedacos[k] = _lz4_bloco(dados[q:q + comp[ordem.index(k)]], tamanhos[k])
                    q += comp[ordem.index(k)]
                return pedacos[0]
            except Exception:
                continue
    raise ValueError("nao consegui achar os dados da imagem no arquivo")


def _bc_para_imagem(fmt, w, h, bruto):
    """Usa o leitor de DDS do Pillow (aceita DXT1/DXT5/BC5/BC4/BC7) montando um cabecalho DDS na memoria."""
    from PIL import Image
    flags = 0x1 | 0x2 | 0x4 | 0x1000 | 0x80000
    cabeca = bytearray(b"DDS ")
    nome = {1: b"DXT1", 2: b"DXT5", 20: b"DX10", 21: b"ATI2", 27: b"ATI1"}[fmt]
    cabeca += struct.pack("<7I", 124, flags, h, w, len(bruto), 0, 1)
    cabeca += b"\x00" * 44
    cabeca += struct.pack("<II4s5I", 32, 4, nome, 0, 0, 0, 0, 0)
    cabeca += struct.pack("<5I", 0x1000, 0, 0, 0, 0)
    if fmt == 20:
        cabeca += struct.pack("<5I", 98, 3, 0, 1, 0)             # BC7_UNORM, texture2D
    img = Image.open(io.BytesIO(bytes(cabeca) + bruto))
    img.load()
    return img.convert("RGBA")


# --------------------------------------------------------------------------
# Descoberta e cache
# --------------------------------------------------------------------------
def _achar_vpk(pasta_jogo_cfg=None):
    import config_cs
    cands = []
    if pasta_jogo_cfg:
        cands.append(Path(pasta_jogo_cfg))
    j = config_cs.achar_pasta_jogo_cfg()
    if j:
        cands.append(j)
    for c in cands:
        for base in (c.parent, c.parent.parent, c):                 # ...\game\csgo\cfg -> ...\game\csgo
            v = base / "pak01_dir.vpk"
            if v.is_file():
                return v
    return None


def _meta_overview(texto: str) -> dict:
    out = {}
    for chave in ("pos_x", "pos_y", "scale"):
        m = re.search(rf'"{chave}"\s+"?(-?[0-9.]+)"?', texto)
        if m:
            out[chave] = float(m.group(1))
    return out


def obter(mapa: str, pasta_dados: Path, pasta_jogo_cfg=None):
    """
    Devolve (info, erro). info = {"png": Path, "pos_x", "pos_y", "scale"} ou None; erro e um texto para mostrar.
    Na primeira vez le do jogo e guarda em <pasta_dados>/mapas; depois so usa o cache.
    """
    mapa = (mapa or "").lower()
    if not re.fullmatch(r"[a-z0-9_]{2,40}", mapa):
        return None, "nome de mapa invalido"
    pasta = Path(pasta_dados) / "mapas"
    png, js = pasta / f"{mapa}.png", pasta / f"{mapa}.json"
    if png.is_file() and js.is_file():
        try:
            return {**json.loads(js.read_text(encoding="utf-8")), "png": png}, None
        except (OSError, ValueError):
            pass
    with _LOCK:
        if mapa in _TENTATIVAS:
            return None, _TENTATIVAS[mapa]
        try:
            vpk = _achar_vpk(pasta_jogo_cfg)
            if not vpk:
                raise FileNotFoundError("não achei o arquivo pak01_dir.vpk do CS2 (a pasta do jogo está em Ajustes?)")
            m_vpk = vpk.stat().st_mtime
            if _INDICE["vpk"] != str(vpk) or _INDICE["mtime"] != m_vpk:
                _INDICE.update(vpk=str(vpk), mtime=m_vpk, **ler_indice_vpk(vpk))
            itens = _INDICE["itens"]
            cand = sorted((k for k in itens if k.startswith("panorama/images/overheadmaps/") and k.endswith(".vtex_c")
                           and re.search(rf"/{re.escape(mapa)}_radar", k)), key=lambda k: ("tutorial" in k, "lower" in k, len(k)))
            if not cand:
                raise FileNotFoundError(f"o jogo não tem radar para {mapa}")
            img = decodificar_vtex(_ler_arquivo(vpk, _INDICE, cand[0]))
            meta = {}
            ov = f"resource/overviews/{mapa}.txt"
            if ov in itens:
                meta = _meta_overview(_ler_arquivo(vpk, _INDICE, ov).decode("utf-8", "replace"))
            if len(meta) < 3:
                raise ValueError("não achei a posição/escala do radar (resource/overviews)")
            pasta.mkdir(parents=True, exist_ok=True)
            img.convert("RGBA").save(png)
            js.write_text(json.dumps({**meta, "largura": img.width, "altura": img.height}), encoding="utf-8")
            return {**meta, "largura": img.width, "altura": img.height, "png": png}, None
        except Exception as exc:  # qualquer falha so desliga o radar daquele mapa, nunca derruba a pagina
            _TENTATIVAS[mapa] = f"{type(exc).__name__}: {exc}"
            return None, _TENTATIVAS[mapa]


def esquecer_falhas():
    _TENTATIVAS.clear()
