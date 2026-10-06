"""Log em arquivo para as execuções do pipeline.

Por que existe: o terminal mostra o progresso (barras, contagens) e os erros
numa linha cortada, como `[erro] Vila Mangalot: HTTP 403: <html>...`. Numa
execução agendada (o deploy da AWS) ninguém está olhando o terminal, e a linha
some. Aqui os erros TRATADOS — os que a coleta engole para seguir adiante —
ficam registrados com o traceback inteiro, data e módulo de origem.

O `print` continua sendo a saída do terminal; o log não duplica nada lá.

Arquivo: `dados/logs/caca.log` (rotativo, 5 x 2 MB). `CACA_LOG_DIR` troca a
pasta — os testes apontam para uma pasta temporária.
"""

from __future__ import annotations

import logging
import os
from logging.handlers import RotatingFileHandler

from cacaimoveis import config

_RAIZ_LOGGER = "cacaimoveis"
_configurado = False


def _configurar() -> None:
    global _configurado
    if _configurado:
        return
    _configurado = True
    pasta = os.environ.get("CACA_LOG_DIR") or config.caminho("dados", "logs")
    os.makedirs(pasta, exist_ok=True)
    arquivo = RotatingFileHandler(
        os.path.join(pasta, "caca.log"),
        maxBytes=2_000_000, backupCount=5, encoding="utf-8", delay=True,
    )
    arquivo.setFormatter(logging.Formatter(
        "%(asctime)s %(levelname)-7s %(name)s:%(lineno)d  %(message)s"))
    raiz = logging.getLogger(_RAIZ_LOGGER)
    raiz.setLevel(logging.INFO)
    raiz.addHandler(arquivo)
    # sem propagar para o logger raiz do Python: o terminal é dos `print`
    raiz.propagate = False


def obter(nome: str) -> logging.Logger:
    """Logger do módulo `nome` (use `logs.obter(__name__)`)."""
    _configurar()
    if not nome.startswith(_RAIZ_LOGGER):
        nome = f"{_RAIZ_LOGGER}.{nome}"
    return logging.getLogger(nome)
