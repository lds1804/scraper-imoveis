"""Coordenadas dos imóveis a partir do cadastro de lotes da prefeitura (GeoSampa).

Por que existe: o banco não guarda latitude/longitude, e o Street View, o mapa
e o cruzamento com as camadas do GeoSampa (risco, metrô, zoneamento) precisam
delas.

Por que NÃO o Nominatim (testado em 2026-10-07, 10 endereços com número): só 3
foram achados, todos só na rua, e a busca por nome devolveu a rua HOMÔNIMA de
outro bairro em 3 casos. O cadastro é melhor porque cada lote da cidade está
lá, com número da porta e polígono:

  - o anúncio traz o número  -> o lote exato                  ("lote")
  - o número não existe      -> interpolação entre os números
                                vizinhos da mesma rua          ("proximo")
  - o anúncio só traz a rua  -> o meio da rua                  ("rua")

A rua é baixada do WFS (camada `lote_cidadao`, com geometria) UMA vez e fica
na tabela `lote_geo`; `lote_geo_ruas` lembra as ruas já tentadas, inclusive as
que não existem no cadastro, para não perguntar de novo.

Uso:
    caca-geocodificar                 # baixa as ruas dos anúncios que faltam
    caca-geocodificar --limite 50     # no máximo 50 ruas
"""

from __future__ import annotations

import argparse
import re
import sqlite3
import sys
import time
from concurrent.futures import ThreadPoolExecutor

import requests

from cacaimoveis import config, endereco, logs

log = logs.obter(__name__)

WFS = "http://wfs.geosampa.prefeitura.sp.gov.br/geoserver/wfs"
CAMADA = "geoportal:lote_cidadao"
CAMPOS = ("cd_setor_fiscal,cd_quadra_fiscal,cd_lote,cd_condominio,"
          "cd_numero_porta,nm_logradouro_completo,ge_poligono")
POR_PAGINA = 5000
TENTATIVAS = 3
THREADS = 3
RUAS_POR_PEDIDO = 25
# ruas homônimas em lados opostos da cidade: se os lotes candidatos se
# espalham por mais que isto (graus; ~3 km) e nada desempata, não chuta
ESPALHO_MAX = 0.03


def garantir_tabelas(conn: sqlite3.Connection) -> None:
    conn.execute(
        """CREATE TABLE IF NOT EXISTS lote_geo (
               setor TEXT, quadra TEXT, lote TEXT, cond TEXT,
               chave TEXT, numero TEXT, lat REAL, lon REAL,
               PRIMARY KEY (setor, quadra, lote, cond))""")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_lote_geo_chave ON lote_geo(chave)")
    conn.execute(
        """CREATE TABLE IF NOT EXISTS lote_geo_ruas (
               chave TEXT PRIMARY KEY, n_lotes INTEGER, baixado_em TEXT)""")


# ---------------------------------------------------------------------------
# Geometria
# ---------------------------------------------------------------------------
def _anel(geom: dict) -> list[list[float]]:
    """O anel externo do (primeiro) polígono, em [lon, lat]."""
    if not geom:
        return []
    c = geom.get("coordinates") or []
    if geom.get("type") == "MultiPolygon" and c:
        c = c[0]
    return c[0] if c else []


def centroide(geom: dict) -> tuple[float, float] | None:
    """(lat, lon) do centro do polígono (média ponderada pela área; se o anel
    for degenerado, a média dos vértices)."""
    anel = _anel(geom)
    if len(anel) < 3:
        return None
    a = cx = cy = 0.0
    for (x0, y0), (x1, y1) in zip(anel, anel[1:] + anel[:1], strict=True):
        f = x0 * y1 - x1 * y0
        a += f
        cx += (x0 + x1) * f
        cy += (y0 + y1) * f
    if abs(a) < 1e-18:
        return (sum(p[1] for p in anel) / len(anel), sum(p[0] for p in anel) / len(anel))
    a *= 3.0
    return cy / a, cx / a


def _num(v) -> int | None:
    m = re.search(r"\d+", str(v or ""))
    return int(m.group()) if m else None


# ---------------------------------------------------------------------------
# Download das ruas
# ---------------------------------------------------------------------------
def nomes_da_rua(conn: sqlite3.Connection, chave: str) -> list[str]:
    """Como o cadastro escreve a rua ('R TEIXEIRA SOARES', 'AV ...')."""
    return [r[0] for r in conn.execute(
        "SELECT DISTINCT logradouro FROM lotes WHERE chave = ?", (chave,))]


def _pedir(nomes: list[str], inicio: int) -> list[dict]:
    lista = ",".join("'" + n.replace("'", "''") + "'" for n in nomes)
    params = {
        "service": "WFS", "version": "2.0.0", "request": "GetFeature",
        "typeName": CAMADA, "outputFormat": "application/json",
        "srsName": "EPSG:4326", "propertyName": CAMPOS,
        "CQL_FILTER": f"nm_logradouro_completo IN ({lista})",
        "sortBy": "cd_identificador", "count": POR_PAGINA, "startIndex": inicio,
    }
    ultimo = ""
    for i in range(TENTATIVAS):
        try:
            r = requests.get(WFS, params=params, timeout=120,
                             headers={"User-Agent": "Mozilla/5.0"})
            if r.status_code == 200 and "ExceptionReport" not in r.text[:2000]:
                return r.json().get("features") or []
            ultimo = f"HTTP {r.status_code}"
        except (requests.RequestException, ValueError) as e:
            ultimo = f"{type(e).__name__}: {str(e)[:60]}"
        time.sleep(2 * (i + 1))
    raise RuntimeError(f"WFS falhou ({ultimo})")


def baixar_ruas(conn: sqlite3.Connection, chaves: list[str]) -> dict[str, int]:
    """Baixa os lotes de VÁRIAS ruas num pedido só. Devolve {chave: nº de lotes}.

    Em lote porque o filtro por nome não usa índice no servidor (varre a camada
    inteira, ~2 s): 25 ruas por pedido custam o mesmo que uma, e a ida rua a rua
    levaria mais de uma hora para as 564 ruas dos anúncios.

    Levanta RuntimeError se o WFS falhar (nenhuma rua do pedido é marcada).
    """
    garantir_tabelas(conn)
    por_nome: dict[str, str] = {}
    for chave in chaves:
        for nome in nomes_da_rua(conn, chave):
            por_nome[nome] = chave
    contagem = dict.fromkeys(chaves, 0)
    linhas = []
    if por_nome:
        inicio = 0
        while True:
            feats = _pedir(list(por_nome), inicio)
            for f in feats:
                p = f.get("properties") or {}
                chave = por_nome.get(str(p.get("nm_logradouro_completo") or "").strip())
                ponto = centroide(f.get("geometry"))
                if not chave or not ponto:
                    continue
                contagem[chave] += 1
                linhas.append((
                    str(p.get("cd_setor_fiscal") or "").strip(),
                    str(p.get("cd_quadra_fiscal") or "").strip(),
                    str(p.get("cd_lote") or "").strip(),
                    str(p.get("cd_condominio") or "").strip(), chave,
                    re.sub(r"\D", "", str(p.get("cd_numero_porta") or "")),
                    ponto[0], ponto[1]))
            if len(feats) < POR_PAGINA:
                break
            inicio += POR_PAGINA
    conn.executemany("INSERT OR REPLACE INTO lote_geo VALUES (?,?,?,?,?,?,?,?)", linhas)
    conn.executemany(
        "INSERT OR REPLACE INTO lote_geo_ruas VALUES (?, ?, datetime('now'))",
        list(contagem.items()))
    conn.commit()
    return contagem


def baixar_rua(conn: sqlite3.Connection, chave: str) -> int:
    """Uma rua só (uso interativo: a rota /geo). Ver `baixar_ruas`."""
    return baixar_ruas(conn, [chave])[chave]


# ---------------------------------------------------------------------------
# Localização
# ---------------------------------------------------------------------------
def separar_numero(rua: str) -> tuple[str, str]:
    """'Rua X, 335' -> ('Rua X', '335'). Sem número -> ('Rua X', '')."""
    rua = re.sub(r"[\s,]+$", "", (rua or "").strip())
    m = re.match(r"^(.*?)[,\s]+(\d+[A-Za-z]?)$", rua)
    return (m.group(1).strip(" ,"), m.group(2)) if m else (rua, "")


def _ceps_por_numero(conn: sqlite3.Connection, chave: str) -> dict[int, str]:
    """número da porta -> CEP, pelo cadastro do IPTU (indexado por rua)."""
    try:
        return {n: c for n, c in (
            (_num(num), cep) for num, cep in conn.execute(
                "SELECT numero, cep FROM iptu WHERE rua_chave = ?", (chave,)))
            if n is not None and c}
    except sqlite3.OperationalError:
        return {}


def _filtrar_por_cep(lotes: list[tuple], ceps: dict[int, str], cep: str) -> list[tuple]:
    """Fica com os lotes do mesmo CEP do anúncio (8 dígitos, depois 5)."""
    cep = re.sub(r"\D", "", cep or "")
    if len(cep) < 5 or not ceps:
        return lotes
    for tam in (8, 5):
        sel = [x for x in lotes if (ceps.get(x[0]) or "")[:tam] == cep[:tam]
               and ceps.get(x[0])]
        if sel:
            return sel
    return lotes


def localizar(conn: sqlite3.Connection, rua: str, bairro: str = "", cep: str = "",
              rua_chave: str = "", consultar_rede: bool = True) -> dict | None:
    """Coordenadas do endereço. {"lat","lon","precisao"} ou None.

    `precisao`: "lote" (número exato), "proximo" (interpolado entre vizinhos) ou
    "rua" (o anúncio não traz o número: meio da rua).
    """
    nome, numero = separar_numero(rua)
    if not nome:
        return None  # só o bairro: o centro dele não diz nada sobre o imóvel
    chave = rua_chave or endereco.chave_tolerante(nome)
    if not chave:
        return None
    garantir_tabelas(conn)
    if not conn.execute("SELECT 1 FROM lote_geo_ruas WHERE chave = ?", (chave,)).fetchone():
        if not consultar_rede:
            return None
        try:
            baixar_rua(conn, chave)
        except RuntimeError as e:
            log.warning("geocode: rua '%s' não baixada: %s", chave, e)
            return None

    lotes = [(_num(n), la, lo) for n, la, lo in conn.execute(
        "SELECT numero, lat, lon FROM lote_geo WHERE chave = ?", (chave,))]
    if not lotes:
        return None
    lotes = _filtrar_por_cep(lotes, _ceps_por_numero(conn, chave), cep)

    alvo = _num(numero)
    if alvo is not None:
        iguais = [x for x in lotes if x[0] == alvo]
        if iguais:
            return {"lat": sum(x[1] for x in iguais) / len(iguais),
                    "lon": sum(x[2] for x in iguais) / len(iguais), "precisao": "lote"}
        # mesmo lado da rua (paridade), os vizinhos mais próximos de cada lado
        lado = [x for x in lotes if x[0] is not None and x[0] % 2 == alvo % 2]
        abaixo = max((x for x in lado if x[0] < alvo), key=lambda x: x[0], default=None)
        acima = min((x for x in lado if x[0] > alvo), key=lambda x: x[0], default=None)
        if abaixo and acima:
            t = (alvo - abaixo[0]) / (acima[0] - abaixo[0])
            return {"lat": abaixo[1] + t * (acima[1] - abaixo[1]),
                    "lon": abaixo[2] + t * (acima[2] - abaixo[2]), "precisao": "proximo"}
        unico = abaixo or acima
        if unico:
            return {"lat": unico[1], "lon": unico[2], "precisao": "proximo"}

    lats = sorted(x[1] for x in lotes)
    lons = sorted(x[2] for x in lotes)
    if lats[-1] - lats[0] > ESPALHO_MAX or lons[-1] - lons[0] > ESPALHO_MAX:
        return None  # ruas homônimas distantes e nada para desempatar
    meio = len(lotes) // 2
    return {"lat": lats[meio], "lon": lons[meio], "precisao": "rua"}


def obter(conn: sqlite3.Connection, rua: str, bairro: str = "", cep: str = "",
          rua_chave: str = "", consultar_rede: bool = True) -> dict | None:
    """Nome antigo de `localizar` (a rota /geo usa este)."""
    return localizar(conn, rua, bairro, cep, rua_chave, consultar_rede)


# ---------------------------------------------------------------------------
# Linha de comando
# ---------------------------------------------------------------------------
def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Baixa a geometria dos lotes das ruas dos anúncios")
    ap.add_argument("--limite", type=int, default=0, metavar="N",
                    help="no máximo N ruas (0 = todas)")
    args = ap.parse_args(argv)

    conn = sqlite3.connect(config.DB_PATH, timeout=60)
    garantir_tabelas(conn)
    chaves = [r[0] for r in conn.execute(
        """SELECT DISTINCT rua_chave FROM anuncios
           WHERE removido_em IS NULL AND COALESCE(rua_chave, '') <> ''
             AND rua_chave NOT IN (SELECT chave FROM lote_geo_ruas)""")]
    if args.limite:
        chaves = chaves[:args.limite]
    print(f"{len(chaves)} rua(s) a baixar", flush=True)

    def uma(grupo: list[str]) -> tuple[list[str], dict[str, int] | None]:
        c = sqlite3.connect(config.DB_PATH, timeout=120)
        try:
            return grupo, baixar_ruas(c, grupo)
        except RuntimeError as e:
            log.warning("geocode: %s", e)
            return grupo, None
        finally:
            c.close()

    grupos = [chaves[i:i + RUAS_POR_PEDIDO] for i in range(0, len(chaves), RUAS_POR_PEDIDO)]
    feitas = lotes = falhas = 0
    try:
        with ThreadPoolExecutor(max_workers=THREADS) as ex:
            for grupo, cont in ex.map(uma, grupos):
                feitas += len(grupo)
                if cont is None:
                    falhas += len(grupo)
                else:
                    lotes += sum(cont.values())
                print(f"  {feitas}/{len(chaves)} ruas · {lotes} lotes · {falhas} falha(s)",
                      flush=True)
    except KeyboardInterrupt:
        print("\n[interrompido; as ruas já baixadas estão salvas]")
    print(f"Pronto: {lotes} lotes em {feitas - falhas} rua(s).")
    return 1 if falhas else 0


if __name__ == "__main__":
    sys.exit(main())
