"""Plano do `caca-atualizar`: quais etapas rodam, em que ordem."""

import argparse

import pytest

from cacaimoveis import atualizar


def _args(**kw):
    base = dict(completo=False, imovelweb=False, provedor="claude", teto=20.0,
                limite_visao=0, max_paginas=0, so=None, pular=None)
    base.update(kw)
    return argparse.Namespace(**base)


def _plano(**kw):
    args = _args(**kw)
    return [e.nome for e in atualizar.escolher(atualizar.montar_etapas(args), args)]


def test_plano_diario_na_ordem_certa():
    assert _plano() == ["backup", "coleta", "fotos", "duplicatas", "endereco",
                        "visao", "itbi", "venal", "venal-iptu", "area"]


def test_completo_inclui_as_mensais_antes_da_comparacao():
    p = _plano(completo=True)
    assert p.index("itbi-baixar") < p.index("iptu") < p.index("ajustes") < p.index("itbi")
    assert p.index("iptu") < p.index("venal-iptu")


def test_imovelweb_so_quando_pedido():
    assert "imovelweb" not in _plano()
    assert _plano(imovelweb=True).index("imovelweb") < _plano(imovelweb=True).index("fotos")


def test_so_e_pular():
    assert _plano(so=["visao", "itbi"]) == ["visao", "itbi"]
    assert "coleta" not in _plano(pular=["coleta"])


def test_provedor_chega_na_etapa_de_visao():
    args = _args(provedor="deepseek", limite_visao=5)
    (visao,) = [e for e in atualizar.montar_etapas(args) if e.nome == "visao"]
    assert visao.comandos == [["analisar_visao", "--provedor", "deepseek",
                               "--limite", "5", "--teto", "20.0"]]


def test_etapa_desconhecida_e_recusada(capsys):
    assert atualizar.main(["--so", "nada", "--listar"]) == 2


def test_falha_numa_etapa_nao_para_as_seguintes(monkeypatch, tmp_path):
    monkeypatch.setattr(atualizar, "TRAVA", str(tmp_path / "trava"))
    rodadas = []

    def falso(cmd):
        rodadas.append(cmd[0])
        return 1 if cmd[0] == "herdar_endereco" else 0

    monkeypatch.setattr(atualizar, "_rodar_modulo", falso)
    assert atualizar.main(["--so", "endereco", "venal"]) == 1
    assert rodadas == ["herdar_endereco", "valor_venal"]
    assert not (tmp_path / "trava").exists()


def test_trava_impede_duas_execucoes(monkeypatch, tmp_path):
    monkeypatch.setattr(atualizar, "TRAVA", str(tmp_path / "trava"))
    (tmp_path / "trava").write_text("123")
    assert atualizar.main(["--so", "venal"]) == 3


@pytest.mark.parametrize("codigo", [130])
def test_ctrl_c_no_filho_interrompe(monkeypatch, tmp_path, codigo):
    monkeypatch.setattr(atualizar, "TRAVA", str(tmp_path / "trava"))
    rodadas = []
    monkeypatch.setattr(atualizar, "_rodar_modulo", lambda cmd: rodadas.append(cmd) or codigo)
    assert atualizar.main(["--so", "venal", "area"]) == 1
    assert len(rodadas) == 1
