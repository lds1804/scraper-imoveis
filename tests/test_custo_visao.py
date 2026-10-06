"""Teto de gasto da análise visual (DeepSeek).

O teto é a única proteção contra uma conta inesperada: se calcular errado, ou
aborta cedo (perdendo trabalho) ou deixa passar do combinado.
"""

import datetime

import pytest

from cacaimoveis import analisar_visao as av

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


# ---------------------------------------------------------------------------
# Confirmação antes de gastar, e gasto na tela
# ---------------------------------------------------------------------------
def _executa(monkeypatch, capsys, *args, resposta="n", tty=True):
    """Roda caca-visao (DeepSeek simulada) e devolve (saída, chamadas, perguntas)."""
    import sys
    import types

    from cacaimoveis import visao

    chamadas, perguntas = [], []

    def analisa(url, fotos, **kw):
        chamadas.append(url)
        return visao.AnaliseFoto(url=url, ok=True, resumo="x", cuidado_nota=3, confianca="alta")

    monkeypatch.setattr(av, "analisar_anuncio", analisa)
    monkeypatch.setattr(av, "provedor_pronto", lambda p: None)
    monkeypatch.setattr(av, "BRL", 1000.0)          # estimativa alta o bastante para perguntar
    monkeypatch.setattr(sys, "stdin", types.SimpleNamespace(isatty=lambda: tty))
    monkeypatch.setattr("builtins.input", lambda *a: perguntas.append(a[0]) or resposta)
    monkeypatch.setattr(sys, "argv", ["caca-visao", "--provedor", "deepseek", "--sem-agrupar",
                                      "--limite", "6", "--teto", "1000000", *args])
    av.main()
    return capsys.readouterr().out, chamadas, perguntas


def _volta_a_pendente(url_like="%"):
    """Os testes dividem o mesmo banco: devolve os anúncios à fila ao terminar."""
    import os
    import sqlite3

    conn = sqlite3.connect(os.environ["CACA_DB"])
    conn.execute("UPDATE anuncios SET foto_ok = NULL WHERE url LIKE ? AND foto_modelo IS NULL "
                 "AND foto_cuidado = 3", (url_like,))
    conn.commit()
    conn.close()


def test_pergunta_antes_de_gastar_e_cancela(monkeypatch, capsys):
    saida, chamadas, perguntas = _executa(monkeypatch, capsys, resposta="n")
    assert len(perguntas) == 1 and "DeepSeek" in perguntas[0] and "teto" in perguntas[0]
    assert chamadas == [] and "Nada foi gasto" in saida


def test_sim_dispensa_a_pergunta_e_mostra_o_gasto(monkeypatch, capsys):
    monkeypatch.setattr(av, "AVISO_DE_GASTO_A_CADA", 1)
    saida, chamadas, perguntas = _executa(monkeypatch, capsys, "--sim")
    assert perguntas == [] and chamadas
    assert "[gasto]" in saida                       # o gasto aparece na tela


def test_sem_teclado_nao_pergunta(monkeypatch, capsys):
    """Execução automática (ex.: agendada): não trava esperando resposta."""
    _, _, perguntas = _executa(monkeypatch, capsys, tty=False)
    assert perguntas == []


def test_teto_padrao_da_deepseek_e_10_reais():
    assert av.TETO_PADRAO_BRL == 10.0
