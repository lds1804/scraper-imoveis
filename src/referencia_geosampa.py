"""Medida de referência INDEPENDENTE do ITBI, a partir do GeoSampa.

POR QUE EXISTE
--------------
O `modelo_casa.py` estima o valor pelo **mercado** (transações reais do ITBI).
Este módulo dá uma segunda opinião pelo **cadastro** da prefeitura, e as duas
são independentes de propósito:

    ITBI     -> preço que as pessoas efetivamente pagaram (mercado)
    GeoSampa -> o que a prefeitura registra como existente (cadastro)

Quando as duas concordam, a estimativa é sólida. Quando divergem, é sinal de
que algo precisa ser olhado — e a divergência em si é a informação.

O QUE O GEOSAMPA DÁ (e o que NÃO dá)
------------------------------------
Dá, por LOTE, com 100% de cobertura de setor/quadra e 96% de área:
    - area_terreno e area_construida OFICIAIS (a prefeitura mediu)
    - uso (Residencial, Não residencial) e situação (ATIVO/CANCELADO)
    - número do imóvel, permitindo o lote exato

**NÃO dá valor venal.** Foi procurado e não existe em dado aberto: nenhuma das
483 camadas do WFS tem valor, e nem o portal de Dados Abertos (buscas por
'venal', 'PGV', 'planta generica de valores' = zero resultados). O valor venal
por imóvel não é publicado. Ver `geosampa.py` para o registro completo.

Então o que este módulo entrega NÃO é valor — é CONFERÊNCIA DE ÁREA e
REFERÊNCIA DE QUADRA, que é o que o dado sustenta.

COMO É USADO (cascata, do mais exato ao mais amplo)
---------------------------------------------------
    1. NÚMERO do imóvel  -> o lote exato        (9% dos anúncios)
    2. só a RUA          -> mediana da rua      (51% dos anúncios)
    3. só a QUADRA       -> mediana da quadra

Medido nos 3 bairros-alvo: 935 de 1.849 anúncios (51%) têm a rua no cadastro,
e 171 (9%) têm o lote exato. Os 49% restantes não têm rua utilizável — a OLX
e o QuintoAndar não publicam o logradouro.

O QUE A DIVERGÊNCIA DE ÁREA SIGNIFICA (importante)
--------------------------------------------------
Divergência NÃO prova que o anúncio mente. É comum construir ou reformar e
não atualizar o cadastro — a prefeitura só toma conhecimento na vistoria ou
na escritura. Serve como SINAL para pedir a documentação da área, não como
acusação. Este texto aparece na interface junto do número.

Uso:
    python referencia_geosampa.py --ajustar     # agrega rua e quadra
    python referencia_geosampa.py --bairro "Vila Mangalot"
    python referencia_geosampa.py --testar <url-do-anuncio>
"""

from __future__ import annotations

import argparse
import sqlite3
import statistics
import sys
import time

import config
import endereco

try:
    sys.stdout.reconfigure(errors="replace")
    sys.stderr.reconfigure(errors="replace")
except Exception:  # noqa: BLE001
    pass

# Área construída plausível de uma casa. Acima disso o lote costuma ser
# prédio inteiro: incluí-lo na mediana da rua distorceria a referência.
AREA_CASA_MAX = 1000.0
AREA_MINIMA = 10.0

# Mínimo de lotes para a mediana de uma rua/quadra ser publicada
MIN_LOTES_RUA = 3
MIN_LOTES_QUADRA = 3

# Faixas da conferência de área. A zona morta de ±15% existe porque o anúncio
# arredonda e pode contar área de forma diferente (útil x construída, varanda,
# edícula) — dentro dela não há o que concluir.
FAIXA_OK = 15.0
FAIXA_ATENCAO = 30.0


def _mediana(v: list[float]) -> float | None:
    v = [x for x in v if x]
    if not v:
        return None
    return statistics.median(v)


def _grau(dif: float | None) -> str:
    """Classifica a divergência de área em compatível / atenção / divergente."""
    if dif is None:
        return "sem dado"
    a = abs(dif)
    if a <= FAIXA_OK:
        return "compativel"
    if a <= FAIXA_ATENCAO:
        return "atencao"
    return "divergente"


# ---------------------------------------------------------------------------
# Agregação
# ---------------------------------------------------------------------------
def criar_tabelas(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        -- mediana dos lotes de uma rua (chave normalizada)
        CREATE TABLE IF NOT EXISTS ref_rua (
            chave            TEXT PRIMARY KEY,
            logradouro       TEXT,
            n_lotes          INTEGER,
            area_construida  REAL,
            area_terreno     REAL,
            pct_residencial  REAL
        );
        CREATE INDEX IF NOT EXISTS idx_refrua_chave ON ref_rua(chave);

        -- mediana dos lotes de uma quadra fiscal (setor/quadra)
        CREATE TABLE IF NOT EXISTS ref_quadra (
            setor            TEXT,
            quadra           TEXT,
            n_lotes          INTEGER,
            area_construida  REAL,
            area_terreno     REAL,
            pct_residencial  REAL,
            setor_fiscal     TEXT,      -- o setor do ITBI, para casar
            PRIMARY KEY (setor, quadra)
        );
        CREATE INDEX IF NOT EXISTS idx_refquad ON ref_quadra(setor, quadra);
        """
    )
    conn.commit()


def ajustar(conn: sqlite3.Connection, verbose: bool = True) -> dict:
    """Agrega os lotes por RUA e por QUADRA."""
    criar_tabelas(conn)
    conn.execute("DELETE FROM ref_rua")
    conn.execute("DELETE FROM ref_quadra")
    conn.commit()

    # ---- por rua ----
    por_rua: dict[str, list] = {}
    for r in conn.execute(
        """SELECT chave, logradouro, area_construida, area_terreno, uso
           FROM lotes WHERE COALESCE(chave,'') <> ''"""
    ):
        por_rua.setdefault(r[0], []).append(r)

    n_ruas = 0
    for chave, lotes in por_rua.items():
        casas = [l for l in lotes
                 if l[2] is None or AREA_MINIMA <= l[2] <= AREA_CASA_MAX]
        constr = [l[2] for l in casas if l[2]]
        terr = [l[3] for l in casas if l[3]]
        if len(constr) < MIN_LOTES_RUA:
            continue
        resid = sum(1 for l in casas if "resid" in str(l[4] or "").lower())
        conn.execute(
            """INSERT OR REPLACE INTO ref_rua
               (chave, logradouro, n_lotes, area_construida, area_terreno,
                pct_residencial)
               VALUES (?,?,?,?,?,?)""",
            (chave, lotes[0][1], len(constr), _mediana(constr),
             _mediana(terr), 100 * resid / max(len(casas), 1)),
        )
        n_ruas += 1

    # ---- por quadra (setor + quadra) ----
    n_quadras = 0
    quadras: dict[tuple, list] = {}
    for r in conn.execute(
        """SELECT setor, quadra, area_construida, area_terreno, uso
           FROM lotes WHERE COALESCE(setor,'')<>'' AND COALESCE(quadra,'')<>''"""
    ):
        quadras.setdefault((r[0], r[1]), []).append(r)

    for (setor, quadra), lotes in quadras.items():
        casas = [l for l in lotes
                 if l[2] is None or AREA_MINIMA <= l[2] <= AREA_CASA_MAX]
        constr = [l[2] for l in casas if l[2]]
        terr = [l[3] for l in casas if l[3]]
        if len(constr) < MIN_LOTES_QUADRA:
            continue
        resid = sum(1 for l in casas if "resid" in str(l[4] or "").lower())
        conn.execute(
            """INSERT OR REPLACE INTO ref_quadra
               (setor, quadra, n_lotes, area_construida, area_terreno,
                pct_residencial, setor_fiscal)
               VALUES (?,?,?,?,?,?,?)""",
            (setor, quadra, len(constr), _mediana(constr), _mediana(terr),
             100 * resid / max(len(casas), 1), setor),
        )
        n_quadras += 1

    conn.commit()
    if verbose:
        print(f"referências: {n_ruas:,d} ruas · {n_quadras:,d} quadras")
        r = conn.execute("""SELECT AVG(area_construida) m,
            AVG(area_terreno) t FROM ref_quadra""").fetchone()
        if r and r[0]:
            print(f"  mediana média: construção {r[0]:,.0f} m² · "
                  f"terreno {r[1] or 0:,.0f} m²")
    return {"ruas": n_ruas, "quadras": n_quadras}


# ---------------------------------------------------------------------------
# Consulta (cascata: lote -> rua -> quadra)
# ---------------------------------------------------------------------------
def buscar(conn: sqlite3.Connection, rua: str, numero: str = "",
           setor: str = "", quadra: str = "") -> dict | None:
    """Referência do cadastro, do mais exato ao mais amplo."""
    chave = endereco.chave_tolerante(rua) if rua else ""

    # ---- 1) lote exato pelo número ----
    if chave and numero:
        l = conn.execute(
            """SELECT numero, area_construida, area_terreno, uso, situacao,
                      setor, quadra
               FROM lotes WHERE chave = ? AND numero = ?
                 AND COALESCE(area_construida,0) > 0 LIMIT 1""",
            (chave, numero)).fetchone()
        if l:
            return {
                "nivel": "lote", "n_lotes": 1,
                "area_construida": l[1], "area_terreno": l[2],
                "uso": l[3], "situacao": l[4],
                "setor": l[5], "quadra": l[6],
                "logradouro": rua, "numero": numero,
            }

    # ---- 2) mediana da rua ----
    if chave:
        r = conn.execute("SELECT * FROM ref_rua WHERE chave = ?", (chave,)).fetchone()
        if r:
            return {
                "nivel": "rua", "n_lotes": r["n_lotes"],
                "area_construida": r["area_construida"],
                "area_terreno": r["area_terreno"],
                "uso": None, "situacao": None,
                "pct_residencial": r["pct_residencial"],
                "logradouro": r["logradouro"], "numero": None,
            }

    # ---- 3) mediana da quadra ----
    if setor and quadra:
        r = conn.execute(
            "SELECT * FROM ref_quadra WHERE setor=? AND quadra=?",
            (setor, quadra)).fetchone()
        if r:
            return {
                "nivel": "quadra", "n_lotes": r["n_lotes"],
                "area_construida": r["area_construida"],
                "area_terreno": r["area_terreno"],
                "uso": None, "situacao": None,
                "pct_residencial": r["pct_residencial"],
                "logradouro": None, "numero": None,
            }
    return None


def conferir(conn: sqlite3.Connection, anuncio: sqlite3.Row) -> dict | None:
    """Compara a área do ANÚNCIO com a do CADASTRO.

    É esta função que produz a segunda medida de referência: o cadastro diz
    o que existe oficialmente, e a diferença contra o anúncio é o sinal.
    """
    ref = buscar(conn, anuncio["rua"] or "",
                 endereco.numero_do_logradouro(anuncio["rua"] or ""))
    if not ref:
        return None

    ac = anuncio["area_construida"]
    of = ref["area_construida"]
    dif = (100 * (ac / of - 1)) if (ac and of) else None

    at = anuncio["area_terreno"]
    ot = ref["area_terreno"]
    dif_t = (100 * (at / ot - 1)) if (at and ot) else None

    return {
        **ref,
        "area_anuncio": ac, "area_oficial": of, "dif_pct": dif,
        "terreno_anuncio": at, "terreno_oficial": ot, "dif_terreno_pct": dif_t,
        "grau": _grau(dif),
        "grau_terreno": _grau(dif_t),
    }


def calcular(conn: sqlite3.Connection, bairro: str = "",
             refazer: bool = False, limite: int | None = None,
             verbose: bool = True) -> dict:
    """Grava `areas_oficiais` para os anúncios (a tabela que a interface lê)."""
    criar_tabelas(conn)
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS areas_oficiais (
            anuncio_url      TEXT PRIMARY KEY,
            nivel            TEXT,
            n_lotes          INTEGER,
            area_anuncio     REAL,
            area_oficial     REAL,
            area_terreno_anuncio REAL,
            area_terreno_oficial REAL,
            dif_pct          REAL,
            logradouro       TEXT,
            numero           TEXT,
            uso              TEXT,
            calculado_em     TEXT
        );
        """
    )
    if refazer:
        conn.execute("DELETE FROM areas_oficiais")
        conn.commit()

    ja = {r[0] for r in conn.execute("SELECT anuncio_url FROM areas_oficiais")}
    sql = """SELECT url, rua, bairro, area_construida, area_terreno
             FROM anuncios WHERE COALESCE(rua,'') <> ''"""
    params: list = []
    if bairro:
        sql += " AND bairro = ?"
        params.append(bairro)
    anuncios = [a for a in conn.execute(sql, params) if a["url"] not in ja]
    if limite:
        anuncios = anuncios[:limite]

    if verbose:
        print(f"anúncios a conferir: {len(anuncios):,d}")

    por_nivel: dict[str, int] = {}
    graus: dict[str, int] = {}
    sem = 0
    t0 = time.time()
    for a in anuncios:
        r = conferir(conn, a)
        if not r:
            sem += 1
            continue
        conn.execute(
            """INSERT OR REPLACE INTO areas_oficiais
               (anuncio_url, nivel, n_lotes, area_anuncio, area_oficial,
                area_terreno_anuncio, area_terreno_oficial, dif_pct,
                logradouro, numero, uso, calculado_em)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,datetime('now'))""",
            (a["url"], r["nivel"], r["n_lotes"], r["area_anuncio"],
             r["area_oficial"], r["terreno_anuncio"], r["terreno_oficial"],
             r["dif_pct"], r["logradouro"], r["numero"], r["uso"]),
        )
        por_nivel[r["nivel"]] = por_nivel.get(r["nivel"], 0) + 1
        graus[r["grau"]] = graus.get(r["grau"], 0) + 1
    conn.commit()

    if verbose:
        print(f"\ncom referência do cadastro: {sum(por_nivel.values()):,d}")
        for k in ("lote", "rua", "quadra"):
            if por_nivel.get(k):
                print(f"   pelo {k:<7}        : {por_nivel[k]:,d}")
        print(f"sem referência           : {sem:,d}")
        print("\ndivergência de área (anúncio x cadastro):")
        for k in ("compativel", "atencao", "divergente", "sem dado"):
            if graus.get(k):
                print(f"   {k:<12}          : {graus[k]:,d}")
        print(f"tempo: {time.time()-t0:.0f}s")
    return {"niveis": por_nivel, "graus": graus, "sem": sem}


def relatorio(conn: sqlite3.Connection, limite: int = 20,
              so_divergentes: bool = False) -> None:
    sql = """SELECT o.*, a.titulo, a.bairro, a.preco
             FROM areas_oficiais o JOIN anuncios a ON a.url = o.anuncio_url
             WHERE o.dif_pct IS NOT NULL"""
    if so_divergentes:
        sql += " AND ABS(o.dif_pct) > 30"
    sql += " ORDER BY ABS(o.dif_pct) DESC LIMIT ?"
    linhas = conn.execute(sql, (limite,)).fetchall()
    if not linhas:
        print("Nada calculado. Rode: python referencia_geosampa.py --calcular")
        return
    print(f"{'dif':>7} {'nivel':>7} {'anúncio':>9} {'cadastro':>10} "
          f"{'preço':>12}  anúncio")
    print("-" * 104)
    for r in linhas:
        print(f"{r['dif_pct']:>+6.0f}% {r['nivel']:>7} {r['area_anuncio'] or 0:>8.0f}m² "
              f"{r['area_oficial'] or 0:>9.0f}m² {r['preco'] or 0:>12,.0f}  "
              f"{_grau(r['dif_pct'])[:4]:<5} {(r['titulo'] or '')[:28]}")
    print("\nDivergência NÃO prova que o anúncio mente: é comum construir e não")
    print("atualizar o cadastro (a prefeitura só sabe na vistoria ou na venda).")
    print("Serve como sinal para pedir a documentação, não como acusação.")


def main() -> int:
    p = argparse.ArgumentParser(description="Referência independente (GeoSampa)")
    p.add_argument("--ajustar", action="store_true", help="agrega rua e quadra")
    p.add_argument("--calcular", action="store_true",
                   help="grava a conferência de área dos anúncios")
    p.add_argument("--refazer", action="store_true")
    p.add_argument("--bairro", default="", help="filtra por bairro")
    p.add_argument("--limite", type=int, default=None)
    p.add_argument("--amostra", type=int, default=20, help="linhas no relatório")
    p.add_argument("--divergentes", action="store_true")
    p.add_argument("--testar", metavar="URL", help="confere um anúncio")
    args = p.parse_args()

    conn = sqlite3.connect(config.DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=300000")

    if args.ajustar:
        ajustar(conn)
    elif args.calcular:
        calcular(conn, bairro=args.bairro, refazer=args.refazer,
                 limite=args.limite)
        print()
        relatorio(conn, limite=args.amostra, so_divergentes=args.divergentes)
    elif args.testar:
        a = conn.execute("""SELECT url, titulo, rua, area_construida,
                                   area_terreno, preco FROM anuncios
                            WHERE url LIKE ? LIMIT 1""",
                         (f"%{args.testar}%",)).fetchone()
        if not a:
            print(f"Anúncio não encontrado: {args.testar!r}")
        else:
            r = conferir(conn, a)
            if not r:
                print("Sem referência de cadastro para este anúncio.")
            else:
                print(f"{(a['titulo'] or '')[:70]}")
                print(f"  rua: {a['rua']}  (referência pelo {r['nivel']}, "
                      f"{r['n_lotes']} lote(s))")
                print(f"  área construída: anúncio {r['area_anuncio'] or 0:,.0f} m² "
                      f"x cadastro {r['area_oficial'] or 0:,.0f} m² "
                      f"-> {r['dif_pct'] or 0:+.0f}% ({r['grau']})")
                print(f"  terreno        : anúncio {r['terreno_anuncio'] or 0:,.0f} m² "
                      f"x cadastro {r['terreno_oficial'] or 0:,.0f} m² "
                      f"-> {(r['dif_terreno_pct'] or 0):+.0f}%")
    else:
        print(__doc__)
    conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
