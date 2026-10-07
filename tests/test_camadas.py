"""Camadas do GeoSampa: geometria e contexto de um ponto, com polígonos inventados."""

import json
import sqlite3

import pytest

from cacaimoveis import camadas

QUADRADO = {"t": "Polygon", "c": [[[-46.70, -23.50], [-46.70, -23.51], [-46.71, -23.51],
                                   [-46.71, -23.50], [-46.70, -23.50]]]}
COM_BURACO = {"t": "Polygon", "c": [
    [[-46.70, -23.50], [-46.70, -23.51], [-46.71, -23.51], [-46.71, -23.50], [-46.70, -23.50]],
    [[-46.704, -23.504], [-46.704, -23.506], [-46.706, -23.506], [-46.706, -23.504],
     [-46.704, -23.504]]]}


def test_ponto_dentro_e_fora_do_poligono():
    assert camadas.dentro(-23.505, -46.705, QUADRADO)
    assert not camadas.dentro(-23.52, -46.705, QUADRADO)


def test_buraco_do_poligono_nao_conta_como_dentro():
    assert camadas.dentro(-23.501, -46.701, COM_BURACO)
    assert not camadas.dentro(-23.505, -46.705, COM_BURACO)


def test_distancia_em_metros():
    # 0,001° de latitude ~ 110 m
    assert camadas.dist_m(-23.5, -46.7, -23.501, -46.7) == pytest.approx(110.5, abs=1)
    # 0,001° de latitude ao norte do quadrado
    assert camadas.dist_ao_contorno(-23.499, -46.705, QUADRADO) == pytest.approx(110.5, abs=1)
    linha = {"t": "LineString", "c": [[-46.70, -23.50], [-46.69, -23.50]]}
    assert camadas.dist_linha(-23.501, -46.695, linha) == pytest.approx(110.5, abs=1)


def _indice(tipo, itens):
    ix = camadas.Indice(tipo)
    for props, g in itens:
        ix.adicionar(props, g, camadas._extremos(g["c"]))
    return ix


def test_indice_acha_o_mais_proximo_dentro_do_raio():
    pontos = _indice("ponto", [
        ({"nm": "perto"}, {"t": "Point", "c": [-46.700, -23.501]}),
        ({"nm": "longe"}, {"t": "Point", "c": [-46.650, -23.450]}),
    ])
    d, p = pontos.mais_proximo(-23.500, -46.700, 500)
    assert p["nm"] == "perto" and d == pytest.approx(110.5, abs=1)
    assert pontos.mais_proximo(-23.500, -46.700, 50) is None
    assert pontos.quantos_ate(-23.500, -46.700, 300) == 1


def _ix_vazio():
    return {n: camadas.Indice(c.tipo) for n, c in camadas.CAMADAS.items()}


def test_contexto_usa_so_o_que_achou():
    ix = _ix_vazio()
    ix["zona"].adicionar({"cd_zoneamento_perimetro": "ZM", "tx_zoneamento_perimetro": "Zona Mista"},
                         QUADRADO, camadas._extremos(QUADRADO["c"]))
    ix["metro"].adicionar({"nm_estacao_metro_trem": "LAPA", "nm_linha_metro_trem": "X"},
                          {"t": "Point", "c": [-46.705, -23.506]}, (-46.705, -23.506, -46.705, -23.506))
    r = camadas.contexto(ix, -23.505, -46.705)
    assert r["zona"] == {"sigla": "ZM", "nome": "Zona Mista"}
    assert r["metro"]["nome"] == "LAPA" and r["metro"]["dist_m"] < 200
    assert r["inundavel"] is False and r["alagamentos_300m"] == 0
    assert "trem" not in r and "risco_geo" not in r and "favela" not in r


def test_contaminacao_potencial_so_a_30_metros():
    """O 'potencial' do cadastro é qualquer posto ou indústria: não vale a 100 m."""
    ix = _ix_vazio()
    ix["contaminada_potencial"].adicionar(
        {"dc_tipo_situacao": "POTENCIAL DE CONTAMINAÇÃO", "dc_atividade": "Posto"},
        QUADRADO, camadas._extremos(QUADRADO["c"]))
    # ~110 m ao norte do quadrado: fora dos 30 m
    assert "contaminada" not in camadas.contexto(ix, -23.499, -46.705)
    # dentro: entra, marcado como potencial
    assert camadas.contexto(ix, -23.505, -46.705)["contaminada"]["potencial"] is True


def test_contaminada_de_verdade_vale_ate_150_metros():
    ix = _ix_vazio()
    ix["contaminada"].adicionar(
        {"dc_classificacao_area_contaminada": "Contaminada", "tx_endereco_area_contaminada": "X"},
        QUADRADO, camadas._extremos(QUADRADO["c"]))
    c = camadas.contexto(ix, -23.499, -46.705)["contaminada"]
    assert c["potencial"] is False and c["dist_m"] == pytest.approx(110.5, abs=1)


def test_baixar_e_carregar_ida_e_volta(monkeypatch):
    feat = {"geometry": {"type": "Polygon", "coordinates": QUADRADO["c"]},
            "properties": {"cd_zoneamento_perimetro": "ZER-1", "tx_zoneamento_perimetro": "Residencial",
                           "ruido": "x"}}
    monkeypatch.setattr(camadas, "_pedir", lambda c, b: [feat, {"geometry": None, "properties": {}}])
    conn = camadas.conectar(":memory:")
    assert camadas.baixar(conn, "zona", (-23.6, -46.8, -23.4, -46.6)) == 1
    ix = camadas.carregar(conn)
    assert ix["zona"].contem(-23.505, -46.705)[0]["cd_zoneamento_perimetro"] == "ZER-1"
    assert "ruido" not in ix["zona"].itens[0][0]       # só os campos pedidos
    assert json.loads(conn.execute("SELECT bbox FROM baixada").fetchone()[0])[0] == -23.6


def test_so_se_filtra_feicoes_na_origem(monkeypatch):
    def ponto(sit):
        return {"geometry": {"type": "Point", "coordinates": [-46.7, -23.5]},
                "properties": {"nm_estacao_metro_trem": "A", "tx_situacao_metro_trem": sit}}

    monkeypatch.setattr(camadas, "_pedir", lambda c, b: [ponto("OPERANDO"), ponto("EM OBRA")])
    conn = camadas.conectar(":memory:")
    assert camadas.baixar(conn, "metro", (-24, -47, -23, -46)) == 1


def test_calcular_nao_inventa_risco_fora_do_recorte(monkeypatch):
    """Ponto fora do que foi baixado fica SEM contexto (e não 'sem risco')."""
    imoveis = sqlite3.connect(":memory:")
    imoveis.execute("""CREATE TABLE anuncios (url TEXT, rua TEXT, bairro TEXT, cep TEXT,
                       rua_chave TEXT, removido_em TEXT)""")
    imoveis.execute("INSERT INTO anuncios VALUES ('u1','Rua A, 1','B','','A',NULL)")
    imoveis.execute("INSERT INTO anuncios VALUES ('u2','Rua B, 1','B','','B',NULL)")
    cam = camadas.conectar(":memory:")
    cam.execute("INSERT INTO baixada VALUES ('zona', ?, 0, 'x')", (json.dumps([-23.6, -46.8, -23.4, -46.6]),))
    from cacaimoveis import geocode
    pos = {"A": {"lat": -23.5, "lon": -46.7, "precisao": "lote"},
           "B": {"lat": -23.9, "lon": -46.1, "precisao": "lote"}}
    monkeypatch.setattr(geocode, "localizar",
                        lambda conn, rua, bairro, cep, chave, consultar_rede=True: pos[chave])
    feitos, fora = camadas.calcular(imoveis, cam)
    assert (feitos, fora) == (1, 1)
    assert [r[0] for r in imoveis.execute("SELECT anuncio_url FROM contexto")] == ["u1"]
