"""Geocodificação pelo cadastro de lotes: sem rede, com lotes inventados."""

import sqlite3

import pytest

from cacaimoveis import geocode


@pytest.fixture()
def conn():
    c = sqlite3.connect(":memory:")
    geocode.garantir_tabelas(c)
    # rua "teste": números ímpares de 1 a 9, de oeste (lon -46.70) a leste, em linha reta
    for i, n in enumerate((1, 3, 5, 7, 9)):
        c.execute("INSERT INTO lote_geo VALUES ('001','001',?, '', 'TESTE', ?, -23.5, ?)",
                  (f"{i:04d}", str(n), -46.70 + 0.001 * i))
    c.execute("INSERT INTO lote_geo_ruas VALUES ('TESTE', 5, datetime('now'))")
    # rua homônima do outro lado da cidade (para o teste de ambiguidade)
    c.execute("INSERT INTO lote_geo VALUES ('002','002','0001','','LONGE','1',-23.7,-46.40)")
    c.execute("INSERT INTO lote_geo VALUES ('002','002','0002','','LONGE','3',-23.5,-46.80)")
    c.execute("INSERT INTO lote_geo_ruas VALUES ('LONGE', 2, datetime('now'))")
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


def test_centroide_do_quadrado():
    quadrado = {"type": "Polygon",
                "coordinates": [[[-46.0, -23.0], [-46.0, -23.002], [-46.002, -23.002],
                                 [-46.002, -23.0], [-46.0, -23.0]]]}
    lat, lon = geocode.centroide(quadrado)
    assert lat == pytest.approx(-23.001) and lon == pytest.approx(-46.001)
    assert geocode.centroide({"type": "Polygon", "coordinates": [[[0, 0], [1, 1]]]}) is None


def test_numero_exato_vira_o_lote(conn):
    r = geocode.localizar(conn, "Rua Teste, 5", rua_chave="TESTE", consultar_rede=False)
    assert r["precisao"] == "lote"
    assert r["lon"] == pytest.approx(-46.698)


def test_numero_ausente_sem_vizinho_de_um_lado_usa_o_mais_proximo(conn):
    # 11 é ímpar e não existe; o vizinho mais próximo do mesmo lado é o 9
    r = geocode.localizar(conn, "Rua Teste, 11", rua_chave="TESTE", consultar_rede=False)
    assert r["precisao"] == "proximo"
    assert r["lon"] == pytest.approx(-46.696)


def test_interpola_entre_vizinhos(conn):
    conn.execute("DELETE FROM lote_geo WHERE chave='TESTE' AND numero='5'")
    r = geocode.localizar(conn, "Rua Teste, 5", rua_chave="TESTE", consultar_rede=False)
    assert r["precisao"] == "proximo"
    assert r["lon"] == pytest.approx(-46.698)   # meio entre o 3 e o 7


def test_sem_numero_devolve_o_meio_da_rua(conn):
    r = geocode.localizar(conn, "Rua Teste", rua_chave="TESTE", consultar_rede=False)
    assert r["precisao"] == "rua"
    assert r["lon"] == pytest.approx(-46.698)


def test_rua_homonima_distante_sem_desempate_nao_chuta(conn):
    assert geocode.localizar(conn, "Rua Longe", rua_chave="LONGE", consultar_rede=False) is None


def test_rua_nao_baixada_sem_rede_nao_inventa(conn):
    assert geocode.localizar(conn, "Rua Outra, 10", rua_chave="OUTRA",
                             consultar_rede=False) is None


def test_sem_rua_nao_localiza(conn):
    assert geocode.localizar(conn, "", "Lapa", consultar_rede=False) is None


def test_cep_desempata_ruas_homonimas(conn):
    # a mesma rua tem lotes em dois CEPs; o do anúncio decide
    conn.execute("CREATE TABLE iptu (sql TEXT, numero TEXT, cep TEXT, rua_chave TEXT)")
    conn.executemany("INSERT INTO iptu VALUES ('x', ?, ?, 'TESTE')",
                     [("1", "05100000"), ("3", "05100000"), ("5", "09999000"),
                      ("7", "09999000"), ("9", "09999000")])
    r = geocode.localizar(conn, "Rua Teste", cep="09999000", rua_chave="TESTE",
                          consultar_rede=False)
    assert r["precisao"] == "rua"
    assert r["lon"] == pytest.approx(-46.697)   # mediana dos lotes 5, 7 e 9 (CEP 09999)
