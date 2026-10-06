"""Migrações do schema, rotas da ponte e o intervalo da API do ZAP."""

import sqlite3

import pytest

import config
import glue_api
import migracoes
import ponte


# ---------------------------------------------------------------------------
# Migrações
# ---------------------------------------------------------------------------
def test_banco_novo_recebe_todas_as_migracoes():
    conn = sqlite3.connect(":memory:")
    assert migracoes.migrar(conn) == len(migracoes.MIGRACOES)
    nomes = {r[0] for r in conn.execute("SELECT name FROM sqlite_master")}
    # a tabela que nenhum código criava, e o índice sem o qual a home leva 213 s
    assert {"valores_venais", "idx_fotos_anuncio"} <= nomes


def test_migrar_duas_vezes_nao_faz_nada():
    conn = sqlite3.connect(":memory:")
    migracoes.migrar(conn)
    antes = conn.execute("SELECT sql FROM sqlite_master ORDER BY name").fetchall()
    migracoes.migrar(conn)
    assert conn.execute("SELECT sql FROM sqlite_master ORDER BY name").fetchall() == antes


def test_banco_antigo_ganha_as_colunas_que_faltam():
    """O `imoveis.db` de trabalho está na versão 0 com parte das colunas."""
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE anuncios (url TEXT PRIMARY KEY, titulo TEXT)")
    conn.execute("INSERT INTO anuncios VALUES ('u', 't')")
    migracoes.migrar(conn)
    cols = {r[1] for r in conn.execute("PRAGMA table_info(anuncios)")}
    assert {"foto_cuidado", "dup_grupo", "rua_chave"} <= cols
    assert conn.execute("SELECT titulo FROM anuncios").fetchone()[0] == "t"


def test_migracao_que_falha_nao_deixa_banco_pela_metade(monkeypatch):
    def quebra(conn):
        conn.execute("CREATE TABLE meia (x)")
        raise RuntimeError("falhou no meio")

    monkeypatch.setattr(migracoes, "MIGRACOES", [*migracoes.MIGRACOES, quebra])
    conn = sqlite3.connect(":memory:")
    with pytest.raises(RuntimeError):
        migracoes.migrar(conn)
    assert migracoes.versao(conn) == len(migracoes.MIGRACOES) - 1
    assert not conn.execute("SELECT 1 FROM sqlite_master WHERE name='meia'").fetchone()


# ---------------------------------------------------------------------------
# Ponte do navegador
# ---------------------------------------------------------------------------
def test_ponte_recusa_outro_site(cliente):
    """Um POST de formulário não passa por preflight: sem checar o Origin,
    qualquer página aberta no navegador apagaria a pasta."""
    r = cliente.post("/_ponte/limpar", headers={"Origin": "https://evil.example"})
    assert r.status_code == 403
    assert "Access-Control-Allow-Origin" not in r.headers


@pytest.mark.parametrize("origem", [
    "https://www.imovelweb.com.br", "http://127.0.0.1:5000", "http://localhost:5000",
])
def test_ponte_aceita_imovelweb_e_maquina_local(cliente, origem):
    r = cliente.get("/_ponte/progresso", headers={"Origin": origem})
    assert r.status_code == 200
    assert r.headers["Access-Control-Allow-Origin"] == origem


def test_ponte_grava_na_pasta_configurada(cliente, tmp_path, monkeypatch):
    monkeypatch.setattr(ponte, "PONTE_DIR", str(tmp_path))
    r = cliente.post("/_ponte/html?nome=../../fora", data=b"<html></html>")
    assert r.status_code == 200
    # o nome é saneado: nada sai da pasta
    assert [p.name for p in tmp_path.iterdir()] == [".._.._fora.html"]


def test_ponte_nao_existe_em_producao():
    """Em produção o blueprint não é registrado (ver webapp)."""
    import subprocess
    import sys

    codigo = (
        "import webapp; "
        "print(any(r.rule.startswith('/_ponte') for r in webapp.app.url_map.iter_rules()))"
    )
    saida = subprocess.run(
        [sys.executable, "-c", codigo], capture_output=True, text=True, check=True,
        env={**__import__("os").environ, "CACA_AMBIENTE": "producao"},
        cwd=config.caminho("src"),
    ).stdout.strip()
    assert saida == "False"


# ---------------------------------------------------------------------------
# Crawl-delay do ZAP/VivaReal
# ---------------------------------------------------------------------------
def test_api_do_zap_respeita_o_crawl_delay(monkeypatch):
    assert config.GLUE_DELAY_S >= 10   # robots.txt: Crawl-delay: 10
    relogio = [1000.0]
    esperas: list[float] = []
    monkeypatch.setattr(glue_api.time, "monotonic", lambda: relogio[0])

    def dormir(s):
        esperas.append(s)
        relogio[0] += s

    monkeypatch.setattr(glue_api.time, "sleep", dormir)
    monkeypatch.setattr(glue_api, "_ultima_chamada", 0.0)

    glue_api._respeitar_intervalo()          # primeira: não espera
    relogio[0] += 3                           # 3 s depois (ex.: troca de bairro)
    glue_api._respeitar_intervalo()
    assert esperas == [pytest.approx(config.GLUE_DELAY_S - 3)]
