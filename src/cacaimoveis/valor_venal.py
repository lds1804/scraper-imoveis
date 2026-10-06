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
    python valor_venal.py --ajustar-ruas   # monta o mapa rua -> CEP
    python valor_venal.py --calcular       # grava o venal de cada anúncio
    python valor_venal.py --testar 05133004 670000
"""

from __future__ import annotations

import argparse
import sqlite3
import statistics
import sys
import time

from cacaimoveis import config, endereco, migracoes

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

# ---------------------------------------------------------------------------
# Recuperar o CEP pela RUA
# ---------------------------------------------------------------------------
# Só o ZAP publica o CEP no anúncio (os 1.895 dele têm). OLX, QuintoAndar e
# Imovelweb NÃO publicam: 100% dos anúncios desses três vêm sem CEP. Sem CEP não
# há região fina e sobrava o nível "cidade" — que é grosso e foi o que fez 60%
# dos anúncios caírem no mesmo balde.
#
# Só que o anúncio quase sempre traz a RUA, e o ITBI tem 537 mil transações com
# rua E CEP. Dá para tirar o CEP das próprias transações. Medido em amostra
# separada: quando o CEP é recuperado pela rua, ele está **certo em 99%** dos
# casos, e o erro da estimativa cai (mediano 26,9% -> 25,2% entre os afetados).
#
# Só entram ruas com evidência suficiente. Avenidas longas cruzam vários CEPs e
# ficam de fora — para elas o CEP dominante não representa nada.
MIN_CASOS_POR_RUA = 3        # transações na mesma rua
PROPORCAO_CEP_DA_RUA = 0.60  # quanto o CEP dominante precisa representar


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

        -- CEP deduzido da rua, para os portais que não publicam o CEP.
        -- Ver o comentário de MIN_CASOS_POR_RUA.
        CREATE TABLE IF NOT EXISTS cep_da_rua (
            rua_chave   TEXT PRIMARY KEY,
            cep5        TEXT,
            n           INTEGER,           -- transações que sustentam o mapa
            ajustado_em TEXT
        );
        """
    )
    conn.commit()
    # `valores_venais` (a tabela que o site lê) vem das migrações. Antes ela
    # não era criada em lugar nenhum e um banco novo quebrava aqui.
    migracoes.migrar(conn)


def ajustar_ruas(conn: sqlite3.Connection, verbose: bool = True) -> int:
    """Monta o mapa rua -> CEP a partir das transações do ITBI.

    Sem isso, os anúncios sem CEP (60% da base) caem no nível "cidade".
    """
    conn.execute(
        """CREATE TABLE IF NOT EXISTS cep_da_rua (
               rua_chave   TEXT PRIMARY KEY,
               cep5        TEXT,
               n           INTEGER,
               ajustado_em TEXT)"""
    )
    por_rua: dict[str, dict[str, int]] = {}
    for r in conn.execute(
        """SELECT rua_chave AS k, cep_norm AS c, COUNT(*) AS n
           FROM itbi
           WHERE COALESCE(rua_chave,'') <> '' AND COALESCE(cep_norm,'') <> ''
           GROUP BY 1, 2"""
    ):
        c = _cep_norm(r["c"])
        if len(c) >= 5:
            # SOMA, não sobrescreve. O SQL agrupa por CEP de 8 dígitos, então
            # uma mesma rua aparece várias vezes com o MESMO prefixo de 5
            # (ex.: 05133001 e 05133004 → ambos '05133'). Com `= r["n"]` ficava
            # só o último contador.
            #
            # Medido antes de corrigir: 1.419 ruas (4,0%) com total errado,
            # 1.417 delas SUBcontadas — e 254 ruas válidas eram descartadas
            # por caírem abaixo de MIN_CASOS_POR_RUA sem motivo. O CEP
            # dominante trocado é raro (2 casos), então o defeito era
            # conservador: perdia rua boa, não criava rua ruim.
            acumulado = por_rua.setdefault(r["k"], {})
            acumulado[c[:5]] = acumulado.get(c[:5], 0) + r["n"]

    conn.execute("DELETE FROM cep_da_rua")
    gravadas = 0
    for chave, ceps in por_rua.items():
        cep5, n = max(ceps.items(), key=lambda x: x[1])
        total = sum(ceps.values())
        if n < MIN_CASOS_POR_RUA or n / total < PROPORCAO_CEP_DA_RUA:
            continue
        conn.execute(
            """INSERT OR REPLACE INTO cep_da_rua
               (rua_chave, cep5, n, ajustado_em)
               VALUES (?,?,?,datetime('now'))""",
            (chave, cep5, total),
        )
        gravadas += 1
    conn.commit()

    if verbose:
        print(f"CEP por rua: {gravadas:,d} ruas "
              f"(de {len(por_rua):,d} com transação no ITBI)")
    return gravadas


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


def cep_efetivo(conn: sqlite3.Connection, cep: str, rua_chave: str) -> tuple[str, str]:
    """CEP do anúncio e de onde ele veio.

    Devolve `(cep_normalizado, origem)`, com origem em:

        'anuncio'  o próprio anúncio publica o CEP (só o ZAP publica)
        'rua'      deduzido da rua, pelo mapa tirado do ITBI
        ''         não foi possível determinar

    Só aceita a dedução quando ela é confiável: a rua precisa ter
    `MIN_CASOS_POR_RUA` transações e o mesmo CEP nelas. É o que faz a
    recuperação acertar ~99% das vezes.
    """
    d = _cep_norm(cep)
    if d:
        return d, "anuncio"
    if rua_chave and _tem_tabela(conn, "cep_da_rua"):
        r = conn.execute(
            "SELECT cep5, n FROM cep_da_rua WHERE rua_chave = ?", (rua_chave,)
        ).fetchone()
        if r and r[0] and r[1] >= MIN_CASOS_POR_RUA:
            # CUIDADO: `_cep_norm` REJEITA 5 dígitos de propósito (no ITBI isso
            # é registro truncado). A chave aqui é um PREFIXO de CEP, não um CEP.
            # Completar até 8 dígitos preserva os três níveis da cascata —
            # `cep5:` usa os 5 reais, `cep4:` e `cep3:` usam 4 e 3, e o "0" no
            # fim nunca chega a ser consultado. Sem isto o prefixo virava
            # string vazia e tudo caía de novo no nível "cidade".
            return r[0].ljust(8, "0"), "rua"
    return "", ""


def _tem_tabela(conn: sqlite3.Connection, nome: str) -> bool:
    return conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (nome,)
    ).fetchone() is not None


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


def estimar(conn: sqlite3.Connection, cep: str, preco: float,
            rua_chave: str = "", mediana_mercado: float | None = None) -> dict | None:
    """Estima o valor venal a partir da razão venal/mercado da região.

    `rua_chave` é opcional e só serve para deduzir o CEP quando o anúncio não
    publica um (ver `cep_efetivo`). Sem ele o comportamento é o antigo.

    `mediana_mercado` é o preço de mercado da região (vindo do ITBI, do
    `comparar_itbi.py`). Quando existe, a razão é aplicada SOBRE ELE, e não
    sobre o preço pedido.

    POR QUÊ: a razão venal/preço é medida sobre o preço DECLARADO nas vendas,
    mas o preço PEDIDO no anúncio costuma ficar ~23% acima do praticado.
    Aplicar a razão sobre o pedido inflava o valor venal — medido: o "valor
    venal" projetado ficava ACIMA da mediana de mercado em 45% dos casos,
    quando o VVR real fica sistematicamente ABAIXO (razão ~0,82).
    """
    if not preco and not mediana_mercado:
        return None
    cep_usado, origem = cep_efetivo(conn, cep, rua_chave)
    razao, regiao, n = razao_da_regiao(conn, cep_usado)
    if not razao:
        return None
    # A base é a melhor estimativa do valor de MERCADO: a mediana do ITBI
    # quando existe, senão o pedido. O venal é sempre uma fração dessa base.
    base = mediana_mercado or preco
    return {
        "valor_venal": base * razao,
        "razao": razao,
        "regiao": regiao,
        "n_pares": n,
        "pct_do_preco": 100 * razao,
        "origem_cep": origem or "nenhuma",
        "cep_usado": cep_usado,
        "base": base,
    }


def calcular(conn: sqlite3.Connection, refazer: bool = False,
             limite: int | None = None, verbose: bool = True) -> int:
    """Grava o valor venal estimado de cada anúncio em `valores_venais`."""
    criar_tabela(conn)
    if refazer:
        conn.execute("DELETE FROM valores_venais")
        conn.commit()

    tem_rua = _tem_tabela(conn, "cep_da_rua")
    if not tem_rua and verbose:
        print("AVISO: tabela cep_da_rua ausente — rodar --ajustar-ruas "
              "melhora a precisão de quem não publica CEP")

    ja = {r[0] for r in conn.execute("SELECT anuncio_url FROM valores_venais")}
    anuncios = [a for a in conn.execute(
        """SELECT url, cep, rua_chave, preco FROM anuncios
           WHERE COALESCE(preco,0) > 0""") if a["url"] not in ja]
    if limite:
        anuncios = anuncios[:limite]

    # A mediana de MERCADO (do comparar_itbi) serve de base para o venal, em
    # vez do preço pedido — ver a docstring de `estimar`. Só se a tabela
    # existir e tiver dado; senão, cai de volta no pedido.
    medianas: dict[str, float] = {}
    if _tem_tabela(conn, "comparacoes"):
        medianas = {
            r["anuncio_url"]: r["mediana"]
            for r in conn.execute(
                "SELECT anuncio_url, mediana FROM comparacoes "
                "WHERE COALESCE(mediana,0) > 0")
        }

    if verbose:
        print(f"anúncios a calcular: {len(anuncios):,d} "
              f"({len(medianas):,d} com mediana de mercado)")

    feitos = sem = 0
    origens: dict[str, int] = {}
    t0 = time.time()
    for a in anuncios:
        r = estimar(conn, a["cep"] or "", a["preco"], a["rua_chave"] or "",
                    medianas.get(a["url"]))
        if not r:
            sem += 1
            continue
        origens[r["origem_cep"]] = origens.get(r["origem_cep"], 0) + 1
        conn.execute(
            """INSERT OR REPLACE INTO valores_venais
               (anuncio_url, valor_venal, razao, regiao, n_pares, preco,
                origem_cep, calculado_em)
               VALUES (?,?,?,?,?,?,?,datetime('now'))""",
            (a["url"], r["valor_venal"], r["razao"], r["regiao"],
             r["n_pares"], a["preco"], r["origem_cep"]),
        )
        feitos += 1
    conn.commit()

    if verbose:
        print(f"com valor venal: {feitos:,d} ({sem:,d} sem preço)")
        for k in ("anuncio", "rua", "nenhuma"):
            if origens.get(k):
                pct = 100 * origens[k] / max(feitos, 1)
                print(f"  CEP do {k:<8}: {origens[k]:>5,d} ({pct:>4.1f}%)")
        print(f"tempo: {time.time()-t0:.0f}s")
    return feitos


def main() -> int:
    p = argparse.ArgumentParser(description="Valor venal estimado (referência)")
    p.add_argument("--ajustar", action="store_true")
    p.add_argument("--ajustar-ruas", action="store_true",
                   help="monta o mapa rua -> CEP (para quem não publica CEP)")
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
        ajustar_ruas(conn)
    elif args.ajustar_ruas:
        ajustar_ruas(conn)
    elif args.calcular:
        calcular(conn, refazer=args.refazer)
    else:
        print(__doc__)
    conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
