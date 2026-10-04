"""Valor VENAL estimado — a avaliação oficial da prefeitura, por região.

POR QUE EXISTE
--------------
O usuário pediu o valor venal na interface. Ele existe no ITBI: **98% das
transações (528 mil de 537 mil) trazem `valor_venal`**, que é a avaliação
municipal do imóvel — a mesma base do IPTU.

Como cada transação traz venal E preço, a razão entre os dois pode ser MEDIDA
em vez de estimada. Foi o que se fez aqui.

POR QUE NÃO SE CALCULA O VENAL DIRETO DAS ÁREAS
-----------------------------------------------
Tentei ajustar `venal ~ terreno + construção` (a forma como a prefeitura
calcula oficialmente). O ajuste é ruim e o motivo é instrutivo:

    R$/m² TERRENO    :    360     <- irreal, o mesmo defeito do mercado
    R$/m² CONSTRUÇÃO :  6.940
    R²               :  0,510

O coeficiente do terreno despenca de novo (R$ 360/m²), como aconteceu com o
valor de transação. **Isso NÃO é erro de dado** — é que o ITBI não publica a
FACE DE QUADRA, que é a chave do cálculo oficial. O valor do terreno na PGV
muda por face de quadra, e essa informação não está nos microdados.

Resultado prático: reconstruir a fórmula da PGV pelas áreas não funciona
(R² 0,51, com erros de −58% a +59%).

A RAZÃO, AO CONTRÁRIO, É MEDÍVEL
--------------------------------
Cada transação dá `venal / preço` de graça. Medido desde 2018:

    geral (mediana)      0,816   -> o venal é ~18% menor que o mercado
    p25                  0,593
    p75                  1,023

E varia por região: 1,6x entre o p10 e o p90 dos CEPs. Isso importa, porque
em São Paulo há regiões onde o venal está abaixo do mercado (subavaliado) e
outras onde está acima.

Nos três bairros-alvo a razão é estável e alta (0,84 a 1,00), o que faz
sentido: são bairros consolidados, com mercado e cadastro alinhados.

COMO É USADO
------------
    valor_venal_estimado = preço_pedido * razao_venal_mercado_do_cep

A razão vem do CEP, com cascata CEP-5 -> CEP-4 -> CEP-3 -> cidade, cada nível
exigindo um mínimo de pares para a mediana ser confiável.

RESSALVA (vai junto do número na interface)
-------------------------------------------
O valor venal é uma referência TRIBUTÁRIA, não de mercado. Ele serve para
comparar com o IPTU e para saber se o anúncio está pedindo acima do que a
prefeitura avalia — mas não substitui a comparação com preços praticados.

Uso:
    python valor_venal.py --ajustar        # agrega as razões por região
    python valor_venal.py --testar 05133004 670000
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

# Só transações residenciais e recentes: o venal de imóvel comercial segue
# outra lógica, e valores antigos não refletem a planta vigente.
USO_RESIDENCIAL = "10"
ANO_MINIMO = 2018

# Faixa de razões aceitáveis. Fora dela é erro de declaração (ex.: doação
# disfarçada de compra e venda, que faz a razão explodir).
RAZAO_MIN = 0.05
RAZAO_MAX = 20.0

# Mínimo de pares (venal, preço) por região
MIN_PARES = 25
MIN_PARES_GROSSO = 15

NIVEIS_CEP = (5, 4, 3)


def _cep_norm(valor) -> str:
    """CEP com 8 dígitos.

    Mesma armadilha já encontrada duas vezes: o ITBI guarda sem o zero à
    esquerda ('5128000'), o anúncio com 8 ('05128000'). Sem normalizar, as
    regiões nunca casam.
    """
    return endereco.normalizar_cep(valor) or ""


def _mediana(v: list[float]) -> float | None:
    return statistics.median(v) if v else None


def criar_tabela(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS razao_venal (
            regiao      TEXT PRIMARY KEY,   -- 'cep5:05133' | 'cidade'
            n           INTEGER,
            razao       REAL,               -- venal / preco (mediana)
            p25         REAL,
            p75         REAL,
            ajustado_em TEXT
        );
        """
    )
    conn.commit()


def ajustar(conn: sqlite3.Connection, verbose: bool = True) -> int:
    """Mede a razão venal/mercado por região e grava."""
    criar_tabela(conn)
    linhas = conn.execute(
        """SELECT valor_venal_corrigido / valor_transacao_corrigido AS razao,
                  cep
           FROM itbi
           WHERE natureza LIKE '1.%' AND proporcao >= 99.9
             AND uso = ?
             AND ano_arquivo >= ?
             AND COALESCE(valor_venal_corrigido,0) > 0
             AND COALESCE(valor_transacao_corrigido,0) > 10000
             AND area_construida BETWEEN 20 AND 500
             AND COALESCE(cep,'') <> ''""",
        (USO_RESIDENCIAL, ANO_MINIMO),
    ).fetchall()

    por_regiao: dict[str, list[float]] = {}
    todos: list[float] = []
    for razao, cep in linhas:
        if not razao or not (RAZAO_MIN < razao < RAZAO_MAX):
            continue
        todos.append(razao)
        d = _cep_norm(cep)
        for n in NIVEIS_CEP:
            if len(d) >= n:
                por_regiao.setdefault(f"cep{n}:{d[:n]}", []).append(razao)

    conn.execute("DELETE FROM razao_venal")
    gravadas = 0
    for regiao, vals in por_regiao.items():
        n_nivel = int(regiao.split(":")[0].replace("cep", ""))
        minimo = MIN_PARES if n_nivel == 5 else MIN_PARES_GROSSO
        if len(vals) < minimo:
            continue
        v = sorted(vals)
        n = len(v)
        conn.execute(
            """INSERT OR REPLACE INTO razao_venal
               (regiao, n, razao, p25, p75, ajustado_em)
               VALUES (?,?,?,?,?,datetime('now'))""",
            (regiao, n, v[n // 2], v[int(.25 * n)], v[int(.75 * n)]),
        )
        gravadas += 1

    if todos:
        v = sorted(todos)
        n = len(v)
        conn.execute(
            """INSERT OR REPLACE INTO razao_venal
               (regiao, n, razao, p25, p75, ajustado_em)
               VALUES ('cidade',?,?,?,?,datetime('now'))""",
            (n, v[n // 2], v[int(.25 * n)], v[int(.75 * n)]),
        )
        gravadas += 1
    conn.commit()

    if verbose:
        print(f"razão venal/mercado: {gravadas} regiões "
              f"(de {len(todos):,d} pares venal+preço)")
        if todos:
            v = sorted(todos)
            n = len(v)
            print(f"  mediana da cidade: {v[n//2]:.3f} "
                  f"(o venal é ~{100*(1-v[n//2]):.0f}% menor que o mercado)")
    return gravadas


def razao_da_regiao(conn: sqlite3.Connection, cep: str) -> tuple[float, str, int]:
    """Razão venal/mercado da região, com cascata CEP-5 -> CEP-4 -> CEP-3 -> cidade."""
    d = _cep_norm(cep)
    for n in NIVEIS_CEP:
        if len(d) >= n:
            r = conn.execute("SELECT razao, n FROM razao_venal WHERE regiao = ?",
                             (f"cep{n}:{d[:n]}",)).fetchone()
            if r:
                return r[0], f"cep{n}:{d[:n]}", r[1]
    r = conn.execute("SELECT razao, n FROM razao_venal WHERE regiao = 'cidade'"
                     ).fetchone()
    if r:
        return r[0], "cidade", r[1]
    return 0.816, "padrao", 0   # mediana medida da cidade, como último recurso


def estimar(conn: sqlite3.Connection, cep: str, preco: float) -> dict | None:
    """Estima o valor venal a partir do preço pedido e da razão da região."""
    if not preco:
        return None
    razao, regiao, n = razao_da_regiao(conn, cep)
    if not razao:
        return None
    return {
        "valor_venal": preco * razao,
        "razao": razao,
        "regiao": regiao,
        "n_pares": n,
        "pct_do_preco": 100 * razao,
    }


def calcular(conn: sqlite3.Connection, refazer: bool = False,
             limite: int | None = None, verbose: bool = True) -> int:
    """Grava o valor venal estimado de cada anúncio em `valores_venais`."""
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS valores_venais (
            anuncio_url    TEXT PRIMARY KEY,
            valor_venal    REAL,
            razao          REAL,
            regiao         TEXT,
            n_pares        INTEGER,
            preco          REAL,
            calculado_em   TEXT
        );
        """
    )
    if refazer:
        conn.execute("DELETE FROM valores_venais")
        conn.commit()

    ja = {r[0] for r in conn.execute("SELECT anuncio_url FROM valores_venais")}
    anuncios = [a for a in conn.execute(
        """SELECT url, cep, preco FROM anuncios
           WHERE COALESCE(preco,0) > 0""") if a["url"] not in ja]
    if limite:
        anuncios = anuncios[:limite]

    if verbose:
        print(f"anúncios a calcular: {len(anuncios):,d}")

    feitos = sem = 0
    t0 = time.time()
    for a in anuncios:
        r = estimar(conn, a["cep"] or "", a["preco"])
        if not r:
            sem += 1
            continue
        conn.execute(
            """INSERT OR REPLACE INTO valores_venais
               (anuncio_url, valor_venal, razao, regiao, n_pares, preco,
                calculado_em)
               VALUES (?,?,?,?,?,?,datetime('now'))""",
            (a["url"], r["valor_venal"], r["razao"], r["regiao"],
             r["n_pares"], a["preco"]),
        )
        feitos += 1
    conn.commit()

    if verbose:
        print(f"com valor venal: {feitos:,d} ({sem:,d} sem preço)")
        print(f"tempo: {time.time()-t0:.0f}s")
    return feitos


def main() -> int:
    p = argparse.ArgumentParser(description="Valor venal estimado (referência)")
    p.add_argument("--ajustar", action="store_true")
    p.add_argument("--calcular", action="store_true")
    p.add_argument("--refazer", action="store_true")
    p.add_argument("--testar", nargs=2, metavar=("CEP", "PRECO"))
    args = p.parse_args()

    conn = sqlite3.connect(config.DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=300000")
    criar_tabela(conn)

    if args.testar:
        cep, preco = args.testar
        r = estimar(conn, cep, float(preco))
        if not r:
            print("Sem dado. Rode --ajustar.")
        else:
            print(f"CEP {cep} · preço R$ {float(preco):,.0f}")
            print(f"  razão da região ({r['regiao']}, {r['n_pares']:,d} pares): "
                  f"{r['razao']:.3f}")
            print(f"  valor venal estimado: R$ {r['valor_venal']:,.0f}")
            print(f"  (o venal é {r['pct_do_preco']:.0f}% do preço pedido)")
    elif args.ajustar:
        ajustar(conn)
    elif args.calcular:
        calcular(conn, refazer=args.refazer)
    else:
        print(__doc__)
    conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
