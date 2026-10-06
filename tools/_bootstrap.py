"""Prepara o ambiente para scripts soltos (testes e ferramentas).

Por que existe: o código de produção mora em `src/`. Quando você roda
`python tests/testar_web.py`, o Python coloca `tests/` no `sys.path` — não
`src/` — então `import webapp` falharia.

Chamar `iniciar()` no topo de cada script resolve isso:

    from _bootstrap import iniciar
    iniciar()

    from cacaimoveis import webapp   # agora funciona, não importa de onde você rodou

Efeitos:
  - `<raiz>/src` no início do `sys.path`
  - diretório de trabalho fixado na raiz (para achar `imoveis.db`, `fotos/`)

É idempotente: chamar duas vezes não faz mal.
"""

from __future__ import annotations

import os
import sys

_RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_SRC = os.path.join(_RAIZ, "src")


def iniciar() -> str:
    """Ajusta `sys.path` e o diretório de trabalho. Devolve a raiz do projeto."""
    if _SRC not in sys.path:
        sys.path.insert(0, _SRC)
    os.chdir(_RAIZ)
    return _RAIZ
