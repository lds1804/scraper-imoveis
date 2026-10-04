"""Cadastro imobiliário fiscal de São Paulo (GeoSampa) — área oficial do lote.

POR QUE EXISTE
--------------
O anúncio informa a área construída e o terreno, mas ninguém confere se é
verdade. Com o cadastro da prefeitura dá para comparar o que o anúncio DIZ
com o que a prefeitura REGISTRA.

O QUE O GEOSAMPA *NÃO* TEM (verificado, não suposto)
----------------------------------------------------
**Valor venal por imóvel não é público.** Foi procurado em dois lugares:

  1. Serviço WFS do GeoSampa — 483 camadas listadas, nenhuma com valor
     venal. A camada `lote_cidadao` traz código fiscal e áreas, não valor.
  2. Portal de Dados Abertos (CKAN) — buscas por 'venal', 'valor venal',
     'planta generica de valores', 'PGV', 'IPTU': zero resultados. A Planta
     Genérica de Valores, que é a base do IPTU, não é publicada.

Então o "valor venal da quadra" que se poderia tentar estimar seria um
número inventado. O que existe de concreto é a ÁREA OFICIAL — e ela é mais
útil para o objetivo real: pegar anúncio com metragem inflada.

A CASCATA (do mais exato para o mais amplo)
-------------------------------------------
    1. número do imóvel  -> o lote exato
    2. só a rua          -> mediana dos lotes da rua
    3. só o bairro       -> mediana dos lotes do bairro

Medido na base: só ~18% dos anúncios trazem o número do imóvel, então a
cascata não é luxo — sem os níveis 2 e 3 a maior parte ficaria sem nada.

COMO LER O RESULTADO (importante)
---------------------------------
Diferença de área NÃO é prova de má-fé. No Brasil é comum construir ou
reformar e não atualizar o cadastro — a prefeitura só toma conhecimento na
vistoria ou na venda. Uma divergência é SINAL PARA INVESTIGAR, e é assim
que o app apresenta.

FONTE
-----
WFS aberto, sem chave e sem captcha:
    http://wfs.geosampa.prefeitura.sp.gov.br/geoserver/wfs
`nm_logradouro_completo` usa o mesmo formato abreviado do ITBI
('R WALTRUDES CORREA'), então `endereco.chave_rua()` normaliza os dois lados.

Uso:
    python geosampa.py --por-bairro            # lotes de cada bairro-alvo
    python geosampa.py --rua "Rua Teeré"       # lotes de uma rua
    python geosampa.py --ingerir               # baixa e grava os lotes
    python geosampa.py --resumo                # o que já está gravado
"""

from __future__ import annotations

import argparse
import os
import re
import sqlite3
import sys
import time

import config
import endereco

# o console do PowerShell 5.1 é cp1252 e derruba o script em Unicode
try:
    sys.stdout.reconfigure(errors="replace")
    sys.stderr.reconfigure(errors="replace")
except Exception:  # noqa: BLE001
    pass

WFS = "http://wfs.geosampa.prefeitura.sp.gov.br/geoserver/wfs"
CAMADA = "geoportal:lote_cidadao"

# campos que interessam (pedir só esses deixa a resposta pequena e rápida)
# CUIDADO: o nome do campo de uso começa com `dc_` e o de tipo de terreno
# com `cd_` — é assim no cadastro, não é erro de digitação. Um nome errado
# aqui faz o WFS devolver um relatório de erro em vez de dados, e o parse
# sai silenciosamente vazio (foi o que aconteceu na primeira versão).
CAMPOS = [
    "cd_setor_fiscal", "cd_quadra_fiscal", "cd_lote", "cd_condominio",
    "cd_numero_porta", "tx_complemento_endereco", "nm_logradouro_completo",
    "dc_tipo_uso_imovel", "cd_tipo_terreno_imovel", "tx_situ_lote",
    "qt_area_terreno", "qt_area_construida",
]

# lotes por consulta: uma rua inteira costuma ter menos, mas bairro tem muito
MAX_POR_CONSULTA = 5000
# pausa entre chamadas (o serviço é público e não tem rate limit declarado)
DELAY_S = 0.2
# tentativas por consulta
TENTATIVAS = 3

# Paralelismo. Medido: cada consulta leva ~9s, e o tempo NÃO cresce com o
# tamanho da rua — é overhead fixo do servidor (montar o GML, provavelmente
# ordenar por logradouro sem índice). Sequencial, as 1.094 ruas levariam
# ~2h45. Com 8 threads cai para ~20 min.
#
# 8 é deliberado: é um serviço público e não há rate limit declarado. Passar
# disso acelera pouco (o gargalo é o servidor) e arrisca ser bloqueado.
PARALELO = 8

# áreas fora desta faixa são erro de cadastro, não imóvel
AREA_MINIMA = 10.0
AREA_MAXIMA = 50_000.0

# R$/m² que separa "casa" de "prédio inteiro" — usado só para comparar
# imóvel com imóvel no resumo por rua/bairro
AREA_MAXIMA_CASA = 1000.0


# ---------------------------------------------------------------------------
# Acesso ao WFS
# ---------------------------------------------------------------------------
def _get(params: dict, tentativas: int = TENTATIVAS) -> str:
    """GET no WFS com retentativa. Import local para o módulo não exigir
    `requests` em quem só vai ler o banco."""
    import requests

    ultimo = ""
    for i in range(tentativas):
        try:
            r = requests.get(WFS, params=params, timeout=180,
                             headers={"User-Agent": "Mozilla/5.0"})
            if r.status_code == 200:
                return r.text
            ultimo = f"HTTP {r.status_code}"
        except Exception as e:  # noqa: BLE001
            ultimo = f"{type(e).__name__}: {str(e)[:60]}"
        time.sleep(1.5 * (i + 1))
    raise RuntimeError(f"WFS falhou ({ultimo})")


def _lotes_do_xml(xml: str) -> list[dict]:
    """Extrai os lotes do GML devolvido pelo WFS.

    Se o WFS não gostou de algum parâmetro, ele devolve um `ExceptionReport`
    do OGC em vez de features — e o parse sairia VAZIO sem ninguém perceber
    (foi o que aconteceu quando um nome de campo estava errado). Por isso o
    relatório de erro é detectado e levantado aqui.
    """
    if "ExceptionReport" in xml or "<ows:Exception" in xml:
        motivo = re.search(r"<ows:ExceptionText>([^<]+)</ows:ExceptionText>", xml)
        raise RuntimeError(f"WFS recusou a consulta: "
                           f"{motivo.group(1)[:120] if motivo else 'motivo nao informado'}")

    lotes = []
    for bloco in re.findall(r"<gml:featureMembers>(.*?)</gml:featureMembers>",
                            xml, re.S):
        for feat in re.findall(
            r"<geoportal:lote_cidadao [^>]*>(.*?)</geoportal:lote_cidadao>",
            bloco, re.S,
        ):
            campos = dict(re.findall(
                r"<geoportal:(\w+)>([^<]*)</geoportal:\1>", feat))
            if campos:
                lotes.append(campos)
    return lotes


def _num(valor) -> float | None:
    try:
        v = float(valor)
    except (TypeError, ValueError):
        return None
    return v if v > 0 else None


def _limpar_lote(c: dict) -> dict | None:
    """Converte um lote cru do GML em registro limpo. None se for inútil."""
    logradouro = (c.get("nm_logradouro_completo") or "").strip()
    if not logradouro:
        return None

    numero = re.sub(r"\D", "", str(c.get("cd_numero_porta") or ""))
    return {
        "setor": (c.get("cd_setor_fiscal") or "").strip(),
        "quadra": (c.get("cd_quadra_fiscal") or "").strip(),
        "lote": (c.get("cd_lote") or "").strip(),
        "logradouro": logradouro,
        "chave": endereco.chave_rua(logradouro),
        "numero": numero,
        "complemento": (c.get("tx_complemento_endereco") or "").strip(),
        "uso": (c.get("dc_tipo_uso_imovel") or "").strip(),
        "tipo_terreno": (c.get("dc_tipo_terreno_imovel") or "").strip(),
        "situacao": (c.get("tx_situ_lote") or "").strip(),
        "area_terreno": _num(c.get("qt_area_terreno")),
        "area_construida": _num(c.get("qt_area_construida")),
    }


def buscar_por_rua(logradouro: str, limite: int = MAX_POR_CONSULTA) -> list[dict]:
    """Todos os lotes de um logradouro.

    Como funciona a busca, e por que não é uma consulta direta:

    1. o cadastro abrevia DIFERENTE de nós ('AV GAL CHARLES DE GAULLE' x
       'Avenida General Charles de Gaulle'), então igualdade não casa;
    2. o LIKE no WFS não aceita a expressão normalizada inteira;
    3. então buscamos por UMA palavra distintiva (`termo_de_busca`) e
       filtramos o resultado AQUI, comparando `chave_tolerante`.

    O filtro local é o que garante que não entra lote de outra rua que
    por acaso compartilhe a palavra buscada.
    """
    termo = endereco.termo_de_busca(logradouro)
    if not termo:
        return []
    alvo = endereco.chave_tolerante(logradouro)
    seguro = termo.replace("'", "''")

    xml = _get({
        "service": "WFS", "version": "1.1.0", "request": "GetFeature",
        "typeName": CAMADA,
        "CQL_FILTER": f"nm_logradouro_completo LIKE '%{seguro}%'",
        "maxFeatures": str(limite),
        "propertyName": ",".join(CAMPOS),
    })

    lotes = []
    for c in _lotes_do_xml(xml):
        lote = _limpar_lote(c)
        if not lote:
            continue
        # confirma que é a MESMA via, não só uma que compartilha a palavra
        if endereco.chave_tolerante(c.get("nm_logradouro_completo", "")) != alvo:
            continue
        lote["chave"] = alvo
        lotes.append(lote)
    return lotes


def buscar_por_bairro(nome_bairro: str, max_logradouros: int = 60) -> list[dict]:
    """Lotes de um bairro-alvo.

    O WFS não tem coluna de bairro, então o caminho é: descobrir quais
    logradouros aparecem nos anúncios daquele bairro e buscar rua por rua.
    É mais lento, mas evita baixar a cidade inteira (que são milhões de lotes).
    """
    conn = sqlite3.connect(config.DB_PATH)
    conn.row_factory = sqlite3.Row
    ruas = [r[0] for r in conn.execute(
        """SELECT DISTINCT rua FROM anuncios
           WHERE LOWER(bairro) = LOWER(?) AND COALESCE(rua,'') <> ''
           LIMIT ?""",
        (nome_bairro, max_logradouros),
    )]
    conn.close()

    todos: list[dict] = []
    vistos: set[tuple] = set()
    for rua in ruas:
        for lote in buscar_por_rua(rua):
            chave = (lote["setor"], lote["quadra"], lote["lote"],
                     lote["complemento"])
            if chave in vistos:
                continue
            vistos.add(chave)
            lote["bairro"] = nome_bairro
            todos.append(lote)
        time.sleep(DELAY_S)
    return todos


# ---------------------------------------------------------------------------
# Banco
# ---------------------------------------------------------------------------
def migrar_chaves(conn: sqlite3.Connection, verbose: bool = True) -> int:
    """Recalcula a `chave` das linhas já gravadas com a normalização nova.

    Necessário porque as primeiras ingestões usavam `chave_rua`, que não
    expande as abreviações do cadastro. A migração é LOCAL (lê o
    `logradouro` já gravado), então não refaz nenhum download.
    """
    linhas = conn.execute("SELECT rowid, logradouro, chave FROM lotes").fetchall()
    mudar = [(endereco.chave_tolerante(r[1]), r[0]) for r in linhas
             if endereco.chave_tolerante(r[1]) != r[2]]
    if mudar:
        conn.executemany("UPDATE lotes SET chave = ? WHERE rowid = ?", mudar)
        conn.commit()
    if verbose:
        print(f"chaves migradas: {len(mudar):,d} de {len(linhas):,d} lotes")
    return len(mudar)


def criar_tabela(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS lotes (
            setor           TEXT,
            quadra          TEXT,
            lote            TEXT,
            complemento     TEXT,
            logradouro      TEXT,
            chave           TEXT,     -- chave_tolerante(logradouro)
            numero          TEXT,
            bairro          TEXT,     -- do anúncio que levou até este lote
            uso             TEXT,
            tipo_terreno    TEXT,
            situacao        TEXT,
            area_terreno    REAL,
            area_construida REAL,
            atualizado_em   TEXT,
            PRIMARY KEY (setor, quadra, lote, complemento)
        );
        CREATE INDEX IF NOT EXISTS idx_lotes_chave    ON lotes(chave);
        CREATE INDEX IF NOT EXISTS idx_lotes_chave_num ON lotes(chave, numero);
        CREATE INDEX IF NOT EXISTS idx_lotes_bairro   ON lotes(bairro);
        """
    )
    conn.commit()
    # bancos criados antes desta versão podem não ter a coluna `bairro`
    cols = {r[1] for r in conn.execute("PRAGMA table_info(lotes)")}
    if "bairro" not in cols:
        conn.execute("ALTER TABLE lotes ADD COLUMN bairro TEXT")
        conn.commit()


def gravar(conn: sqlite3.Connection, lotes: list[dict]) -> int:
    if not lotes:
        return 0
    criar_tabela(conn)
    conn.executemany(
        """INSERT OR REPLACE INTO lotes
           (setor, quadra, lote, complemento, logradouro, chave, numero,
            bairro, uso, tipo_terreno, situacao, area_terreno,
            area_construida, atualizado_em)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,datetime('now'))""",
        [
            (l["setor"], l["quadra"], l["lote"], l["complemento"],
             l["logradouro"], l["chave"], l["numero"], l.get("bairro"),
             l["uso"], l["tipo_terreno"], l["situacao"],
             l["area_terreno"], l["area_construida"])
            for l in lotes
        ],
    )
    conn.commit()
    return len(lotes)


# ---------------------------------------------------------------------------
# Consulta (usada pelo comparativo)
# ---------------------------------------------------------------------------
def area_oficial(conn: sqlite3.Connection, rua: str, numero: str = "",
                 bairro: str = "") -> dict | None:
    """Área oficial de referência, em cascata: lote exato -> rua -> bairro.

    Recebe o NOME da rua (não a chave): a conversão para `chave_tolerante`
    acontece aqui, para que os dois lados usem a mesma função e não haja
    chance de uma ponta passar por uma normalização e a outra não.

    Devolve None se não houver lote algum. `area_construida`/`area_terreno`
    são MEDIANAS quando o nível é rua ou bairro (não dá para saber qual lote
    é o imóvel), e o valor do lote quando o número casa.
    """
    chave = endereco.chave_tolerante(rua) if rua else ""
    if not chave and not bairro:
        return None

    # ---- nível 1: lote exato pelo número ----
    if chave and numero:
        linha = conn.execute(
            """SELECT area_construida, area_terreno, logradouro, numero, uso
               FROM lotes WHERE chave = ? AND numero = ?
                 AND COALESCE(area_construida,0) > 0
               LIMIT 1""",
            (chave, numero),
        ).fetchone()
        if linha:
            return {
                "nivel": "lote",
                "n_lotes": 1,
                "area_construida": linha[0],
                "area_terreno": linha[1],
                "logradouro": linha[2],
                "numero": linha[3],
                "uso": linha[4],
            }

    # ---- nível 2: mediana da rua ----
    if chave:
        r = _mediana_dos_lotes(conn, "chave = ?", (chave,))
        if r:
            r["nivel"] = "rua"
            return r

    # ---- nível 3: mediana do bairro ----
    if bairro:
        r = _mediana_dos_lotes(conn, "LOWER(bairro) = LOWER(?)", (bairro,))
        if r:
            r["nivel"] = "bairro"
            return r
    return None


def _mediana_dos_lotes(conn: sqlite3.Connection, onde: str,
                       params: tuple) -> dict | None:
    """Mediana das áreas dos lotes que casam com o filtro.

    Descarta áreas fora da faixa plausível e os lotes que parecem prédio
    inteiro (`area_construida` acima de AREA_MAXIMA_CASA), porque a mediana da
    rua deve representar CASAS — misturar um edifício de 4.000 m² distorce.
    """
    linhas = conn.execute(
        f"""SELECT area_construida, area_terreno FROM lotes
            WHERE {onde}
              AND COALESCE(area_construida,0) BETWEEN ? AND ?
              AND COALESCE(area_construida,0) <= ?""",
        (*params, AREA_MINIMA, AREA_MAXIMA, AREA_MAXIMA_CASA),
    ).fetchall()
    if not linhas:
        return None

    construidas = sorted(r[0] for r in linhas if r[0])
    terrenos = sorted(r[1] for r in linhas if r[1])
    if not construidas:
        return None
    return {
        "n_lotes": len(linhas),
        "area_construida": _mediana(construidas),
        "area_terreno": _mediana(terrenos) if terrenos else None,
        "logradouro": None,
        "numero": None,
        "uso": None,
    }


def _mediana(valores: list[float]) -> float | None:
    n = len(valores)
    if not n:
        return None
    meio = n // 2
    return valores[meio] if n % 2 else (valores[meio - 1] + valores[meio]) / 2


# ---------------------------------------------------------------------------
# Comparação área do anúncio x área oficial
# ---------------------------------------------------------------------------
def criar_tabela_comparacao(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS areas_oficiais (
            anuncio_url      TEXT PRIMARY KEY,
            nivel            TEXT,   -- 'lote' | 'rua' | 'bairro'
            n_lotes          INTEGER,
            area_anuncio     REAL,
            area_oficial     REAL,
            area_terreno_anuncio REAL,
            area_terreno_oficial REAL,
            dif_pct          REAL,   -- (anuncio/oficial - 1) * 100
            logradouro       TEXT,
            numero           TEXT,
            uso              TEXT,
            calculado_em     TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_ao_nivel ON areas_oficiais(nivel);
        CREATE INDEX IF NOT EXISTS idx_ao_dif   ON areas_oficiais(dif_pct);
        """
    )
    conn.commit()


def _grau_diferenca(dif: float | None) -> str:
    """Classifica a diferença de área em faixas legíveis.

    A faixa morta de +-15% existe porque o anúncio arredonda e às vezes conta
    a área de forma diferente da prefeitura (área útil x área construída,
    varanda, edícula). Nessa faixa não há o que concluir.
    """
    if dif is None:
        return "sem dado"
    a = abs(dif)
    if a <= 15:
        return "compativel"
    if a <= 30:
        return "atencao"
    return "divergente"


def comparar_areas(conn: sqlite3.Connection, refazer: bool = False,
                   limite: int | None = None, verbose: bool = True) -> dict:
    """Compara a área que o anúncio informa com a área do cadastro fiscal.

    RESSALVA que a interface repete: divergência NÃO prova que o anúncio
    mente. É comum construir/reformar e não atualizar o cadastro — a
    prefeitura só toma conhecimento na vistoria ou na venda. Serve para
    levantar suspeita e pedir a documentação, não para acusar.
    """
    criar_tabela_comparacao(conn)

    if refazer:
        conn.execute("DELETE FROM areas_oficiais")
        conn.commit()

    ja = {r[0] for r in conn.execute("SELECT anuncio_url FROM areas_oficiais")}
    sql = """SELECT url, rua, bairro, area_construida, area_terreno
             FROM anuncios
             WHERE COALESCE(area_construida,0) > 0
               AND (COALESCE(rua,'') <> '' OR COALESCE(bairro,'') <> '')"""
    anuncios = [a for a in conn.execute(sql) if a["url"] not in ja]
    if limite:
        anuncios = anuncios[:limite]

    if verbose:
        print(f"anúncios a comparar: {len(anuncios):,d}")

    por_nivel = {"lote": 0, "rua": 0, "bairro": 0}
    graus = {"compativel": 0, "atencao": 0, "divergente": 0}
    sem_base = 0
    t0 = time.time()

    for a in anuncios:
        numero = endereco.numero_do_logradouro(a["rua"] or "")
        of = area_oficial(conn, a["rua"] or "", numero, a["bairro"] or "")
        if not of:
            sem_base += 1
            continue

        ac = a["area_construida"]
        oficial = of["area_construida"]
        dif = (100 * (ac / oficial - 1)) if (ac and oficial) else None

        conn.execute(
            """INSERT OR REPLACE INTO areas_oficiais
               (anuncio_url, nivel, n_lotes, area_anuncio, area_oficial,
                area_terreno_anuncio, area_terreno_oficial, dif_pct,
                logradouro, numero, uso, calculado_em)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,datetime('now'))""",
            (a["url"], of["nivel"], of["n_lotes"], ac, oficial,
             a["area_terreno"], of["area_terreno"], dif,
             of["logradouro"] or a["rua"], numero, of["uso"]),
        )

        por_nivel[of["nivel"]] = por_nivel.get(of["nivel"], 0) + 1
        graus[_grau_diferenca(dif)] = graus.get(_grau_diferenca(dif), 0) + 1

    conn.commit()
    if verbose:
        print(f"\ncom área oficial : {sum(por_nivel.values()):,d}")
        for nivel, n in por_nivel.items():
            if n:
                print(f"   por {nivel:<7}     : {n:,d}")
        print(f"sem base         : {sem_base:,d}")
        print("\ndiferença anúncio x cadastro:")
        for g in ("compativel", "atencao", "divergente"):
            print(f"   {g:<12}      : {graus[g]:,d}")
        print(f"tempo            : {time.time()-t0:.0f}s")
    return {"niveis": por_nivel, "graus": graus, "sem_base": sem_base}


def relatorio_areas(conn: sqlite3.Connection, limite: int = 25,
                    so_divergentes: bool = False) -> None:
    """Lista os anúncios com maior diferença entre área declarada e oficial."""
    sql = """
        SELECT o.*, a.titulo, a.bairro, a.preco, a.portal
        FROM areas_oficiais o JOIN anuncios a ON a.url = o.anuncio_url
        WHERE o.dif_pct IS NOT NULL
    """
    if so_divergentes:
        sql += " AND ABS(o.dif_pct) > 30"
    sql += " ORDER BY ABS(o.dif_pct) DESC LIMIT ?"

    linhas = conn.execute(sql, (limite,)).fetchall()
    if not linhas:
        print("Nada calculado. Rode: python geosampa.py --comparar")
        return

    print(f"{'dif':>7} {'nivel':>7} {'anúncio':>9} {'cadastro':>10} {'preço':>11}  anúncio")
    print("-" * 108)
    for r in linhas:
        marca = _grau_diferenca(r["dif_pct"])
        print(f"{r['dif_pct']:>+6.0f}% {r['nivel']:>7} {r['area_anuncio']:>8.0f}m² "
              f"{r['area_oficial']:>9.0f}m² {r['preco'] or 0:>11,.0f}  "
              f"{marca[:4]:<5} {(r['titulo'] or '')[:30]}")
        print(f"{'':>7} {'':>7} {'':>9} {'':>10} {'':>11}  "
              f"{str(r['bairro'] or '')[:20]} · {str(r['logradouro'] or '')[:28]} "
              f"{r['numero'] or ''}")

    print("\nDivergência NÃO prova que o anúncio mente: é comum construir e não")
    print("atualizar o cadastro (a prefeitura só sabe na vistoria ou na venda).")
    print("Sirva como sinal para pedir a documentação, não como acusação.")


# ---------------------------------------------------------------------------
# Ingestão
# ---------------------------------------------------------------------------
def ingerir(conn: sqlite3.Connection, forcar: bool = False,
            verbose: bool = True) -> int:
    """Baixa os lotes de todas as ruas dos bairros-alvo e grava.

    Percorre os LOGRAUDOUROS que aparecem nos anúncios (não a cidade toda):
    são ~1.100 ruas, o que dá minutos em paralelo, contra milhões de lotes
    da cidade inteira.

    A gravação acontece na thread principal (uma conexão sqlite3 não pode
    atravessar threads); as threads só fazem a chamada HTTP.
    """
    from concurrent.futures import ThreadPoolExecutor, as_completed

    criar_tabela(conn)
    ja = {r[0] for r in conn.execute(
        "SELECT DISTINCT chave FROM lotes WHERE COALESCE(chave,'') <> ''")}

    # guarda o bairro junto, para o nível 3 da cascata ter a que se referir
    linhas = conn.execute(
        """SELECT rua, MIN(bairro) AS bairro FROM anuncios
           WHERE COALESCE(rua,'') <> '' GROUP BY rua ORDER BY rua""").fetchall()
    tarefas = [(r["rua"], r["bairro"]) for r in linhas]
    if not forcar:
        tarefas = [t for t in tarefas
                   if endereco.chave_rua(t[0]) not in ja]

    if verbose:
        print(f"logradouros a consultar: {len(tarefas):,d} "
              f"({len(ja):,d} já no banco) · {PARALELO} threads")

    total_lotes = sem_dado = erros = feitos = 0
    t0 = time.time()

    def _buscar(rua: str) -> tuple[str, list[dict], str]:
        try:
            return rua, buscar_por_rua(rua), ""
        except Exception as e:  # noqa: BLE001
            return rua, [], f"{type(e).__name__}: {str(e)[:80]}"

    with ThreadPoolExecutor(max_workers=PARALELO) as pool:
        futuros = {}
        for rua, _ in tarefas:
            futuros[pool.submit(_buscar, rua)] = rua

        for fut in as_completed(futuros):
            rua, lotes, erro = fut.result()
            feitos += 1
            if erro:
                erros += 1
                if verbose:
                    print(f"  [erro] {rua[:38]}: {erro}")
            elif not lotes:
                sem_dado += 1
            else:
                bairro = next((b for r, b in tarefas if r == rua), None)
                if bairro:
                    for l in lotes:
                        l["bairro"] = bairro
                total_lotes += gravar(conn, lotes)

            if verbose and feitos % 100 == 0:
                resta = len(tarefas) - feitos
                ritmo = feitos / max(time.time() - t0, 1)
                print(f"  {feitos:,d}/{len(tarefas):,d} ruas · "
                      f"{total_lotes:,d} lotes · {ritmo:.1f} ruas/s · "
                      f"faltam ~{resta / max(ritmo, 0.01) / 60:.0f} min")

    if verbose:
        print(f"\nlotes gravados    : {total_lotes:,d}")
        print(f"ruas sem retorno  : {sem_dado:,d}")
        print(f"erros             : {erros:,d}")
        print(f"tempo             : {time.time()-t0:.0f}s")
    return total_lotes


# ---------------------------------------------------------------------------
# Relatórios
# ---------------------------------------------------------------------------
def resumo(conn: sqlite3.Connection) -> None:
    criar_tabela(conn)
    n = conn.execute("SELECT COUNT(*) FROM lotes").fetchone()[0]
    if not n:
        print("Nenhum lote no banco. Rode: python geosampa.py --ingerir")
        return
    print(f"lotes no banco : {n:,d}")
    print(f"logradouros    : "
          f"{conn.execute('SELECT COUNT(DISTINCT chave) FROM lotes').fetchone()[0]:,d}")
    print(f"bairros        : "
          f"{conn.execute('SELECT COUNT(DISTINCT bairro) FROM lotes').fetchone()[0]:,d}")

    disp = conn.execute("""SELECT COUNT(*) FROM anuncios a
        WHERE EXISTS (SELECT 1 FROM lotes l WHERE l.chave = a.rua_chave)""").fetchone()[0]
    tot = conn.execute("SELECT COUNT(*) FROM anuncios").fetchone()[0]
    print(f"\nanúncios com rua conhecida no cadastro: {disp:,d} de {tot:,d} "
          f"({100*disp/tot:.0f}%)")

    print(f"\n{'bairro':<26} {'lotes':>7} {'mediana constr.':>16} {'mediana terreno':>16}")
    print("-" * 70)
    for r in conn.execute("""
        SELECT bairro, COUNT(*) n, area_construida, area_terreno FROM (
            SELECT bairro, area_construida, area_terreno, COUNT(*) OVER (PARTITION BY bairro) n
            FROM lotes WHERE COALESCE(area_construida,0) BETWEEN 10 AND 1000
        ) GROUP BY bairro ORDER BY n DESC LIMIT 18"""):
        pass  # a mediana precisa ser calculada em Python (SQLite não tem)
    _tabela_por_bairro(conn)


def _tabela_por_bairro(conn: sqlite3.Connection) -> None:
    bairros = [r[0] for r in conn.execute(
        "SELECT DISTINCT bairro FROM lotes WHERE COALESCE(bairro,'')<>'' "
        "ORDER BY bairro")]
    linhas = []
    for b in bairros:
        cs = sorted(r[0] for r in conn.execute(
            """SELECT area_construida FROM lotes WHERE LOWER(bairro)=LOWER(?)
               AND COALESCE(area_construida,0) BETWEEN ? AND ?""",
            (b, AREA_MINIMA, AREA_MAXIMA_CASA)))
        ts = sorted(r[0] for r in conn.execute(
            """SELECT area_terreno FROM lotes WHERE LOWER(bairro)=LOWER(?)
               AND COALESCE(area_terreno,0) BETWEEN ? AND ?""",
            (b, AREA_MINIMA, AREA_MAXIMA)))
        if len(cs) < 3:
            continue
        linhas.append((b, len(cs), _mediana(cs), _mediana(ts) if ts else None))
    for b, n, c, t in sorted(linhas, key=lambda x: -x[1]):
        ct = f"{c:,.0f} m2" if c else "-"
        tt = f"{t:,.0f} m2" if t else "-"
        print(f"{b[:24]:<26} {n:>7,d} {ct:>16} {tt:>16}")


def mostrar_rua(conn: sqlite3.Connection, rua: str) -> None:
    chave = endereco.chave_tolerante(rua)
    linhas = conn.execute(
        """SELECT numero, area_terreno, area_construida, uso, situacao
           FROM lotes WHERE chave = ? ORDER BY CAST(numero AS INTEGER)""",
        (chave,)).fetchall()
    if not linhas:
        print(f"Nenhum lote para {rua!r} (chave {chave!r}).")
        print("Dica: rode --ingerir para baixar os lotes das ruas dos anúncios.")
        return
    print(f"=== {rua} — {len(linhas)} lotes (chave {chave}) ===")
    print(f"{'nº':>6} {'terreno':>10} {'construida':>12}  {'uso':<14} situacao")
    for num, t, c, uso, sit in linhas:
        tt = f"{t:,.0f} m2" if t else "-"
        cc = f"{c:,.0f} m2" if c else "-"
        print(f"{num or '-':>6} {tt:>10} {cc:>12}  {str(uso or '')[:14]:<14} {sit or ''}")


def main() -> int:
    p = argparse.ArgumentParser(description="Cadastro fiscal do GeoSampa")
    p.add_argument("--ingerir", action="store_true",
                   help="baixa os lotes das ruas dos anúncios")
    p.add_argument("--forcar", action="store_true",
                   help="rebaixa ruas já no banco")
    p.add_argument("--resumo", action="store_true", help="o que já está gravado")
    p.add_argument("--comparar", action="store_true",
                   help="compara a área do anúncio com a do cadastro")
    p.add_argument("--refazer", action="store_true", help="recalcula a comparação")
    p.add_argument("--divergentes", action="store_true",
                   help="lista só as maiores diferenças de área")
    p.add_argument("--limite", type=int, default=None, help="máx. de anúncios")
    p.add_argument("--rua", metavar="NOME", help="lista os lotes de uma rua")
    p.add_argument("--bairro", metavar="NOME",
                   help="baixa os lotes de um bairro inteiro")
    p.add_argument("--migrar", action="store_true",
                   help="recalcula a chave dos lotes já gravados (local)")
    args = p.parse_args()

    conn = sqlite3.connect(config.DB_PATH)
    conn.row_factory = sqlite3.Row

    if args.migrar:
        criar_tabela(conn)
        migrar_chaves(conn)
        conn.close()
        return 0

    if args.ingerir:
        ingerir(conn, forcar=args.forcar)
        conn.close()
        return 0

    if args.comparar:
        comparar_areas(conn, refazer=args.refazer, limite=args.limite)
        print()
        relatorio_areas(conn, so_divergentes=args.divergentes)
        conn.close()
        return 0

    if args.bairro:
        lotes = buscar_por_bairro(args.bairro)
        print(f"{len(lotes):,d} lotes em {args.bairro}")
        print(f"gravados: {gravar(conn, lotes):,d}")
        conn.close()
        return 0

    if args.rua:
        # se não estiver no banco, busca na hora
        criar_tabela(conn)
        if not conn.execute("SELECT 1 FROM lotes WHERE chave = ? LIMIT 1",
                            (endereco.chave_tolerante(args.rua),)).fetchone():
            print("nao esta no banco, buscando no GeoSampa...")
            gravar(conn, buscar_por_rua(args.rua))
        mostrar_rua(conn, args.rua)
        conn.close()
        return 0

    resumo(conn)
    conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
