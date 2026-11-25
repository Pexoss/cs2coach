"""Gera icone.ico (mira) para o .exe e o instalador. Usado so na hora de empacotar."""
from PIL import Image, ImageDraw

cor = (76, 194, 255, 255)
img = Image.new("RGBA", (256, 256), (15, 18, 24, 255))
d = ImageDraw.Draw(img)
d.ellipse((56, 56, 200, 200), outline=cor, width=14)
for ln in ((128, 8, 128, 88), (128, 168, 128, 248), (8, 128, 88, 128), (168, 128, 248, 128)):
    d.line(ln, fill=cor, width=14)
img.save("icone.ico", sizes=[(16, 16), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
print("icone.ico criado")
