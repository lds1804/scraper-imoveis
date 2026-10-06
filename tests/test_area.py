"""QUAL área o anúncio publica (terreno x construção).

O anúncio publica UM número de metragem e o cadastro tem DOIS. O código
comparava sempre com a CONSTRUÇÃO e acusava divergência quando o anúncio
tinha publicado o TERRENO. Medido: 15,2% das conferências eram falso positivo.
"""

import urllib.parse

import pytest

from cacaimoveis import referencia_geosampa as rg
from cacaimoveis import webapp

F = rg.FAIXA_OK / 100


@pytest.mark.parametrize("anuncio, construida, terreno, esperado", [
    (200, 60, 200, "terreno"),      # o caso real: Rua Alvares Otero 37
    (434, 145, 434, "terreno"),     # Rua Itapejara 86
    (200, 200, 200, "ambos"),       # terreno = construção
    (100, 100, 300, "construcao"),  # o caso normal
    (900, 100, 130, "nenhum"),      # não casa com nada
    (None, 100, 200, "sem dado"),   # anúncio não informou
])
def test_casa_com(anuncio, construida, terreno, esperado):
    assert rg._casa_com(anuncio, construida, terreno)[1] == esperado


def test_zona_morta_de_15_por_cento():
    assert rg._casa_com(100 * (1 + F * 0.9), 100, 300)[1] == "construcao"
    assert rg._casa_com(100 * (1 + F * 2), 100, 300)[1] == "nenhum"


@pytest.mark.parametrize("area_of, esperado", [
    ({"dif_pct": 233.0, "area_casa": "terreno"}, "compativel"),
    ({"dif_pct": 0.0, "area_casa": "ambos"}, "compativel"),
    ({"dif_pct": 250.0, "area_casa": "nenhum"}, "divergente"),
    ({"dif_pct": 8.0, "area_casa": "construcao"}, "compativel"),
    (None, "sem dado"),
    ({"dif_pct": 5.0}, "compativel"),   # registro antigo, sem area_casa
])
def test_grau_da_area(area_of, esperado):
    assert webapp._grau_da_area(area_of) == esperado


def test_pagina_explica_que_o_anuncio_publicou_o_terreno(cliente, banco):
    url = banco.execute(
        "SELECT anuncio_url FROM areas_oficiais WHERE area_casa='terreno' LIMIT 1"
    ).fetchone()[0]
    r = cliente.get("/anuncio/" + urllib.parse.quote(url, safe=""))
    assert r.status_code == 200
    texto = r.get_data(as_text=True).replace("<b>", "").replace("</b>", "")
    assert "publica a área do TERRENO" in texto
    assert "não é erro do anúncio" in texto
    assert "prefeitura registra como construído" in texto


@pytest.mark.dados_reais
def test_banco_real_tem_area_casa(banco_real):
    tot, preenchidas, terreno = banco_real.execute(
        """SELECT COUNT(*), COUNT(area_casa), SUM(area_casa='terreno')
           FROM areas_oficiais""").fetchone()
    assert preenchidas >= tot - 5
    assert terreno > 0
    valores = {r[0] for r in banco_real.execute("SELECT DISTINCT area_casa FROM areas_oficiais")}
    assert valores <= {"construcao", "terreno", "ambos", "nenhum", "sem dado", None}
