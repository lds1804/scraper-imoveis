"""Entorno do imóvel: risco, transporte, zoneamento e vizinhança (GeoSampa).

Para cada anúncio com coordenada (ver `geocode.py`), diz em que zona ele está,
se há risco de inundação/deslizamento por perto, a que distância ficam o metrô,
o trem, um parque, um hospital, etc. É contexto para o preço, não avaliação.

COMO FUNCIONA
-------------
1. `caca-camadas --baixar` baixa do WFS do GeoSampa só o RECORTE da região dos
   anúncios (bbox dos lotes geocodificados + margem) e guarda em
   `dados/camadas.db` (separado do `imoveis.db`, que já passa de 1 GB).
2. `caca-camadas --calcular` cruza cada anúncio com as camadas, em Python puro
   (ponto-em-polígono com grade de busca e distância em metros), e grava o
   resultado em `contexto`.

O QUE FICOU DE FORA, DE PROPÓSITO
---------------------------------
As camadas `mancha_inundacao_5/25/100` têm 154 a 210 mil polígonos SÓ na região
dos anúncios (grade de células de ~50 m²). Em vez delas valem `area_inundavel`
(11 polígonos, TR 100 anos) e as ocorrências já registradas de alagamento e
inundação (pontos).

Uso:
    caca-camadas --baixar
    caca-camadas --calcular
"""

from __future__ import annotations

import argparse
import json
import math
import sqlite3
import sys
import time
from collections import defaultdict
from dataclasses import dataclass, field

import requests

from cacaimoveis import config, logs

log = logs.obter(__name__)

WFS = "http://wfs.geosampa.prefeitura.sp.gov.br/geoserver/wfs"
CAMADAS_DB = config.caminho("dados", "camadas.db")
MARGEM = 0.03           # graus (~3 km) em volta dos anúncios (cobre o raio de 3 km do metrô)
LIMITE = 300000         # teto de feições por pedido (maior que qualquer camada daqui)
CELULA = 0.004          # grade de busca em memória (graus, ~450 m)


@dataclass
class Camada:
    wfs: str
    tipo: str                         # "poligono" | "ponto" | "linha"
    props: list[str] = field(default_factory=list)
    so_se: tuple[str, str] | None = None   # (campo, valor): guarda só estas feições


CAMADAS: dict[str, Camada] = {
    "zona": Camada("perimetro_zona_lei_18177_24", "poligono",
                   ["cd_zoneamento_perimetro", "tx_zoneamento_perimetro"]),
    "inundavel": Camada("area_inundavel", "poligono", ["qt_tempo_retorno"]),
    "alagamento": Camada("risco_ocorrencia_alagamento", "ponto", ["dt_ocorrencia"]),
    "inundacao": Camada("risco_ocorrencia_inundacao", "ponto", ["dt_ocorrencia"]),
    "risco_hidro": Camada("risco_hidrologico", "poligono",
                          ["tx_grau_risco_hidrologico", "nm_area_risco_hidrologico"]),
    "risco_geo": Camada("area_risco_geologico", "poligono",
                        ["tx_grau_de_risco_geologico", "tx_tipo_processo_geologico",
                         "nm_area_risco"]),
    "solo_mole": Camada("solo_mole", "poligono", []),
    "declividade": Camada("declividade", "poligono", ["nm_classe_declividade"]),
    "ipvs": Camada("indice_paulista_vulnerabilidadesocial", "poligono",
                   ["cd_indice_vulnerabilidade_social"]),
    "contaminada": Camada("area_contaminada_reabilitada_svma", "poligono",
                          ["dc_classificacao_area_contaminada", "tx_endereco_area_contaminada"]),
    "contaminada_potencial": Camada("GEOSAMPA_area_contaminada_sigac", "poligono",
                                    ["dc_tipo_situacao", "dc_atividade"]),
    "tombado": Camada("patrimonio_cultural_bem_tombado", "poligono", ["nm_area_tombada"]),
    "envoltoria": Camada("patrimonio_cultural_area_envoltoria_CONPRESP", "poligono",
                         ["nm_area"]),
    "favela": Camada("habita2geosampa_habi_favela2geosampa", "poligono", ["nome"]),
    "parque": Camada("pde_parque_municipal", "poligono",
                     ["nm_parque", "tx_tipo_situacao_projeto"],
                     so_se=("tx_tipo_situacao_projeto", "Existente")),
    "operacao_urbana": Camada("operacao_urbana", "poligono", ["nm_operacao_urbana"]),
    "metro": Camada("estacao_metro", "ponto",
                    ["nm_estacao_metro_trem", "nm_linha_metro_trem", "tx_situacao_metro_trem"],
                    so_se=("tx_situacao_metro_trem", "OPERANDO")),
    "trem": Camada("estacao_trem", "ponto",
                   ["nm_estacao_metro_trem", "nm_linha_metro_trem", "tx_situacao_metro_trem"],
                   so_se=("tx_situacao_metro_trem", "OPERANDO")),
    "corredor": Camada("corredor_onibus", "linha", ["nm_corredor"]),
    "hospital": Camada("equipamento_saude_hospital", "ponto", ["nm_equipamento"]),
}


# ---------------------------------------------------------------------------
# Armazenamento
# ---------------------------------------------------------------------------
def conectar(caminho: str | None = None) -> sqlite3.Connection:
    conn = sqlite3.connect(caminho or CAMADAS_DB, timeout=60)
    conn.execute(
        """CREATE TABLE IF NOT EXISTS feicao (
               camada TEXT, props TEXT, minx REAL, miny REAL, maxx REAL, maxy REAL,
               geom TEXT)""")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_feicao_camada ON feicao(camada)")
    conn.execute(
        """CREATE TABLE IF NOT EXISTS baixada (
               camada TEXT PRIMARY KEY, bbox TEXT, n INTEGER, em TEXT)""")
    return conn


def _arredondar(c):
    if isinstance(c, (int, float)):
        return round(c, 6)
    return [_arredondar(x) for x in c]


def _extremos(coords) -> tuple[float, float, float, float]:
    xs: list[float] = []
    ys: list[float] = []

    def rec(c):
        if c and isinstance(c[0], (int, float)):
            xs.append(c[0])
            ys.append(c[1])
        else:
            for x in c:
                rec(x)

    rec(coords)
    return min(xs), min(ys), max(xs), max(ys)


def _pedir(camada: Camada, bbox: tuple[float, float, float, float]) -> list[dict]:
    """Todas as feições do recorte, num pedido só.

    Sem paginar de propósito: sem `sortBy` o servidor devolve a 2ª página VAZIA
    em várias camadas (medido em 2026-10-07: `declividade` deu HTTP 400 e a
    `contaminada_potencial` parou em exatamente 5.000 de 7.575).
    """
    la0, lo0, la1, lo1 = bbox
    params = {
        "service": "WFS", "version": "2.0.0", "request": "GetFeature",
        "typeName": "geoportal:" + camada.wfs, "outputFormat": "application/json",
        "srsName": "EPSG:4326", "count": LIMITE,
        "bbox": f"{la0},{lo0},{la1},{lo1},urn:ogc:def:crs:EPSG::4326",
    }
    ultimo = ""
    for i in range(3):
        try:
            r = requests.get(WFS, params=params, timeout=300,
                             headers={"User-Agent": "Mozilla/5.0"})
            if r.status_code == 200 and "ExceptionReport" not in r.text[:2000]:
                d = r.json()
                feats = d.get("features") or []
                total = d.get("numberMatched")
                if isinstance(total, int) and total > len(feats):
                    raise RuntimeError(
                        f"{camada.wfs}: o servidor devolveu {len(feats)} de {total} feições")
                return feats
            ultimo = f"HTTP {r.status_code}"
        except (requests.RequestException, ValueError) as e:
            ultimo = f"{type(e).__name__}: {str(e)[:60]}"
        time.sleep(3 * (i + 1))
    raise RuntimeError(f"WFS falhou em {camada.wfs} ({ultimo})")


def baixar(conn: sqlite3.Connection, nome: str, bbox: tuple[float, float, float, float]) -> int:
    """Baixa o recorte de uma camada e substitui o que havia. Devolve o nº de feições."""
    camada = CAMADAS[nome]
    linhas = []
    for f in _pedir(camada, bbox):
        g = f.get("geometry")
        if not g or not g.get("coordinates"):
            continue
        props = f.get("properties") or {}
        if camada.so_se and str(props.get(camada.so_se[0])) != camada.so_se[1]:
            continue
        coords = _arredondar(g["coordinates"])
        x0, y0, x1, y1 = _extremos(coords)
        guardar = {k: props.get(k) for k in camada.props if props.get(k) not in (None, "")}
        linhas.append((nome, json.dumps(guardar, ensure_ascii=False), x0, y0, x1, y1,
                       json.dumps({"t": g["type"], "c": coords}, separators=(",", ":"))))
    conn.execute("DELETE FROM feicao WHERE camada = ?", (nome,))
    conn.executemany("INSERT INTO feicao VALUES (?,?,?,?,?,?,?)", linhas)
    conn.execute("INSERT OR REPLACE INTO baixada VALUES (?,?,?,datetime('now'))",
                 (nome, json.dumps(bbox), len(linhas)))
    conn.commit()
    return len(linhas)


# ---------------------------------------------------------------------------
# Geometria (metros, projeção local)
# ---------------------------------------------------------------------------
def _m(lat0: float):
    kx = 111320.0 * math.cos(math.radians(lat0))
    return kx, 110540.0


def dist_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    kx, ky = _m((lat1 + lat2) / 2)
    return math.hypot((lon2 - lon1) * kx, (lat2 - lat1) * ky)


def _dist_segmento(lat, lon, a, b) -> float:
    """Distância (m) do ponto ao segmento a-b, ambos em [lon, lat]."""
    kx, ky = _m(lat)
    px, py = lon * kx, lat * ky
    ax, ay = a[0] * kx, a[1] * ky
    bx, by = b[0] * kx, b[1] * ky
    dx, dy = bx - ax, by - ay
    t = 0.0 if dx == dy == 0 else max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / (dx * dx + dy * dy)))
    return math.hypot(px - (ax + t * dx), py - (ay + t * dy))


def _dentro_do_anel(lat: float, lon: float, anel) -> bool:
    dentro = False
    j = len(anel) - 1
    for i in range(len(anel)):
        xi, yi = anel[i][0], anel[i][1]
        xj, yj = anel[j][0], anel[j][1]
        if (yi > lat) != (yj > lat) and lon < (xj - xi) * (lat - yi) / (yj - yi) + xi:
            dentro = not dentro
        j = i
    return dentro


def _poligonos(g: dict) -> list:
    return [g["c"]] if g["t"] == "Polygon" else (g["c"] if g["t"] == "MultiPolygon" else [])


def dentro(lat: float, lon: float, g: dict) -> bool:
    for poly in _poligonos(g):
        if poly and _dentro_do_anel(lat, lon, poly[0]) and not any(
                _dentro_do_anel(lat, lon, b) for b in poly[1:]):
            return True
    return False


def dist_ao_contorno(lat: float, lon: float, g: dict) -> float:
    melhor = math.inf
    for poly in _poligonos(g):
        for anel in poly:
            for a, b in zip(anel, anel[1:], strict=False):
                melhor = min(melhor, _dist_segmento(lat, lon, a, b))
    return melhor


def _linhas(g: dict) -> list:
    return [g["c"]] if g["t"] == "LineString" else (g["c"] if g["t"] == "MultiLineString" else [])


def dist_linha(lat: float, lon: float, g: dict) -> float:
    melhor = math.inf
    for linha in _linhas(g):
        for a, b in zip(linha, linha[1:], strict=False):
            melhor = min(melhor, _dist_segmento(lat, lon, a, b))
    return melhor


# ---------------------------------------------------------------------------
# Consulta em memória
# ---------------------------------------------------------------------------
class Indice:
    """Uma camada em memória, com grade para achar candidatos rápido."""

    def __init__(self, tipo: str):
        self.tipo = tipo
        self.itens: list[tuple[dict, dict, tuple]] = []   # (props, geom, bbox)
        self.grade: dict[tuple[int, int], list[int]] = defaultdict(list)

    def adicionar(self, props: dict, geom: dict, bbox: tuple) -> None:
        i = len(self.itens)
        self.itens.append((props, geom, bbox))
        x0, y0, x1, y1 = bbox
        for cx in range(int(x0 // CELULA), int(x1 // CELULA) + 1):
            for cy in range(int(y0 // CELULA), int(y1 // CELULA) + 1):
                self.grade[(cx, cy)].append(i)

    def candidatos(self, lat: float, lon: float, raio_m: float = 0.0) -> list[int]:
        dlat = raio_m / 110540.0
        dlon = raio_m / (111320.0 * math.cos(math.radians(lat)))
        vistos: set[int] = set()
        for cx in range(int((lon - dlon) // CELULA), int((lon + dlon) // CELULA) + 1):
            for cy in range(int((lat - dlat) // CELULA), int((lat + dlat) // CELULA) + 1):
                vistos.update(self.grade.get((cx, cy), ()))
        return [i for i in vistos
                if self.itens[i][2][0] - dlon <= lon <= self.itens[i][2][2] + dlon
                and self.itens[i][2][1] - dlat <= lat <= self.itens[i][2][3] + dlat]

    def contem(self, lat: float, lon: float) -> list[dict]:
        return [self.itens[i][0] for i in self.candidatos(lat, lon)
                if dentro(lat, lon, self.itens[i][1])]

    def mais_proximo(self, lat: float, lon: float, raio_m: float) -> tuple[float, dict] | None:
        """(distância em m, props) do mais próximo dentro do raio; 0 se estiver dentro."""
        melhor: tuple[float, dict] | None = None
        for i in self.candidatos(lat, lon, raio_m):
            props, g, _ = self.itens[i]
            if self.tipo == "ponto":
                d = dist_m(lat, lon, g["c"][1], g["c"][0])
            elif self.tipo == "linha":
                d = dist_linha(lat, lon, g)
            else:
                d = 0.0 if dentro(lat, lon, g) else dist_ao_contorno(lat, lon, g)
            if d <= raio_m and (melhor is None or d < melhor[0]):
                melhor = (d, props)
        return melhor

    def quantos_ate(self, lat: float, lon: float, raio_m: float) -> int:
        return sum(1 for i in self.candidatos(lat, lon, raio_m)
                   if dist_m(lat, lon, self.itens[i][1]["c"][1], self.itens[i][1]["c"][0]) <= raio_m)


def carregar(conn: sqlite3.Connection) -> dict[str, Indice]:
    indices = {n: Indice(c.tipo) for n, c in CAMADAS.items()}
    for nome, props, x0, y0, x1, y1, geom in conn.execute(
            "SELECT camada, props, minx, miny, maxx, maxy, geom FROM feicao"):
        if nome in indices:
            indices[nome].adicionar(json.loads(props), json.loads(geom), (x0, y0, x1, y1))
    return indices


# ---------------------------------------------------------------------------
# O contexto de um ponto
# ---------------------------------------------------------------------------
def contexto(ix: dict[str, Indice], lat: float, lon: float) -> dict:
    """Tudo o que as camadas dizem sobre o ponto. Só entram as chaves com achado
    (ou com distância), para a tela não mostrar 'não' em campo que não foi medido."""
    r: dict = {}

    z = ix["zona"].contem(lat, lon)
    if z:
        r["zona"] = {"sigla": z[0].get("cd_zoneamento_perimetro"),
                     "nome": z[0].get("tx_zoneamento_perimetro")}

    r["inundavel"] = bool(ix["inundavel"].contem(lat, lon))
    # ocorrências já registradas num raio de 300 m
    r["alagamentos_300m"] = (ix["alagamento"].quantos_ate(lat, lon, 300)
                             + ix["inundacao"].quantos_ate(lat, lon, 300))
    for chave, camp_grau, camp_nome in (
            ("risco_hidro", "tx_grau_risco_hidrologico", "nm_area_risco_hidrologico"),
            ("risco_geo", "tx_grau_de_risco_geologico", "nm_area_risco")):
        achou = ix[chave].mais_proximo(lat, lon, 100)
        if achou:
            d, p = achou
            r[chave] = {"grau": str(p.get(camp_grau) or "").upper(), "nome": p.get(camp_nome),
                        "dist_m": round(d), "dentro": d == 0,
                        "tipo": p.get("tx_tipo_processo_geologico")}
    r["solo_mole"] = bool(ix["solo_mole"].contem(lat, lon))
    d = ix["declividade"].contem(lat, lon)
    if d:
        r["declividade"] = d[0].get("nm_classe_declividade")
    v = ix["ipvs"].contem(lat, lon)
    if v and v[0].get("cd_indice_vulnerabilidade_social") is not None:
        r["ipvs"] = int(v[0]["cd_indice_vulnerabilidade_social"])

    # Contaminação. O cadastro SIGAC lista como "potencial" qualquer posto ou
    # indústria: medido, 80% dos anúncios têm um a 150 m, então mostrar isso
    # sempre seria ruído. Só as classes de fato contaminadas contam até 150 m;
    # o "potencial" só se estiver a 30 m (lote vizinho), e com esse rótulo.
    graves = ix["contaminada"].mais_proximo(lat, lon, 150)
    potencial = ix["contaminada_potencial"].mais_proximo(lat, lon, 30)
    if graves:
        d, p = graves
        r["contaminada"] = {"dist_m": round(d), "classe": p.get("dc_classificacao_area_contaminada"),
                            "detalhe": p.get("tx_endereco_area_contaminada"), "potencial": False}
    elif potencial:
        d, p = potencial
        r["contaminada"] = {"dist_m": round(d), "classe": p.get("dc_tipo_situacao"),
                            "detalhe": p.get("dc_atividade"), "potencial": True}

    r["tombado"] = bool(ix["tombado"].contem(lat, lon))
    e = ix["envoltoria"].contem(lat, lon)
    if e:
        r["envoltoria"] = e[0].get("nm_area")
    o = ix["operacao_urbana"].contem(lat, lon)
    if o:
        r["operacao_urbana"] = o[0].get("nm_operacao_urbana")

    achou = ix["favela"].mais_proximo(lat, lon, 150)
    if achou:
        r["favela"] = {"dist_m": round(achou[0]), "nome": achou[1].get("nome")}

    for chave, raio, extra in (("metro", 3000, "nm_linha_metro_trem"),
                               ("trem", 3000, "nm_linha_metro_trem"),
                               ("parque", 2000, None), ("hospital", 3000, None),
                               ("corredor", 1500, None)):
        achou = ix[chave].mais_proximo(lat, lon, raio)
        if achou:
            d, p = achou
            nome = (p.get("nm_estacao_metro_trem") or p.get("nm_parque")
                    or p.get("nm_corredor") or p.get("nm_equipamento"))
            r[chave] = {"dist_m": round(d), "nome": nome,
                        **({"linha": p.get(extra)} if extra else {})}
    return r


# ---------------------------------------------------------------------------
# Cálculo para os anúncios
# ---------------------------------------------------------------------------
def garantir_tabela(conn: sqlite3.Connection) -> None:
    conn.execute(
        """CREATE TABLE IF NOT EXISTS contexto (
               anuncio_url TEXT PRIMARY KEY, lat REAL, lon REAL, precisao TEXT,
               dados TEXT, calculado_em TEXT)""")


def calcular(conn: sqlite3.Connection, camadas_conn: sqlite3.Connection,
             refazer: bool = False) -> tuple[int, int]:
    """Calcula o contexto dos anúncios ativos. Devolve (calculados, sem coordenada)."""
    garantir_tabela(conn)
    ix = carregar(camadas_conn)
    vazias = [n for n, i in ix.items() if not i.itens]
    if vazias:
        log.warning("camadas sem feição (rode --baixar): %s", ", ".join(vazias))

    ja = set() if refazer else {r[0] for r in conn.execute("SELECT anuncio_url FROM contexto")}
    baixadas = camadas_conn.execute("SELECT bbox FROM baixada").fetchall()
    if not baixadas:
        raise RuntimeError("nenhuma camada baixada: rode `caca-camadas --baixar`")
    # o recorte comum a todas as camadas baixadas
    caixas = [json.loads(b[0]) for b in baixadas]
    rec = (max(c[0] for c in caixas), max(c[1] for c in caixas),
           min(c[2] for c in caixas), min(c[3] for c in caixas))

    cache_pontos: dict[tuple, dict] = {}
    feitos = sem = 0
    for url, lat, lon, precisao in pontos_dos_anuncios(conn):
        if url in ja:
            continue
        # fora do que foi baixado, "nenhum risco" seria mentira: não calcula
        if not (rec[0] <= lat <= rec[2] and rec[1] <= lon <= rec[3]):
            sem += 1
            continue
        ponto = (round(lat, 5), round(lon, 5))
        if ponto not in cache_pontos:
            cache_pontos[ponto] = contexto(ix, lat, lon)
        conn.execute(
            "INSERT OR REPLACE INTO contexto VALUES (?,?,?,?,?,datetime('now'))",
            (url, lat, lon, precisao, json.dumps(cache_pontos[ponto], ensure_ascii=False)))
        feitos += 1
        if feitos % 500 == 0:
            conn.commit()
            print(f"  {feitos} anúncios...", flush=True)
    conn.commit()
    return feitos, sem


def pontos_dos_anuncios(conn: sqlite3.Connection) -> list[tuple]:
    """(url, lat, lon, precisao) dos anúncios ativos que têm coordenada."""
    from cacaimoveis import geocode

    saida = []
    for url, rua, bairro, cep, chave in conn.execute(
            """SELECT url, rua, bairro, cep, rua_chave FROM anuncios
               WHERE removido_em IS NULL AND COALESCE(rua, '') <> ''""").fetchall():
        loc = geocode.localizar(conn, rua, bairro or "", cep or "", chave or "",
                                consultar_rede=False)
        if loc:
            saida.append((url, loc["lat"], loc["lon"], loc["precisao"]))
    return saida


def bbox_dos_anuncios(conn: sqlite3.Connection) -> tuple[float, float, float, float] | None:
    """Recorte (lat0, lon0, lat1, lon1) que cobre 98% dos anúncios, mais a margem.

    Percentis em vez de mín/máx: ruas homônimas distantes jogavam alguns pontos
    do outro lado da cidade e o recorte virava a cidade inteira (54 mil
    polígonos de zoneamento em vez de ~20 mil).
    """
    pts = pontos_dos_anuncios(conn)
    if not pts:
        return None
    lats = sorted(p[1] for p in pts)
    lons = sorted(p[2] for p in pts)
    n = len(pts)
    lo, hi = int(0.01 * (n - 1)), int(0.99 * (n - 1))
    return lats[lo] - MARGEM, lons[lo] - MARGEM, lats[hi] + MARGEM, lons[hi] + MARGEM


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Entorno dos imóveis (camadas do GeoSampa)")
    ap.add_argument("--baixar", action="store_true", help="baixa o recorte das camadas")
    ap.add_argument("--refazer", action="store_true",
                    help="com --baixar: baixa de novo; com --calcular: recalcula todos")
    ap.add_argument("--calcular", action="store_true", help="cruza os anúncios com as camadas")
    ap.add_argument("--so", nargs="+", metavar="CAMADA", help="só estas camadas (com --baixar)")
    args = ap.parse_args(argv)
    if not (args.baixar or args.calcular):
        ap.print_help()
        return 2

    conn = sqlite3.connect(config.DB_PATH, timeout=60)
    cam = conectar()
    if args.baixar:
        bbox = bbox_dos_anuncios(conn)
        if not bbox:
            print("Sem lotes geocodificados: rode `caca-geocodificar` antes.")
            return 1
        print(f"Recorte: lat {bbox[0]:.3f}..{bbox[2]:.3f}  lon {bbox[1]:.3f}..{bbox[3]:.3f}")
        ja = {r[0] for r in cam.execute("SELECT camada FROM baixada")}
        falhas = 0
        for nome in (args.so or CAMADAS):
            if nome in ja and not args.refazer:
                print(f"  {nome:22s} já baixada")
                continue
            t = time.time()
            try:
                n = baixar(cam, nome, bbox)
                print(f"  {nome:22s} {n:>6} feições  ({time.time() - t:.0f}s)", flush=True)
            except RuntimeError as e:
                falhas += 1
                print(f"  {nome:22s} FALHOU: {e}", flush=True)
        if falhas:
            return 1
    if args.calcular:
        try:
            feitos, sem = calcular(conn, cam, refazer=args.refazer)
        except RuntimeError as e:
            print(e)
            return 1
        print(f"Contexto calculado para {feitos} anúncio(s); {sem} sem coordenada.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
