"""Plano do `caca-atualizar`: quais etapas rodam, em que ordem."""

import argparse

import pytest

from cacaimoveis import atualizar


def _args(**kw):
    base = dict(completo=False, imovelweb=False, provedor="claude", teto=10.0,
                limite_visao=0, max_paginas=0, so=None, pular=None)
    base.update(kw)
    return argparse.Namespace(**base)


def _plano(**kw):
    args = _args(**kw)
    return [e.nome for e in atualizar.escolher(atualizar.montar_etapas(args), args)]


def test_plano_diario_na_ordem_certa():
    assert _plano() == ["backup", "coleta", "fotos", "duplicatas", "endereco",
                        "entorno", "visao", "itbi", "venal", "venal-iptu", "area"]


def test_completo_inclui_as_mensais_antes_da_comparacao():
    p = _plano(completo=True)
    assert p.index("itbi-baixar") < p.index("iptu") < p.index("ajustes") < p.index("itbi")
    assert p.index("iptu") < p.index("venal-iptu")
    # camadas novas ANTES de calcular o entorno com elas
    assert p.index("camadas") < p.index("entorno")


def _plano_da_linha_de_comando(capsys, *flags):
    atualizar.main(["--listar", *flags])
    linhas = capsys.readouterr().out.split("Plano:")[1].splitlines()
    return [ln.split()[1] for ln in linhas if ln.strip() and ln.split()[0].rstrip(".").isdigit()]


def test_imovelweb_roda_no_mensal_ou_quando_pedido(capsys):
    """Medido: 2-3 min por bairro, Cloudflare barra depois de ~4 páginas e metade
    do que traz já existe em outro portal — não compensa todo dia."""
    assert "imovelweb" not in _plano_da_linha_de_comando(capsys)
    assert "imovelweb" in _plano_da_linha_de_comando(capsys, "--imovelweb")
    assert "imovelweb" in _plano_da_linha_de_comando(capsys, "--completo")
    assert "imovelweb" not in _plano_da_linha_de_comando(capsys, "--completo", "--sem-imovelweb")
    p = _plano_da_linha_de_comando(capsys, "--imovelweb")
    assert p.index("imovelweb") < p.index("coleta")


def test_etapa_imovelweb_so_visita_detalhe_com_parada_por_bloqueio():
    args = _args(imovelweb=True)
    (etapa,) = [e for e in atualizar.montar_etapas(args) if e.nome == "imovelweb"]
    assert etapa.comandos == [["main"], ["enriquecer_detalhes", "--parar-apos", "5"]]


def test_cloudflare_aparece_com_instrucao_e_nao_para_o_resto(monkeypatch, tmp_path):
    monkeypatch.setattr(atualizar, "TRAVA", str(tmp_path / "trava"))
    rodadas = []

    def falso(cmd):
        rodadas.append(cmd[0])
        return 3 if cmd[0] == "main" else 0

    monkeypatch.setattr(atualizar, "_rodar_modulo", falso)
    etapa = [e for e in atualizar.montar_etapas(_args(imovelweb=True)) if e.nome == "imovelweb"][0]
    situacao, detalhe = atualizar.rodar(etapa, _args(imovelweb=True))
    assert situacao == "falhou" and "Cloudflare" in detalhe and "--dry-run" in detalhe
    # sem a coleta, os detalhes não rodam (o bloqueio vale para os dois)
    assert rodadas == ["main"]
    assert atualizar.main(["--so", "imovelweb", "venal"]) == 1
    assert rodadas[-1] == "valor_venal"


def test_so_e_pular():
    assert _plano(so=["visao", "itbi"]) == ["visao", "itbi"]
    assert "coleta" not in _plano(pular=["coleta"])


def test_provedor_chega_na_etapa_de_visao():
    args = _args(provedor="deepseek", limite_visao=5)
    (visao,) = [e for e in atualizar.montar_etapas(args) if e.nome == "visao"]
    assert visao.comandos == [["analisar_visao", "--provedor", "deepseek",
                               "--limite", "5", "--teto", "10.0", "--sim"]]


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
