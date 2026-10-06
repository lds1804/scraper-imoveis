"""Leitura dos filtros da URL e montagem do SQL, sem passar pelo Flask."""

import pytest
from werkzeug.datastructures import MultiDict

from cacaimoveis import filtros as flt


def test_le_multipla_escolha_e_descarta_numero_invalido():
    f = flt.Filtros.da_url(MultiDict([
        ("bairro", "Lapa"), ("bairro", " Pirituba "), ("bairro", ""),
        ("preco_max", "abc"), ("terreno_min", "250"), ("quartos_min", "2,5"),
        ("so_quintal", "1"), ("so_arvores", "true"),
    ]))
    assert f.bairro == ["Lapa", "Pirituba"]
    assert f.preco_max == ""          # inválido: sem filtro
    assert f.terreno_min == "250"
    assert f.quartos_min == ""        # "2,5" não é inteiro
    assert f.so_quintal is True
    assert f.so_arvores is False      # só "1" liga
    assert f.ordem == flt.ORDEM_PADRAO == "encaixe"


def test_ordem_encaixe_bate_com_a_formula_em_python(banco):
    """A nota calculada no SQL é a mesma de `nota_encaixe` em Python."""
    flt.registrar_funcoes(banco)
    sql, params = flt.montar_consulta(banco, flt.Filtros(), tem_comp=True, tem_dup=True)
    linhas = banco.execute(sql.replace("SELECT a.*", "SELECT a.*, c.razao AS _r", 1),
                           params).fetchall()
    notas = [r["_nota"] for r in linhas]
    confs = dict(banco.execute("SELECT anuncio_url, confianca FROM comparacoes").fetchall())
    for r in linhas:
        assert r["_nota"] == flt.nota_encaixe(dict(r), r["_r"], confs.get(r["url"]))
    com_nota = [n for n in notas if n is not None]
    assert com_nota == sorted(com_nota, reverse=True)
    # quem não tem nota vai para o fim
    assert notas[len(com_nota):] == [None] * (len(notas) - len(com_nota))


@pytest.mark.parametrize("valores, esperado", [
    (["agua-branca"], ["Água Branca"]),
    (["JARDIM IRIS"], ["Jardim Íris"]),
    (["Lapa", "lapa"], ["Lapa"]),
    (["Jaguara"], []),
])
def test_resolver_bairros(banco, valores, esperado):
    assert flt.resolver_bairros(banco, valores) == esperado


def test_bairros_prioritarios_vem_primeiro_na_ordem_do_config():
    nomes = ["Água Branca", "City América", "Lapa", "Parque Maria Domitila",
             "Parque São Domingos", "Pirituba", "Vila Mangalot", "Jardim Íris"]
    ordenados, n = flt.ordenar_bairros(nomes)
    assert n == 4
    assert ordenados[:4] == ["Vila Mangalot", "Parque São Domingos",
                             "City América", "Parque Maria Domitila"]
    assert ordenados[4:] == ["Água Branca", "Jardim Íris", "Lapa", "Pirituba"]   # sem acento


def test_prioritario_que_nao_existe_na_base_e_ignorado():
    ordenados, n = flt.ordenar_bairros(["Lapa", "Vila Mangalot"])
    assert ordenados == ["Vila Mangalot", "Lapa"] and n == 1
    assert flt.ordenar_bairros(["Lapa", "Pirituba"]) == (["Lapa", "Pirituba"], 0)


def test_prioritarios_casam_sem_acento_e_sem_caixa(monkeypatch):
    monkeypatch.setattr(flt.config, "BAIRROS_PRIORITARIOS", ("city america", "VILA MANGALOT"))
    ordenados, n = flt.ordenar_bairros(["Lapa", "Vila Mangalot", "City América"])
    assert ordenados == ["City América", "Vila Mangalot", "Lapa"] and n == 2
