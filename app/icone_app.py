# -*- coding: utf-8 -*-
"""Desenho do icone do CS2 Coach (mira com ponto central sobre fundo arredondado). Usado no .exe e na bandeja."""
from PIL import Image, ImageDraw, ImageFilter


def desenhar(tam=256):
    k = 4                                   # desenha 4x maior e reduz: bordas suaves
    N = tam * k
    base = Image.new("RGBA", (N, N), (0, 0, 0, 0))
    # fundo: quadrado arredondado com degrade vertical
    grad = Image.new("RGBA", (N, N))
    gd = ImageDraw.Draw(grad)
    for y in range(N):
        t = y / (N - 1)
        gd.line([(0, y), (N, y)], fill=(int(24 - 10 * t), int(32 - 12 * t), int(48 - 20 * t), 255))
    mask = Image.new("L", (N, N), 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, N - 1, N - 1), radius=int(N * 0.22), fill=255)
    base.paste(grad, (0, 0), mask)
    # brilho da mira
    cor, cor2 = (76, 194, 255, 255), (255, 255, 255, 255)
    cx = N // 2
    brilho = Image.new("RGBA", (N, N), (0, 0, 0, 0))
    bd = ImageDraw.Draw(brilho)
    r, esp = int(N * 0.27), int(N * 0.055)
    bd.ellipse((cx - r, cx - r, cx + r, cx + r), outline=(76, 194, 255, 110), width=int(esp * 1.8))
    base = Image.alpha_composite(base, brilho.filter(ImageFilter.GaussianBlur(N * 0.015)))
    d = ImageDraw.Draw(base)
    # anel
    d.ellipse((cx - r, cx - r, cx + r, cx + r), outline=cor, width=esp)
    # quatro traços com vão no centro
    vao = int(N * 0.10)
    for x0, y0, x1, y1 in ((cx, int(N * 0.12), cx, cx - vao),
                           (cx, cx + vao, cx, N - int(N * 0.12)),
                           (int(N * 0.12), cx, cx - vao, cx), (cx + vao, cx, N - int(N * 0.12), cx)):
        d.line((x0, y0, x1, y1), fill=cor, width=esp)
    # ponto central
    p = int(N * 0.035)
    d.ellipse((cx - p, cx - p, cx + p, cx + p), fill=cor2)
    return base.resize((tam, tam), Image.LANCZOS)
