"""Geocodificação: parsing do endereço, cache e validação do CEP. Sem rede."""

import sqlite3

import pytest
import requests

from cacaimoveis import geocode


@pytest.fixture()
def conn():
    c = sqlite3.connect(":memory:")
    return c


@pytest.mark.parametrize("rua, esperado", [
    ("Rua Tomé de Souza, 335", ("Rua Tomé de Souza", "335")),
    ("Rua Diogo Fernandes,", ("Rua Diogo Fernandes", "")),
    ("Avenida X 12A", ("Avenida X", "12A")),
    ("Rua Sem Numero", ("Rua Sem Numero", "")),
    ("", ("", "")),
])
def test_separa_o_numero_da_rua(rua, esperado):
    assert geocode.separar_numero(rua) == esperado


def test_cep_de_outra_regiao_e_recusado():
    """A rua homônima de outro bairro foi o erro medido (05131 x 05163)."""
    assert geocode._cep_confere({"address": {"postcode": "05163-100"}}, "05131110") is False
    assert geocode._cep_confere({"address": {"postcode": "05131-900"}}, "05131110") is True
    # sem como conferir, vale
    assert geocode._cep_confere({"address": {}}, "05131110") is True
    assert geocode._cep_confere({"address": {"postcode": "05163-100"}}, "") is True


def test_cache_evita_repetir_a_consulta(conn, monkeypatch):
    chamadas = []

    def falso(**params):
        chamadas.append(params)
        return {"lat": "-23.5", "lon": "-46.7", "type": "house",
                "address": {"postcode": "05131-110"}}

    monkeypatch.setattr(geocode, "_consultar", falso)
    a = geocode.obter(conn, "Rua A, 10", "Vila Mangalot", "05131110")
    assert a == {"lat": -23.5, "lon": -46.7, "precisao": "casa"}
    n = len(chamadas)
    assert geocode.obter(conn, "Rua A, 10", "Vila Mangalot", "05131110") == a
    assert len(chamadas) == n          # 2ª vez: do cache


def test_nao_achou_vai_para_o_cache_mas_erro_de_rede_nao(conn, monkeypatch):
    monkeypatch.setattr(geocode, "_consultar", lambda **p: None)
    assert geocode.obter(conn, "Rua B, 5", "Lapa", "05050000") is None
    assert conn.execute("SELECT precisao FROM geocode").fetchone()[0] == "nenhuma"

    def cai(**p):
        raise requests.ConnectionError("sem rede")

    monkeypatch.setattr(geocode, "_consultar", cai)
    assert geocode.obter(conn, "Rua C, 7", "Lapa", "05050000") is None
    assert conn.execute("SELECT COUNT(*) FROM geocode").fetchone()[0] == 1  # só o "não achei"


def test_sem_rua_nao_consulta(conn, monkeypatch):
    monkeypatch.setattr(geocode, "_consultar",
                        lambda **p: pytest.fail("não deveria consultar"))
    assert geocode.obter(conn, "", "Lapa") is None
