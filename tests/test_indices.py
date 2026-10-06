"""Reajuste pelo IGP-M e janela de 10 anos na comparação com o ITBI.

A comparação juntava vendas de 2007 e de 2020 na MESMA mediana, e um anúncio
praticamente no preço aparecia "212% acima do preço praticado". Dois defeitos
somados: o índice (IPCA, inflação geral) e a falta de corte de época.

A série vem de `tests/fixtures/indices.json` (dado público do BCB), então
estes testes não dependem de rede.
"""

import pytest

import comparar_itbi
import indices


@pytest.fixture(scope="module")
def igpm():
    return indices.Reajustador("igpm", verbose=False)


@pytest.fixture(scope="module")
def ipca():
    return indices.Reajustador("ipca", verbose=False)


def test_serie_igpm_carregada(igpm):
    assert igpm.nome == "igpm"
    assert len(igpm.serie) > 300


@pytest.mark.parametrize("ano, oficial", [
    ("2020", 23.14), ("2021", 17.79), ("2022", 5.45), ("2023", -3.18), ("2024", 6.54),
])
def test_igpm_acumulado_bate_com_o_oficial(igpm, ano, oficial):
    """O BCB publica variação % mensal; somar em vez de compor daria absurdo."""
    ant, fim = f"{int(ano) - 1}12", f"{ano}12"
    calc = (igpm.serie[fim] / igpm.serie[ant] - 1) * 100
    assert calc == pytest.approx(oficial, abs=0.1)


def test_referencia_e_o_mes_mais_recente(igpm):
    assert igpm.referencia == max(igpm.serie)


def test_fator_cresce_com_a_distancia(igpm):
    assert igpm.fator("20060101") > igpm.fator("20150101") > igpm.fator("20250101") > 1.0


@pytest.mark.parametrize("data", ["20070313", "20070711"])
def test_igpm_reajusta_mais_que_ipca(igpm, ipca, data):
    assert igpm.fator(data) > ipca.fator(data)


def test_caso_real_que_denunciou_o_defeito(igpm):
    """R PEDRO FERREIRA DE SOUZA, 160 m²: R$ 100 mil em 2007, R$ 600 mil em 2020."""
    v2007 = igpm.reajustar(100_000, "20070313")
    v2020 = igpm.reajustar(600_000, "20200624")
    assert 300_000 < v2007 < 400_000
    assert 850_000 < v2020 < 1_000_000
    assert v2020 / 160 > 2 * (v2007 / 160)


def test_limiares_documentados():
    """Números que saíram de medição; mudar sem medir quebra este teste."""
    assert comparar_itbi.JANELA_ANOS == 10
    assert comparar_itbi.MIN_DENTRO_JANELA >= 1
    assert comparar_itbi.MIN_TRANSACOES == 3
    assert comparar_itbi.TOLERANCIA_AREA == 0.25


@pytest.mark.dados_reais
def test_banco_real_usa_igpm_e_janela(banco_real, igpm, ipca):
    c = banco_real.execute(
        """SELECT * FROM comparacoes
           WHERE ano_mais_antigo IS NOT NULL AND ano_mais_novo IS NOT NULL
           ORDER BY n_transacoes DESC LIMIT 1""").fetchone()
    assert c is not None, "nenhuma comparação com ano registrado (rode --atualizar)"
    assert c["indice_reajuste"] == "igpm"
    assert c["ano_mais_novo"] >= c["ano_mais_antigo"]

    ruins, total = banco_real.execute(
        """SELECT SUM(ano_mais_novo - ano_mais_antigo > 10), COUNT(*)
           FROM comparacoes WHERE ano_mais_antigo IS NOT NULL""").fetchone()
    # as que misturam +10 anos são as que caíram no fallback
    assert total > 0 and 100 * ruins / total < 60

    ok, motivo = comparar_itbi.esta_atualizada(banco_real, igpm)
    assert ok, motivo
    ok2, _ = comparar_itbi.esta_atualizada(banco_real, ipca)
    assert not ok2, "índice diferente deveria pedir recálculo"
