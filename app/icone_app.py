# -*- coding: utf-8 -*-
"""Icone do CS2 Coach: mira branca sobre um quadrado arredondado em degrade azul (visual limpo, estilo Windows 11).
Usado no .exe, no instalador e na bandeja."""
from PIL import Image, ImageChops, ImageDraw, ImageFilter

TEMAS = {   # (cor 1, cor 2, cor da mira)
    "azul": ((20, 110, 235), (90, 205, 255), (255, 255, 255)),
    "grafite": ((38, 42, 52), (70, 78, 94), (96, 205, 255)),
    "claro": ((250, 252, 255), (222, 232, 245), (0, 95, 184)),
}


def desenhar(tam=256, tema="azul"):
    k = 4
    N = tam * k
    c1, c2, mira = TEMAS[tema]
    # degrade diagonal
    gx = Image.linear_gradient("L").resize((N, N))
    diag = ImageChops.add(gx, gx.transpose(Image.ROTATE_90), scale=2)
    base = Image.composite(Image.new("RGB", (N, N), c2), Image.new("RGB", (N, N), c1), diag).convert("RGBA")
    # brilho suave no topo
    brilho = Image.new("RGBA", (N, N), (0, 0, 0, 0))
    ImageDraw.Draw(brilho).ellipse((-N * 0.2, -N * 0.75, N * 1.2, N * 0.45), fill=(255, 255, 255, 30))
    base = Image.alpha_composite(base, brilho.filter(ImageFilter.GaussianBlur(N * 0.05)))
    mascara = Image.new("L", (N, N), 0)
    ImageDraw.Draw(mascara).rounded_rectangle((0, 0, N - 1, N - 1), radius=int(N * 0.225), fill=255)
    icone = Image.new("RGBA", (N, N), (0, 0, 0, 0))
    icone.paste(base, (0, 0), mascara)
    # mira
    cx = N // 2
    esp = int(N * 0.058)
    r = int(N * 0.235)
    camada = Image.new("RGBA", (N, N), (0, 0, 0, 0))
    d = ImageDraw.Draw(camada)
    cor = mira + (255,)
    d.ellipse((cx - r, cx - r, cx + r, cx + r), outline=cor, width=esp)
    vao, ponta = int(N * 0.085), int(N * 0.15)
    for (x0, y0, x1, y1) in ((cx, ponta, cx, cx - r - vao // 2), (cx, cx + r + vao // 2, cx, N - ponta),
                             (ponta, cx, cx - r - vao // 2, cx), (cx + r + vao // 2, cx, N - ponta, cx)):
        d.line((x0, y0, x1, y1), fill=cor, width=esp)
        d.ellipse((x0 - esp // 2, y0 - esp // 2, x0 + esp // 2, y0 + esp // 2), fill=cor)
        d.ellipse((x1 - esp // 2, y1 - esp // 2, x1 + esp // 2, y1 + esp // 2), fill=cor)
    p = int(N * 0.04)
    d.ellipse((cx - p, cx - p, cx + p, cx + p), fill=cor)
    sombra = camada.filter(ImageFilter.GaussianBlur(N * 0.012))
    sombra = Image.merge("RGBA", (Image.new("L", (N, N), 0),) * 3 + (sombra.getchannel("A").point(lambda v: int(v * 0.30)),))
    icone = Image.alpha_composite(icone, ImageChops.offset(sombra, 0, int(N * 0.012)))
    icone = Image.alpha_composite(icone, camada)
    return icone.resize((tam, tam), Image.LANCZOS)
