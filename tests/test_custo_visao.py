"""Teto de gasto da análise visual (DeepSeek).

O teto é a única proteção contra uma conta inesperada: se calcular errado, ou
aborta cedo (perdendo trabalho) ou deixa passar do combinado.
"""

import datetime

import pytest

import analisar_visao as av

UTC = datetime.UTC


@pytest.mark.parametrize("quando, esperado", [
    (datetime.datetime(2026, 10, 6, 0, 30, tzinfo=UTC), False),
    (datetime.datetime(2026, 10, 6, 1, 0, tzinfo=UTC), True),
    (datetime.datetime(2026, 10, 6, 2, 0, tzinfo=UTC), True),
    (datetime.datetime(2026, 10, 6, 4, 0, tzinfo=UTC), False),
    (datetime.datetime(2026, 10, 6, 5, 0, tzinfo=UTC), False),
    (datetime.datetime(2026, 10, 6, 6, 0, tzinfo=UTC), True),
    (datetime.datetime(2026, 10, 6, 9, 59, tzinfo=UTC), True),
    (datetime.datetime(2026, 10, 6, 10, 0, tzinfo=UTC), False),
    (datetime.datetime(2026, 10, 6, 23, 0, tzinfo=UTC), False),
    # fim de semana é sempre barato, mesmo dentro da faixa de hora
    (datetime.datetime(2026, 10, 10, 2, 0, tzinfo=UTC), False),
    (datetime.datetime(2026, 10, 11, 7, 0, tzinfo=UTC), False),
])
def test_horario_de_pico(quando, esperado):
    """Pico: 01-04h e 06-10h UTC, de segunda a sexta."""
    assert av._e_peak(quando) is esperado


def test_custo_bate_com_a_conta_a_mao():
    # tokens medidos na API em 2026-10-03
    n_fotos, n_ads = 1000, 10
    ent = n_fotos * 458 + n_ads * av.TOK_PROMPT
    sai = n_ads * av.TOK_SAIDA
    off = (ent * 0.15 / 1e6 + sai * 0.60 / 1e6) * 5.42
    peak = (ent * 0.30 / 1e6 + sai * 1.20 / 1e6) * 5.42
    assert av._custo_brl(n_fotos, n_ads, "original") == pytest.approx(off, abs=0.01)
    assert av._custo_brl(n_fotos, n_ads, "original", peak=True) == pytest.approx(peak, abs=0.01)


def test_pico_custa_o_dobro():
    razao = av._custo_brl(1000, 10, "original", peak=True) / av._custo_brl(1000, 10, "original")
    assert razao == pytest.approx(2.0, abs=0.001)


def test_tokens_por_foto_medidos():
    assert av.TOK_FOTO == {"low": 203, "original": 458}
    assert 0.4 < av._custo_brl(1000, 10, "low") / av._custo_brl(1000, 10, "original") < 0.5


def test_estimativa_confere_com_a_medicao():
    """Medido (medir_custo_analise.py): 1.602 chamadas, 22.165 fotos."""
    assert av._custo_brl(22165, 1602, "original") == pytest.approx(10.82, abs=0.05)
    assert av._custo_brl(22165, 1602, "original", peak=True) == pytest.approx(21.63, abs=0.05)


def test_teto_de_20_reais_cobre_a_lacuna_so_fora_do_pico():
    assert av._custo_brl(22165, 1602, "original") < 20.0
    assert av._custo_brl(22165, 1602, "original", peak=True) > 20.0
