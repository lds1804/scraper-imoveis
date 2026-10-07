"""Configuração comum dos testes (pytest).

O banco de teste é criado ANTES de qualquer módulo do projeto ser importado:
`config` lê `CACA_DB` na importação, então a variável tem de estar definida
quando o primeiro `import config` acontecer. Por isso isto roda no corpo do
módulo, e não dentro de uma fixture.

Os testes NUNCA tocam o `imoveis.db` de trabalho, exceto os marcados com
`dados_reais`, que conferem a qualidade do dado coletado e são pulados quando
o banco não está presente (CI, máquina nova).
"""

from __future__ import annotations

import os
import shutil
import sqlite3
import sys
import tempfile

import pytest

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(RAIZ, "src")
FIXTURES = os.path.join(RAIZ, "tests", "fixtures")
BANCO_REAL = os.path.join(RAIZ, "imoveis.db")

_TMP = tempfile.mkdtemp(prefix="caca_testes_")
os.environ["CACA_DB"] = os.path.join(_TMP, "teste.db")
os.environ["CACA_FOTOS"] = os.path.join(_TMP, "fotos")
os.environ["CACA_LOG_DIR"] = os.path.join(_TMP, "logs")
os.environ.setdefault("CACA_AMBIENTE", "local")
if SRC not in sys.path:
    sys.path.insert(0, SRC)

import fabrica  # noqa: E402  (tests/ está no sys.path pelo rootdir)

from cacaimoveis import indices  # noqa: E402

fabrica.criar(os.environ["CACA_DB"])
# índices econômicos (BCB, dado público) num arquivo versionado: os testes
# não dependem de rede nem do `dados/` local
# o cache de índices vai para uma CÓPIA e não vence nunca: sem isso, o TTL de
# `indices.carregar` baixava os índices da rede e reescrevia a fixture versionada
shutil.copy(os.path.join(FIXTURES, "indices.json"), os.path.join(_TMP, "indices.json"))
indices.CACHE = os.path.join(_TMP, "indices.json")
indices.TTL_DIAS = 10**9


def contem(trecho, texto) -> bool:
    """`trecho in texto`, para usar em `assert not contem(...)`.

    Num `assert x not in html` que falha, o pytest monta um diff do HTML
    inteiro (centenas de KB) e trava por minutos. Passando por uma função, a
    asserção vê só um booleano.
    """
    return trecho in texto


def pytest_sessionfinish(session, exitstatus):
    shutil.rmtree(_TMP, ignore_errors=True)


@pytest.fixture(scope="session")
def app():
    from cacaimoveis import webapp

    webapp.app.config["TESTING"] = True
    return webapp.app


@pytest.fixture()
def cliente(app):
    return app.test_client()


@pytest.fixture()
def banco():
    """Conexão (somente leitura) com o banco de TESTE."""
    conn = sqlite3.connect(f"file:{os.environ['CACA_DB']}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    yield conn
    conn.close()


@pytest.fixture()
def banco_real(monkeypatch):
    """O `imoveis.db` de trabalho, em modo leitura. Pula se não existir."""
    if not os.path.exists(BANCO_REAL):
        pytest.skip("imoveis.db ausente (teste de dados reais)")
    from cacaimoveis import config

    monkeypatch.setattr(config, "DB_PATH", BANCO_REAL)
    conn = sqlite3.connect(f"file:{BANCO_REAL}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    yield conn
    conn.close()
