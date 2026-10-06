"""Valor venal do IPTU pela fórmula da lei, sobre um CSV no formato do GeoSampa."""

import datetime
import sqlite3
import zipfile

import pytest

from cacaimoveis import iptu, migracoes

CABECALHO = (
    "NUMERO DO CONTRIBUINTE;ANO DO EXERCICIO;NUMERO DA NL;DATA DO CADASTRAMENTO;"
    "NUMERO DO CONDOMINIO;CODLOG DO IMOVEL;NOME DE LOGRADOURO DO IMOVEL;NUMERO DO IMOVEL;"
    "COMPLEMENTO DO IMOVEL;BAIRRO DO IMOVEL;REFERENCIA DO IMOVEL;CEP DO IMOVEL;"
    "QUANTIDADE DE ESQUINAS/FRENTES;FRACAO IDEAL;AREA DO TERRENO;AREA CONSTRUIDA;AREA OCUPADA;"
    "VALOR DO M2 DO TERRENO;VALOR DO M2 DE CONSTRUCAO;ANO DA CONSTRUCAO CORRIGIDO;"
    "QUANTIDADE DE PAVIMENTOS;TESTADA PARA CALCULO;TIPO DE USO DO IMOVEL;"
    "TIPO DE PADRAO DA CONSTRUCAO;TIPO DE TERRENO;FATOR DE OBSOLESCENCIA;"
    "ANO DE INICIO DA VIDA DO CONTRIBUINTE;MES DE INICIO DA VIDA DO CONTRIBUINTE;"
    "FASE DO CONTRIBUINTE"
)


def _linha(sql, rua, num, cep, at, ac, vt, vc, testada, padrao="Residencial horizontal - padrão B",
           tipo="Normal", fobs="0.70", esquinas="0", fracao="1.0000", ano="1985"):
    return (f"{sql};2026;1;01/01/26;00-0;0;{rua};{num};;LAPA;;{cep};{esquinas};{fracao};"
            f"{at};{ac};{ac};{vt};{vc};{ano};1;{testada};Residência;{padrao};{tipo};{fobs};1990;1;0")


@pytest.mark.parametrize("area, testada, esperado", [
    (100, 10, 0.7071),    # p = 10 (piso da tabela)
    (110, 10, 0.7416),    # p = 11
    (300, 10, 1.0),       # p = 30
    (410, 10, 0.9877),    # p = 41
    (800, 10, 0.7071),    # p = 80
    (5000, 10, 0.4472),   # p > 200 (teto)
])
def test_fator_de_profundidade_bate_com_a_tabela_i(area, testada, esperado):
    assert iptu.fator_profundidade(area, testada) == pytest.approx(esperado, abs=1e-4)


def test_duas_esquinas_tem_profundidade_um():
    assert iptu.fator_profundidade(800, 10, n_esquinas=2) == 1.0


def test_formula_do_venal():
    terreno, construcao = iptu.venal(250, 120, 1000, 2000, testada=10,
                                     tipo_terreno="Lote de fundos", fator_obsolescencia=0.7)
    assert terreno == pytest.approx(250 * 1000 * 1.0 * 0.6)   # p = 25 -> 1,0
    assert construcao == pytest.approx(120 * 2000 * 0.7)


@pytest.fixture()
def conn_iptu(tmp_path):
    arquivo = tmp_path / "IPTU_2026.zip"
    linhas = [
        CABECALHO,
        # a casa do anúncio: Rua Doutor Elias, 50 ("R DR ELIAS" no cadastro)
        _linha("1", "R DR ELIAS", "0050", "05001-000", 200, 150, 1500, 2500, 8),
        _linha("2", "R DR ELIAS", "50", "05001-000", 120, 60, 1500, 2500, 6),
        # vizinhas, para a mediana da rua
        *[_linha(str(10 + i), "R DR ELIAS", str(100 + i), "05001-000", 180, 120,
                 1400 + 10 * i, 2400, 9) for i in range(6)],
        # apartamento: fica de fora
        _linha("99", "R DR ELIAS", "70", "05001-000", 1000, 80, 1500, 3000, 20,
               padrao="Residencial vertical - padrão C"),
    ]
    with zipfile.ZipFile(arquivo, "w") as z:
        z.writestr("IPTU_2026.csv", "\n".join(linhas))
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    migracoes.migrar(conn)
    n = iptu.ingerir(conn, str(arquivo), verbose=False)
    assert n == 8   # só as casas
    return conn


def _anuncio(conn, url, rua, at, ac, cep="", preco=800_000):
    conn.execute(
        "INSERT INTO anuncios (url, rua, cep, area_terreno, area_construida, preco) "
        "VALUES (?,?,?,?,?,?)", (url, rua, cep, at, ac, preco))


def test_anuncio_casa_com_o_lote_pelo_endereco(conn_iptu):
    _anuncio(conn_iptu, "a", "Rua Doutor Elias, 50", 200, 140)
    iptu.calcular(conn_iptu, verbose=False)
    r = conn_iptu.execute("SELECT * FROM venal_iptu WHERE anuncio_url='a'").fetchone()
    assert r["nivel"] == "lote"
    assert r["sql"] == "1"       # das duas unidades no nº 50, a de área mais próxima
    # profundidade 200/8 = 25 m: faixa 20-40, fator 1,0
    esperado = 200 * 1500 * 1.0 + 150 * 2500 * 0.70
    assert r["valor_venal"] == pytest.approx(esperado, rel=1e-3)
    assert r["ano_construcao"] == 1985
    assert r["ano_base"] == 2026


def test_sem_numero_usa_as_casas_parecidas_da_rua(conn_iptu):
    # sem área de terreno (o caso de 99% dos anúncios) e com 120 m²
    _anuncio(conn_iptu, "b", "Rua Doutor Elias", None, 120)
    iptu.calcular(conn_iptu, verbose=False)
    r = conn_iptu.execute("SELECT * FROM venal_iptu WHERE anuncio_url='b'").fetchone()
    assert r["nivel"] == "rua"
    # entram as 6 vizinhas de 120 m² e a de 150 m² (limite exato de +25%);
    # a de 60 m² fica de fora
    assert r["n_lotes"] == 7
    vizinhas = conn_iptu.execute(
        "SELECT venal_terreno + venal_construcao FROM iptu "
        "WHERE area_construida BETWEEN 90 AND 150").fetchall()
    import statistics
    assert r["valor_venal"] == pytest.approx(statistics.median(v[0] for v in vizinhas), rel=1e-6)


def test_novo_ano_refaz_os_anuncios(conn_iptu):
    _anuncio(conn_iptu, "a", "Rua Doutor Elias, 50", 200, 140)
    iptu.calcular(conn_iptu, verbose=False)
    migracoes.gravar_meta(conn_iptu, "iptu_ano", "2027")
    assert iptu.calcular(conn_iptu, verbose=False) == 1


def test_rotina_avisa_quando_falta_o_ano_corrente(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(iptu, "PASTA", str(tmp_path))
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    migracoes.migrar(conn)
    ano = datetime.date.today().year
    migracoes.gravar_meta(conn, "iptu_ano", str(ano - 1))
    resumo = iptu.rotina(conn, verbose=False)
    assert f"IPTU {ano} não baixado" in resumo
    assert f"IPTU_{ano}.zip" in capsys.readouterr().out


def test_lote_com_area_muito_diferente_cai_para_a_rua(conn_iptu):
    """Número de endereço errado: o lote do nº 50 tem 150 m², o anúncio 600."""
    _anuncio(conn_iptu, "c", "Rua Doutor Elias, 50", None, 600)
    iptu.calcular(conn_iptu, verbose=False)
    assert conn_iptu.execute(
        "SELECT nivel FROM venal_iptu WHERE anuncio_url='c'").fetchone() is None or \
        conn_iptu.execute("SELECT nivel FROM venal_iptu WHERE anuncio_url='c'").fetchone()[0] != "lote"
