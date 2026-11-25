# -*- coding: utf-8 -*-
"""
Onde o CS2 Coach guarda os seus dados.

  Windows:  %APPDATA%\\CS2Coach          (ex.: C:\\Users\\voce\\AppData\\Roaming\\CS2Coach)
  Outros:   ~/.cs2coach
  Qualquer: a variavel de ambiente CS2COACH_DADOS manda em tudo.

Os dados ficam FORA da pasta do programa: atualizar ou reinstalar nunca apaga o seu historico.
Se existir uma pasta "dados" antiga ao lado do programa (versoes anteriores), ela e copiada uma vez.
"""
from __future__ import annotations

import os
import shutil
from pathlib import Path

_AQUI = Path(__file__).resolve().parent


def pasta_dados() -> Path:
    env = os.environ.get("CS2COACH_DADOS")
    if env:
        p = Path(env).expanduser()
    elif os.name == "nt":
        p = Path(os.environ.get("APPDATA") or Path.home()) / "CS2Coach"
    else:
        p = Path.home() / ".cs2coach"
    p.mkdir(parents=True, exist_ok=True)
    return p


def migrar_dados_antigos() -> str | None:
    """Copia uma pasta 'dados' de versoes antigas (ao lado do programa) para o lugar novo, uma unica vez."""
    destino = pasta_dados()
    if (destino / "cs2coach.db").exists() or (destino / ".migrado").exists():
        return None
    for antigo in (_AQUI / "dados", _AQUI.parent / "dados"):
        if (antigo / "cs2coach.db").exists() and antigo.resolve() != destino.resolve():
            try:
                shutil.copytree(antigo, destino, dirs_exist_ok=True)
                (destino / ".migrado").write_text(f"copiado de {antigo}\n", encoding="utf-8")
                return str(antigo)
            except OSError:
                return None
    return None
