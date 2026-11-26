"""Gera icone.ico (mira) para o .exe e o instalador. Usado so na hora de empacotar."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "app"))
from icone_app import desenhar  # noqa: E402

desenhar(256).save("icone.ico", sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
print("icone.ico criado")
