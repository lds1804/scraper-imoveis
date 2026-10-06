"""Classificador de tipo de imóvel (casa x apartamento).

A OLX NÃO tem filtro de tipo que funcione (testado: `/casas?q=X`,
`?category=1002` e as abas da página são ignorados), então o tipo sai do
TÍTULO — e isso decide o que entra no banco.

Armadilha real: "Vila Mangalot", "Vila Leopoldina" e "Vila Jaguara" são NOMES
DE BAIRRO. Se "vila" contasse como pista de casa, todo apartamento desses
bairros passaria como casa. Foi o que aconteceu na 1ª versão.
"""

import pytest

from scraper_browser import Anuncio, e_casa, tipo_do_anuncio

URL_PADRAO = "https://sp.olx.com.br/x/imoveis/anuncio-12345678"


def _tipo(titulo: str, url: str = URL_PADRAO) -> str:
    return tipo_do_anuncio(Anuncio(url=url, titulo=titulo))


@pytest.mark.parametrize("titulo", [
    "Apartamento à Venda - Vila Mangalot, 3 Quartos",
    "APARTAMENTO À VENDA VILA MANGALOT",
    "Ótimo apartamento à venda na Vila Mangalot",
    "Apartamento para venda em Vila Mangalot com 3 quartos",
    "Apartamento em Vila Leopoldina",
    "Apartamento 2 quartos Vila Jaguara",
    "Cobertura duplex à venda",
    "Kitnet mobiliada no centro",
    "Studio novo na Lapa",
    "Flat mobiliado com serviço",
    "Loft moderno em Pinheiros",
    # contém as duas palavras: o apartamento é o tipo real
    "Apartamento em condomínio de casas",
    # "Vila" no título é o bairro, não o tipo
    "Apartamento à venda Vila Mangalot",
    "Apartamento novo em Vila Leopoldina",
    "Apartamento 3 quartos na Vila Jaguara",
])
def test_apartamento_e_descartado(titulo):
    assert _tipo(titulo) == "apartamento"


@pytest.mark.parametrize("titulo", [
    "Casa para venda em Vila Mangalot com 4 quartos",
    "Sobrado à venda no Parque Maria Domitila",
    "Lindo sobrado novo à venda",
    "Casa térrea com quintal",
    "Casa de vila 120m² reformada",
    "Casa residencial em São Paulo",
    "Chácara com pomar em Pirituba",
    # casas nos bairros "Vila X" não podem ser perdidas
    "Casa térrea na Vila Mangalot",
    "Sobrado na Vila Leopoldina",
])
def test_casa_e_mantida(titulo):
    assert _tipo(titulo) == "casa"


@pytest.mark.parametrize("titulo", [
    "Excelente oportunidade na vila mangalot!",
    "Imóvel para venda com 285 metros quadrados",
    "OPORTUNIDADE CITY AMÉRICA POR APENAS 3 MESES",
])
def test_sem_tipo_fica_incerto(titulo):
    """Título sem tipo explícito: a decisão é não descartar."""
    assert _tipo(titulo) == "incerto"


@pytest.mark.parametrize("url, esperado", [
    ("https://sp.olx.com.br/sao-paulo-e-regiao/imoveis/"
     "venda-apartamento-sao-caetano-do-sul-centro-1460188398", "apartamento"),
    ("https://sp.olx.com.br/sao-paulo-e-regiao/imoveis/"
     "casa-para-venda-em-vila-mangalot-1540642325", "casa"),
])
def test_tipo_cai_para_a_url(url, esperado):
    assert _tipo("Sem tipo no titulo", url) == esperado


@pytest.mark.parametrize("titulo, esperado", [
    ("Casa simples", True),
    ("Apartamento grande", False),
    ("Imóvel sem tipo informado", True),  # incerto não é descartado
])
def test_e_casa_trata_incerto_como_casa(titulo, esperado):
    assert e_casa(Anuncio(url="https://x/i/a-12345678", titulo=titulo)) is esperado
