"""Atalhos na raiz do projeto.

Por que existem: o código de verdade mora em `src/`, mas `python src/main.py`
resolve os imports a partir de `src/`, e é por isso que funciona. O que NÃO
funciona é `from src.main import ...` nem rodar de dentro de `src/` esperando
achar o banco.

Estes arquivos na raiz resolvem as duas coisas:

  - `python main.py` continua funcionando (sem precisar escrever `src/`)
  - o diretório de trabalho é fixado na RAIZ, então `imoveis.db`, `fotos/` e
    `html_ponte/` são sempre encontrados, não importa de onde você chamou

Cada um apenas delega para o módulo correspondente em `src/`.
"""

from __future__ import annotations

import os
import runpy
import sys

RAIZ = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(RAIZ, "src")


def executar(modulo: str) -> None:
    """Roda `src/<modulo>.py` como se fosse o programa principal."""
    # `src/` primeiro no path: os imports internos (`import config`,
    # `from storage import DB`) precisam achar os irmãos.
    if SRC not in sys.path:
        sys.path.insert(0, SRC)

    # fixa o diretório de trabalho na raiz, para os caminhos relativos de
    # dados (banco, fotos, html_ponte) apontarem para o lugar certo
    os.chdir(RAIZ)

    alvo = os.path.join(SRC, f"{modulo}.py")
    if not os.path.exists(alvo):
        sys.exit(f"modulo nao encontrado: {alvo}")

    sys.argv[0] = alvo
    runpy.run_path(alvo, run_name="__main__")
