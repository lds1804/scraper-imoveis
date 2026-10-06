"""Coleta incremental: parar de paginar quando só há anúncio já visto."""

import sqlite3

import pytest

from cacaimoveis import glue_api, migracoes, olx, quintoandar
from cacaimoveis import incremental as inc
from cacaimoveis.scraper_browser import Anuncio


@pytest.fixture()
def conn():
    c = sqlite3.connect(":memory:")
    migracoes.migrar(c)
    return c


def _urls(*ids):
    return [f"https://x/{i}" for i in ids]


def test_vistos_e_semeado_com_o_que_ja_esta_salvo():
    c = sqlite3.connect(":memory:")
    # banco antigo (versão 3): já tem anúncios, ainda não tem `vistos`
    for m in migracoes.MIGRACOES[:6]:
        m(c)
    c.execute("INSERT INTO anuncios (url, portal) VALUES ('https://x/1', 'zap')")
    migracoes._m007_vistos(c)
    assert c.execute("SELECT portal FROM vistos WHERE url = 'https://x/1'").fetchone()[0] == "zap"


def test_conta_so_o_que_ainda_nao_foi_visto(conn):
    i = inc.Incremental(conn, "zap")
    assert i.novos(_urls(1, 2, 3)) == 3
    i.registrar(_urls(1, 2))
    assert i.novos(_urls(1, 2, 3)) == 1
    assert i.novos(_urls(1, 1, 2)) == 0       # repetida na mesma página não conta dobrado


def test_para_depois_de_duas_paginas_sem_novidade(conn):
    i = inc.Incremental(conn, "zap")
    i.novo_bairro()
    assert not i.pode_parar(5)    # página com novidade
    assert not i.pode_parar(0)    # 1ª sem novidade
    assert i.pode_parar(0)        # 2ª seguida: encerra
    assert i.parados == 1


def test_uma_pagina_com_novidade_zera_a_contagem(conn):
    i = inc.Incremental(conn, "zap")
    i.novo_bairro()
    assert not i.pode_parar(0)
    assert not i.pode_parar(2)    # voltou a ter novidade
    assert not i.pode_parar(0)


def test_completa_nunca_para(conn):
    i = inc.Incremental(conn, "zap", completa=True)
    i.novo_bairro()
    assert all(not i.pode_parar(0) for _ in range(10))
    assert "completa" in i.resumo()


# ---------------------------------------------------------------------------
# Os três portais, de ponta a ponta com a rede simulada
# ---------------------------------------------------------------------------
def _anuncio(i):
    return Anuncio(url=f"https://x/{i}", titulo=f"Casa {i}")


def _paginas(monkeypatch, modulo, nome, por_pagina):
    """Faz `nome` devolver `por_pagina[pagina - 1]` e conta as chamadas."""
    chamadas = []

    def falsa(*args, **kw):
        pagina = args[1] if len(args) > 1 else kw.get("pagina", 1)
        chamadas.append(pagina)
        itens = por_pagina[pagina - 1] if pagina <= len(por_pagina) else []
        return [_anuncio(i) for i in itens], 10_000

    monkeypatch.setattr(modulo, nome, falsa)
    return chamadas


@pytest.mark.parametrize("portal", ["zap", "quinto"])
def test_portais_de_api_param_cedo_e_registram(conn, monkeypatch, portal):
    # páginas 1-2 já vistas, 3 também: deve parar depois da 2ª sem novidade
    paginas = [[1, 2], [3, 4], [5, 6], [7, 8]]
    i = inc.Incremental(conn, portal)
    i.registrar(_urls(1, 2, 3, 4, 5, 6, 7, 8))
    if portal == "zap":
        chamadas = _paginas(monkeypatch, glue_api, "buscar_pagina", paginas)
        saida = list(glue_api.coletar_bairro("Lapa", "zap", incremental=i))
    else:
        chamadas = _paginas(monkeypatch, quintoandar, "buscar_pagina", paginas)
        saida = list(quintoandar.coletar_bairro("Lapa", incremental=i))
    assert chamadas == [1, 2]                  # parou, em vez de 4 páginas
    assert len(saida) == 4                     # as páginas ainda são entregues
    assert i.parados == 1


def test_anuncio_novo_no_topo_e_entregue_e_registrado(conn, monkeypatch):
    i = inc.Incremental(conn, "zap")
    i.registrar(_urls(1, 2, 3, 4, 5, 6))
    chamadas = _paginas(monkeypatch, glue_api, "buscar_pagina", [[99, 1], [2, 3], [4, 5], [6]])
    saida = [a.url for a in glue_api.coletar_bairro("Lapa", "zap", incremental=i)]
    assert "https://x/99" in saida
    assert chamadas == [1, 2, 3]               # 99 era novo na pág. 1; 2 e 3 já vistas
    assert i.novos(_urls(99)) == 0             # e agora está registrado


def test_pagina_so_vira_vista_depois_de_processada(conn, monkeypatch):
    """Coleta que cai no meio da página refaz essa página na próxima rodada."""
    i = inc.Incremental(conn, "zap")
    _paginas(monkeypatch, glue_api, "buscar_pagina", [[1, 2, 3]])
    gerador = glue_api.coletar_bairro("Lapa", "zap", incremental=i)
    next(gerador)                              # consumidor pegou só o 1º e "caiu"
    assert i.novos(_urls(1, 2, 3)) == 3
    gerador.close()
    assert i.novos(_urls(1, 2, 3)) == 3


def test_olx_para_cedo(conn, monkeypatch):
    paginas = [[1, 2], [3, 4], [5, 6], [7, 8]]
    i = inc.Incremental(conn, "olx")
    i.registrar(_urls(*range(1, 9)))
    chamadas = []

    def abrir(page, url):
        chamadas.append(url)
        return "html"

    def cards(html, termo, cidade):
        return [_anuncio(n) for n in paginas[len(chamadas) - 1]]

    monkeypatch.setattr(olx, "_abrir", abrir)
    monkeypatch.setattr(olx, "parse_cards", cards)
    monkeypatch.setattr(olx.time, "sleep", lambda s: None)
    list(olx.coletar_bairro(None, "Lapa", incremental=i))
    assert len(chamadas) == 2
    assert all("sf=1" in u for u in chamadas)  # mais recentes primeiro


def test_ordenacao_por_mais_recentes_nos_tres_portais():
    assert "sf=1" in olx.montar_url("Lapa") and "sf=1" in olx.montar_url("Lapa", 3)
    assert quintoandar._corpo("x", 1, 20)["sorting"]["criteria"] == "MOST_RECENT"


@pytest.mark.parametrize("modulo", ["zap", "quintoandar_principal", "olx_principal"])
def test_a_flag_completa_chega_ate_a_coleta(monkeypatch, modulo):
    """`caca-atualizar --completo` repassa --completa: cada portal precisa
    aceitá-la e entregá-la a `coletar` (um parâmetro esquecido só aparece
    quando o comando roda de verdade)."""
    import importlib
    import sys

    mod = importlib.import_module(f"cacaimoveis.{modulo}")
    recebido = {}
    monkeypatch.setattr(mod, "coletar", lambda *a, **k: recebido.update(args=a, kw=k))
    monkeypatch.setattr(sys, "argv", [modulo, "--bairros", "lapa", "--completa"])
    mod.main()
    assert True in recebido["args"] or recebido["kw"].get("completa") is True
    monkeypatch.setattr(sys, "argv", [modulo, "--bairros", "lapa"])
    mod.main()
    assert True not in recebido["args"] and not recebido["kw"].get("completa")
