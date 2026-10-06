"""Nota de encaixe (ordem padrão) e cópias do mesmo imóvel.

ENCAIXE: o usuário pediu peso para imóvel bem conservado. A nota soma
desconto e conservação porque foi MEDIDO que são independentes (correlação
+0,032 em 1.384 anúncios).

CÓPIAS: a mesma casa chega a ter 25 anúncios. Decisão de produto: a listagem
mostra uma linha por imóvel e as outras ofertas aparecem numa tabela na
página do anúncio — expandir viraria 25 cards idênticos.
"""

import re
import urllib.parse

import pytest
from conftest import contem

from cacaimoveis import config, webapp

N = webapp._nota_encaixe


# ---------------------------------------------------------------------------
# Nota de encaixe
# ---------------------------------------------------------------------------
def test_extremos_da_nota():
    assert N({"foto_cuidado": 5}, 0.50) == 1.0   # impecável e 50% abaixo
    assert N({"foto_cuidado": 1}, 1.00) == 0.0   # degradado e no preço


def test_conservado_ganha_de_degradado_mais_barato():
    assert N({"foto_cuidado": 5}, 0.95) > N({"foto_cuidado": 1}, 0.70)


def test_problema_visivel_desconta():
    sem = N({"foto_cuidado": 4}, 0.85)
    com = N({"foto_cuidado": 4, "foto_problemas": "mofo,infiltracao"}, 0.85)
    assert com == round(sem - config.ENCAIXE_PENAL_PROBLEMA, 4)
    assert N({"foto_cuidado": 1, "foto_problemas": "mofo"}, 1.0) >= 0.0


@pytest.mark.parametrize("anuncio, razao", [
    ({"foto_cuidado": None}, 0.9),   # sem análise de foto
    ({"foto_cuidado": 5}, None),     # sem comparação de preço
])
def test_sem_dado_nao_inventa_nota(anuncio, razao):
    assert N(anuncio, razao) is None


def _notas(html: str) -> list[int]:
    return [int(x) for x in re.findall(r'encaixe-texto">\s*encaixe (\d+)', html)]


def test_ordem_encaixe_e_o_padrao_e_ordena(cliente):
    home = cliente.get("/").get_data(as_text=True)
    assert re.search(r'<option value="encaixe"\s+selected>', home)
    notas = _notas(home)
    assert notas
    assert notas == sorted(notas, reverse=True)
    abaixo = cliente.get("/?ordem=abaixo").get_data(as_text=True)
    assert _notas(abaixo) != notas


def test_sqlite_lower_nao_baixa_acento(banco):
    """Documenta a causa do bug do filtro de bairro (ver test_web)."""
    assert banco.execute("SELECT LOWER('Água Branca')").fetchone()[0] != "água branca"
    assert webapp._sem_acento("Água Branca") == webapp._sem_acento("agua branca")


# ---------------------------------------------------------------------------
# Cópias do mesmo imóvel
# ---------------------------------------------------------------------------
def _total(html: str) -> int:
    m = re.search(r"<b>([\d.]+)</b>\s*imóve", html)
    return int(m.group(1).replace(".", "")) if m else -1


def _detalhe(cliente, url: str) -> str:
    r = cliente.get("/anuncio/" + urllib.parse.quote(url, safe=""))
    assert r.status_code == 200
    return r.get_data(as_text=True)


def _principal(banco, gid: int) -> str:
    return banco.execute(
        "SELECT url FROM anuncios WHERE dup_grupo=? AND dup_melhor=1", (gid,)).fetchone()[0]


def test_listagem_mostra_uma_linha_por_imovel(cliente, banco):
    total = banco.execute("SELECT COUNT(*) FROM anuncios").fetchone()[0]
    unicos = banco.execute(
        "SELECT COUNT(*) FROM anuncios WHERE dup_grupo IS NULL OR dup_melhor=1").fetchone()[0]
    assert _total(cliente.get("/").get_data(as_text=True)) == unicos
    assert _total(cliente.get("/?todas=1").get_data(as_text=True)) == total
    # `?grupo=N` foi aposentado e é ignorado
    assert _total(cliente.get("/?grupo=1").get_data(as_text=True)) == unicos


def test_aviso_do_card_leva_a_pagina_do_anuncio(cliente):
    # o grupo de 25 está em Vila Mangalot
    home = cliente.get("/?bairro=Vila+Mangalot").get_data(as_text=True)
    m = re.search(r'<a class="card-copias"\s+href="([^"]+)"', home)
    assert m
    link = m.group(1).replace("&amp;", "&")
    assert link.startswith("/anuncio/") and link.endswith("#ofertas")
    assert "todas=1" not in link and "grupo=" not in link


def test_pagina_mostra_as_outras_ofertas(cliente, banco):
    n = banco.execute("SELECT COUNT(*) FROM anuncios WHERE dup_grupo=1").fetchone()[0]
    det = _detalhe(cliente, _principal(banco, 1))
    assert "Outras ofertas deste imóvel" in det
    assert 'id="ofertas"' in det
    assert f"{n} no total" in det
    assert 'class="ofertas-tabela"' in det
    assert "este anúncio" in det
    assert len(re.findall(r'class="ofertas-link"', det)) == n - 1
    assert "imobiliárias diferentes" in det
    # 25 com o mesmo preço: o selo "mais barata" aparece UMA vez só
    assert len(re.findall(r'class="ofertas-menor"', det)) == 1


def test_grupo_de_dois(cliente, banco):
    det = _detalhe(cliente, _principal(banco, 2))
    assert len(re.findall(r'class="ofertas-link"', det)) == 1
    assert "2 no total" in det


def test_grupo_com_variacao_mostra_a_faixa(cliente, banco):
    assert "de diferença" in _detalhe(cliente, _principal(banco, 3))


def test_anuncio_sem_copia_nao_mostra_o_bloco(cliente, banco):
    url = banco.execute("SELECT url FROM anuncios WHERE dup_grupo IS NULL LIMIT 1").fetchone()[0]
    assert not contem("Outras ofertas deste imóvel", _detalhe(cliente, url))
