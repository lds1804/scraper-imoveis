"""Rotas e filtros da interface web.

Os filtros são combináveis e a URL é a única fonte de verdade deles. Um erro
aqui não quebra a página — devolve a lista errada em silêncio, que é pior.
Foi assim que o filtro "Terra batida" ficou devolvendo zero: o código estava
sintaticamente correto.

Roda contra o banco sintético de `fabrica.py`: as contagens esperadas vêm do
próprio banco, não de números fixos.
"""

import html as htmlmod
import re
import urllib.parse

import pytest
from conftest import contem
from flask import url_for

POR_PAGINA = 60


def _texto(r) -> str:
    return r.get_data(as_text=True)


def _cards(r) -> int:
    return r.data.count(b'class="card"')


def _total(r) -> int:
    """O total filtrado exibido no topo ("<b>1.234</b> imóveis")."""
    m = re.search(r"<b>([\d.]+)</b>\s*im", _texto(r))
    return int(m.group(1).replace(".", "")) if m else -1


def _achata(html: str) -> str:
    """O template quebra a linha entre `value` e `checked`."""
    return re.sub(r"\s+", " ", html)


def _links_anuncio(r) -> list[str]:
    return [htmlmod.unescape(h) for h in re.findall(r'href="(/anuncio/[^"]+)"', _texto(r))]


def _visiveis(banco, where: str = "1=1", params=()) -> int:
    """Quantos anúncios a listagem padrão (1 por imóvel) deveria mostrar."""
    return banco.execute(
        f"""SELECT COUNT(*) FROM anuncios a
            LEFT JOIN comparacoes c ON c.anuncio_url = a.url
            WHERE (a.dup_grupo IS NULL OR a.dup_melhor = 1) AND {where}""",
        params).fetchone()[0]


# ---------------------------------------------------------------------------
# Página inicial e paginação
# ---------------------------------------------------------------------------
def test_home_responde_paginada(cliente, banco):
    r = cliente.get("/")
    assert r.status_code == 200
    assert _cards(r) == POR_PAGINA
    assert b"paginacao" in r.data
    assert _total(r) == _visiveis(banco)


def test_paginas_nao_repetem_anuncios(cliente):
    p1, p2 = cliente.get("/"), cliente.get("/?pagina=2")
    assert _cards(p1) == _cards(p2) == POR_PAGINA
    u1, u2 = set(_links_anuncio(p1)), set(_links_anuncio(p2))
    assert u1 and u2 and not (u1 & u2)


def test_trocar_de_pagina_preserva_o_filtro(cliente):
    h = _texto(cliente.get("/?so_quintal=1&pagina=1"))
    assert "so_quintal=1" in h and "pagina=2" in h


@pytest.mark.parametrize("pagina", ["99999", "abc", "-3"])
def test_pagina_invalida_nao_quebra(cliente, pagina):
    r = cliente.get(f"/?pagina={pagina}")
    assert r.status_code == 200
    assert _cards(r) > 0


# ---------------------------------------------------------------------------
# Filtros
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("query, where", [
    ("so_quintal=1", "a.match_quintal = 1"),
    ("so_financiamento=1", "a.aceita_financiamento = 1"),
    ("sem_financiamento=1", "(a.aceita_financiamento IS NULL OR a.aceita_financiamento = 0)"),
    ("so_arvores=1", "a.foto_arvores = 1"),
    ("so_abaixo=1", "c.razao < 1"),
    ("piso_quintal=grama", "a.foto_piso_quintal = 'grama'"),
    ("piso_quintal=cimento", "a.foto_piso_quintal = 'cimento'"),
    ("piso_quintal=misto", "a.foto_piso_quintal = 'misto'"),
    # regressão: "terra" pergunta se TEM terra, não o piso predominante
    ("piso_quintal=terra", "a.foto_quintal_terra = 1"),
    ("quartos_min=3", "a.quartos >= 3"),
    ("terreno_min=300", "a.area_terreno >= 300"),
    ("preco_max=500000", "a.preco <= 500000"),
])
def test_filtro_devolve_exatamente_o_esperado(cliente, banco, query, where):
    esperado = _visiveis(banco, where)
    assert esperado > 0, "a fábrica deveria ter exemplos deste filtro"
    r = cliente.get("/?" + query)
    assert r.status_code == 200
    assert _total(r) == esperado


def test_filtros_combinados_se_acumulam(cliente, banco):
    r = cliente.get("/?so_arvores=1&so_financiamento=1")
    assert _total(r) == _visiveis(banco, "a.foto_arvores = 1 AND a.aceita_financiamento = 1")


def test_filtro_impossivel_devolve_vazio(cliente):
    r = cliente.get("/?preco_max=1")
    assert r.status_code == 200
    assert _cards(r) == 0


@pytest.mark.parametrize("query", ["preco_max=abc", "terreno_min=x", "quartos_min=2.5x"])
def test_filtro_numerico_invalido_e_ignorado(cliente, banco, query):
    """Antes: `float('abc')` derrubava a página com 500."""
    r = cliente.get("/?" + query)
    assert r.status_code == 200
    assert _total(r) == _visiveis(banco)


def test_dois_pisos_combinam_com_ou(cliente):
    n1 = _total(cliente.get("/?piso_quintal=grama"))
    n2 = _total(cliente.get("/?piso_quintal=cimento"))
    juntos = _total(cliente.get("/?piso_quintal=grama&piso_quintal=cimento"))
    assert juntos == n1 + n2   # pisos predominantes são disjuntos


def test_caixa_todas_vem_desmarcada(cliente):
    """Regressão: a ordem padrão sobrescrevia `todas` com a lista de linhas,
    e a caixa "mostrar todas as cópias" aparecia marcada sem pedido."""
    h = _achata(_texto(cliente.get("/")))
    assert not contem('name="todas" value="1" checked', h)
    h = _achata(_texto(cliente.get("/?todas=1")))
    assert 'name="todas" value="1" checked' in h


# ---------------------------------------------------------------------------
# Bairro (múltipla escolha)
# ---------------------------------------------------------------------------
def test_painel_lista_os_bairros(cliente, banco):
    h = _texto(cliente.get("/"))
    opcoes = re.findall(r'<input type="checkbox" name="bairro" value="([^"]+)"', h)
    no_banco = {r[0] for r in banco.execute("SELECT DISTINCT bairro FROM anuncios")}
    assert set(opcoes) == no_banco
    assert 'class="multi-valor"' in h


def test_cada_bairro_devolve_o_que_promete(cliente, banco):
    """Regressão: o LOWER() do SQLite não baixa letra acentuada maiúscula, e
    "Água Branca" / "Jardim Íris" devolviam ZERO."""
    for (bairro,) in banco.execute("SELECT DISTINCT bairro FROM anuncios"):
        real = banco.execute("SELECT COUNT(*) FROM anuncios WHERE bairro=?", (bairro,)).fetchone()[0]
        r = cliente.get("/", query_string={"bairro": bairro, "todas": "1"})
        assert _total(r) == real, bairro
        assert f'name="bairro" value="{bairro}" checked' in _achata(_texto(r))


@pytest.mark.parametrize("escrita", [
    "Água Branca", "agua-branca", "agua branca", "AGUA BRANCA", "ÁGUA BRANCA",
])
def test_variacoes_de_escrita_do_bairro(cliente, banco, escrita):
    real = banco.execute("SELECT COUNT(*) FROM anuncios WHERE bairro='Água Branca'").fetchone()[0]
    assert _total(cliente.get("/", query_string={"bairro": escrita, "todas": "1"})) == real


@pytest.mark.parametrize("bairro", ["agua", "Bairro Que Nao Existe", "Jaguara"])
def test_bairro_inexistente_devolve_zero(cliente, bairro):
    """Sem isto o filtro sumia em silêncio e a lista vinha COMPLETA."""
    assert _total(cliente.get("/", query_string={"bairro": bairro, "todas": "1"})) == 0


def test_dois_bairros_somam(cliente, banco):
    def n(b):
        return banco.execute("SELECT COUNT(*) FROM anuncios WHERE bairro=?", (b,)).fetchone()[0]

    qs = [("bairro", "Lapa"), ("bairro", "Água Branca"), ("todas", "1")]
    r = cliente.get("/", query_string=qs)
    assert _total(r) == n("Lapa") + n("Água Branca")
    h = _achata(_texto(r))
    assert h.count('name="bairro" value="Lapa" checked') == 1
    assert h.count('name="bairro" value="Água Branca" checked') == 1
    # o mesmo bairro duas vezes não soma
    qs = [("bairro", "Lapa"), ("bairro", "Lapa"), ("todas", "1")]
    assert _total(cliente.get("/", query_string=qs)) == n("Lapa")


# ---------------------------------------------------------------------------
# Chips e botão Limpar
# ---------------------------------------------------------------------------
def test_chip_remove_so_o_proprio_filtro(cliente):
    h = _texto(cliente.get("/?so_quintal=1&so_arvores=1"))
    assert "Filtros ativos" in h
    assert h.count('class="chip"') == 2
    m = re.search(r'<a class="chip" href="([^"]+)"[^>]*>\s*só com quintal', h)
    assert m
    destino = m.group(1).replace("&amp;", "&")
    assert "so_arvores=1" in destino and "so_quintal" not in destino


def test_botao_limpar_so_com_filtro(cliente):
    assert not contem(b"btn-secundario", cliente.get("/").data)
    assert b"btn-secundario" in cliente.get("/?so_quintal=1").data


# ---------------------------------------------------------------------------
# Página do anúncio
# ---------------------------------------------------------------------------
def test_todos_os_links_da_listagem_abrem(cliente):
    r = cliente.get("/?so_arvores=1")
    links = _links_anuncio(r)
    assert links
    assert {cliente.get(h).status_code for h in links} == {200}
    assert "O que as fotos mostram" in _texto(cliente.get(links[0]))


@pytest.mark.parametrize("caractere", ["?", "$"])
def test_anuncio_com_caractere_especial_na_url_abre(app, cliente, banco, caractere):
    """`?` cru no href corta a URL antes de chegar ao Flask; tem de sair
    codificado (%3F). `$` é legal num caminho (RFC 3986) e passa cru."""
    urls = [r[0] for r in banco.execute(
        "SELECT url FROM anuncios WHERE instr(url, ?) > 0", (caractere,))]
    assert urls
    with app.test_request_context():
        hrefs = [htmlmod.unescape(url_for("detalhe", anuncio_url=u)) for u in urls]
    ruins = [h for h in hrefs if cliente.get(h).status_code != 200]
    assert not ruins


def test_anuncio_inexistente_devolve_404(cliente):
    assert cliente.get("/anuncio/nao-existe-12345").status_code == 404


def test_medidas_de_valor_aparecem(cliente):
    h = _texto(cliente.get("/"))
    assert "Mercado" in h and 'class="delta' in h
    assert "Venal IPTU" in h
    # a chave interna da região não pode vazar para a tela
    assert not contem("cep5:", h) and not contem("cep4:", h)


def test_rotulo_da_regiao_do_venal_e_legivel(cliente, banco):
    url = banco.execute(
        """SELECT v.anuncio_url FROM valores_venais v
           JOIN comparacoes c ON c.anuncio_url = v.anuncio_url
           WHERE v.regiao LIKE 'cep5:%' LIMIT 1""").fetchone()[0]
    r = cliente.get("/anuncio/" + urllib.parse.quote(url, safe=""))
    assert r.status_code == 200
    h = _texto(r)
    assert "do CEP" in h
    assert "transaç" in h


def test_voltar_preserva_os_filtros(cliente, banco):
    url = banco.execute("SELECT url FROM anuncios LIMIT 1").fetchone()[0]
    caminho = "/anuncio/" + urllib.parse.quote(url, safe="")
    origem = "http://localhost/?so_quintal=1&pagina=2"
    h = _texto(cliente.get(caminho, headers={"Referer": origem}))
    assert 'href="http://localhost/?so_quintal=1&amp;pagina=2"' in h
    # Referer de outro site não vira redirect aberto
    h = _texto(cliente.get(caminho, headers={"Referer": "https://evil.example/"}))
    assert not contem("evil.example", h)


# ---------------------------------------------------------------------------
# Aviso de cobertura da análise visual
# ---------------------------------------------------------------------------
def test_filtro_pelas_fotos_avisa_quantos_foram_analisados(cliente, banco):
    total, analisados = banco.execute(
        """SELECT COUNT(*), COUNT(foto_analisada_em) FROM anuncios
           WHERE dup_grupo IS NULL OR dup_melhor = 1""").fetchone()
    assert analisados < total, "a fábrica deveria ter anúncios sem análise"
    h = _achata(_texto(cliente.get("/?so_arvores=1")))
    assert "cobertura-visual" in h
    assert f"só {analisados} de {total} imóveis" in h


def test_sem_filtro_visual_nao_mostra_o_aviso(cliente):
    assert not contem("cobertura-visual", _texto(cliente.get("/?so_quintal=1")))


def test_anuncio_que_saiu_do_ar_some_da_listagem(app, banco):
    import os
    import sqlite3

    rw = sqlite3.connect(os.environ["CACA_DB"])
    url = rw.execute("SELECT url FROM anuncios WHERE dup_grupo IS NULL LIMIT 1").fetchone()[0]
    antes = _total(app.test_client().get("/"))
    rw.execute("UPDATE anuncios SET removido_em = datetime('now') WHERE url = ?", (url,))
    rw.commit()
    try:
        assert _total(app.test_client().get("/")) == antes - 1
    finally:
        rw.execute("UPDATE anuncios SET removido_em = NULL WHERE url = ?", (url,))
        rw.commit()
        rw.close()


def test_pagina_do_anuncio_mostra_o_venal_do_iptu(cliente, banco):
    url = banco.execute(
        "SELECT anuncio_url FROM venal_iptu WHERE nivel = 'lote' LIMIT 1").fetchone()[0]
    h = _texto(cliente.get("/anuncio/" + urllib.parse.quote(url, safe="")))
    assert "Valor venal (IPTU 2026)" in h
    assert "este imóvel" in h and "construído em" in h
    assert "Valor de referência do ITBI" in h


# ---------------------------------------------------------------------------
# Trocar de página preserva TODOS os valores dos filtros de múltipla escolha
# ---------------------------------------------------------------------------
def _query_dos_links_de_pagina(html: str) -> list[dict]:
    from urllib.parse import parse_qs, urlparse

    links = re.findall(r'<a class="pag-(?:num|seta)" href="([^"]+)"', html)
    return [parse_qs(urlparse(htmlmod.unescape(h)).query) for h in links]


def test_paginacao_preserva_varios_bairros(cliente):
    """Bug: ?bairro=A&bairro=B&...&pagina=2 mantinha só o primeiro bairro."""
    q = [("bairro", "Lapa"), ("bairro", "Pirituba"), ("bairro", "Vila Mangalot"),
         ("todas", "1"), ("ordem", "encaixe")]
    r = cliente.get("/", query_string=q)
    links = _query_dos_links_de_pagina(_texto(r))
    assert links, "a busca deveria ter mais de uma página"
    for params in links:
        assert params["bairro"] == ["Lapa", "Pirituba", "Vila Mangalot"]
        assert params["todas"] == ["1"] and params["ordem"] == ["encaixe"]
        assert len(params["pagina"]) == 1          # a página não se repete


def test_pagina_2_continua_com_os_mesmos_bairros(cliente):
    q = [("bairro", "Lapa"), ("bairro", "Pirituba"), ("bairro", "Vila Mangalot"), ("todas", "1")]
    p1 = _total(cliente.get("/", query_string=q))
    r2 = cliente.get("/", query_string=[*q, ("pagina", "2")])
    assert _total(r2) == p1                      # mesmo conjunto, só outra página
    assert 'name="bairro" value="Pirituba" checked' in _achata(_texto(r2))
