"""Valor de CASA: terreno (por localização) + construção (depreciada).

POR QUE ISTO EXISTE
-------------------
A primeira versão comparava anúncio com ITBI dividindo valor por área
(`R$/m²`), tratando casa como apartamento. O usuário apontou o erro: **numa
casa o valor é o terreno MAIS a construção, e a construção deprecia**.
Metodologicamente ele está certo — é assim que a NBR 14653 avalia e é como a
própria prefeitura calcula o valor venal.

O QUE FOI MEDIDO (e que mudou o desenho)
----------------------------------------
Tentei primeiro a regressão direta, `valor ~ terreno + construção`. **Ela
falha, e os dados mostram por quê**:

    teste com a construção FIXA em 90-130 m², variando o terreno:
        terreno  67 m² -> valor mediano R$ 479.000
        terreno 110 m² -> valor mediano R$ 490.000
        terreno 460 m² -> valor mediano R$ 500.000
        (terreno 5,8x maior, valor 1,04x  ->  praticamente não muda)

O mesmo acontece com o valor venal da prefeitura: ajustando
`venal ~ terreno + construção`, o coeficiente do terreno sai R$ 65/m² contra
R$ 7.081/m² da construção. Ou seja, **não é defeito do dado do ITBI** — a
área do terreno, sozinha, não carrega o valor.

A explicação: o valor do terreno em São Paulo está na LOCALIZAÇÃO (CEP, face
de quadra), não na metragem. É por isso que a Planta Genérica de Valores usa
valor por FACE DE QUADRA.

O MODELO QUE FUNCIONA
---------------------
Os dois valores são estimados SEPARADAMENTE, cada um pela sua fonte natural:

  1. R$/m² DO TERRENO — de transações de imóveis PRATICAMENTE SEM CONSTRUÇÃO
     (construção <= 40 m²): nessas, o preço pago É o preço do terreno.
     Medido em 2022: mediana R$ 1.456/m² (p25 850, p75 2.266), n=354.
     Sendo atributo de LOCALIZAÇÃO, é calculado por REGIÃO (CEP) — que é o
     que a PGV faz.

  2. R$/m² DA CONSTRUÇÃO — do resíduo `valor - terreno*preço_terreno` da
     região, dividido pela área construída. Por região, e espera-se que CAIA
     com o tempo (depreciação).

  3. valor = area_terreno * R$/m²_terreno_da_região
           + area_construida * R$/m²_construção_da_região

A metragem do terreno ENTRA na conta — multiplicada pelo preço da região.
Assim o modelo respeita o método pedido: um lote maior no mesmo bairro vale
mais (mais m² ao mesmo R$/m²). O que ele não faz — porque o dado não sustenta
— é fingir que dá para saber o valor do terreno só pela metragem, ignorando
a localização.

DEPRECIAÇÃO
-----------
O ITBI não tem coluna de idade. A depreciação é MEDIDA, não presumida:
`ajustar_depreciacao()` monta a série anual e mostra o que aconteceu com o
R$/m² da construção em termos reais.

CORREÇÃO MONETÁRIA — POR QUE NÃO IGP-M
--------------------------------------
O usuário pediu IGP-M. **Não foi possível**, por razão objetiva:
`api.bcb.gov.br` responde **NXDOMAIN** no DNS público — confirmado em dois
resolvedores independentes (Google e Cloudflare retornam `Status: 3`); não é
bloqueio da rede local. O portal da FGV (`portalibre.fgv.br`) rejeita TLS e
o IPEAData não responde.

O que funciona é o IPCA (IBGE, agregado 1737) — já usado em `indices.py`. E
ele serve, por um motivo empírico: o R$/m² mediano por ano, corrigido pelo
IPCA, vai de 1,000 (2006) a 2,285 (2014) e recua a 1,888 (2026). O IPCA já
embute o ciclo imobiliário — o imóvel subiu MAIS que a inflação geral até
2014 e MENOS depois.

Uso:
    python modelo_casa.py --ajustar          # ajusta os modelos por região
    python modelo_casa.py --serie            # evolução ano a ano
    python modelo_casa.py --validar          # erro médio em casos reais
    python modelo_casa.py --testar 05133004 120 250
"""

from __future__ import annotations

import argparse
import re
import sqlite3
import sys
import time

import config

try:
    sys.stdout.reconfigure(errors="replace")
    sys.stderr.reconfigure(errors="replace")
except Exception:  # noqa: BLE001
    pass

# Filtros de plausibilidade
AREA_TERRENO_MIN = 40.0
AREA_TERRENO_MAX = 2000.0
AREA_CONSTRUIDA_MIN = 20.0
AREA_CONSTRUIDA_MAX = 500.0

# "Sem construção" na prática: casa com até isso de construção é terreno com
# benfeitoria residual. Acima disso a construção domina o preço.
#
# ATENÇÃO: `area_construida = 0` NÃO EXISTE no ITBI (0 de 537 mil linhas) —
# lote vazio puro não gera ITBI de compra e venda. Então este filtro não
# isola "lote vazio": pega também casas de vila e unidades de conjunto
# habitacional ('CS 1', 'FDS', 'CONJ ...' no complemento). Ver
# `_e_terreno()`.
CONSTRUCAO_RESIDUAL = 40.0

# Ano a partir do qual as transações são consideradas "valor corrente".
# Medido: incluindo 2006-2017 o R$/m² do terreno sai 1.321; só de 2018 para
# cá sai 1.611, e 1.805 excluindo também os imóveis com complemento. Valores
# antigos, mesmo corrigidos pelo IPCA, ficam fora da faixa de mercado atual.
ANO_MINIMO = 2018

# Elasticidade do R$/m² em relação ao tamanho do lote.
#
# MEDIDO (n=2.881, terreno quase puro desde 2018):
#     log(R$/m²) = 10.648 - 0.662 * log(area)   (R² = 0.161)
#
# O expoente NEGATIVO diz que o m² fica mais barato em lote grande — é o
# comportamento esperado (lote de 60 m² vale R$ 3.960/m²; de 500 m², R$ 771/m²)
# e faz o valor TOTAL subir com o tamanho, que é o correto.
#
# Por que importa: sem isto o modelo usava UMA média para todo tamanho de
# lote, e como o lote mediano dos anúncios (~190 m²) é grande, o valor saía
# subestimado. Foi o usuário quem percebeu ("1.300 é baixo, pedem 2.000-3.000").
ELASTICIDADE_AREA = -0.662

# A elasticidade é estimada dos dados, não fixada: fica aqui como semente e
# é recalculada por `estimar_elasticidade()`, porque é um parâmetro
# mensurável e re-estimá-lo mantém o modelo honesto se a base mudar.
MIN_AMOSTRA_ELASTICIDADE = 300

# Mínimo de transações para estimar o R$/m² de uma região.
# 12 é alto para um CEP completo (só ~100 CEPs da cidade têm esse volume de
# terreno puro). Por isso as tabelas são construídas em VÁRIAS granularidades
# de CEP e a estimativa cai para a mais fina que tiver dado — ver
# `_regioes()` e `valor_estimado()`.
MIN_POR_REGIAO = 12
# O mesmo mínimo, mais brando, para os níveis mais grossos (CEP-4, CEP-3):
# quanto maior a área coberta, mais aceitável um ajuste com menos pontos.
MIN_POR_REGIAO_GROSSO = 8
# Granularidades, da mais fina para a mais grossa
NIVEIS_CEP = (5, 4, 3)
# Mínimo para entrar na série anual
MIN_POR_ANO = 100


def _regioes(cep) -> list[str]:
    """Chaves de região do mais específico ao mais genérico.

    CEP-5 (bairro), CEP-4, CEP-3 (região) e por fim 'cidade'. Assim uma
    rua/CEP sem transações suficientes ainda herda o preço da vizinhança,
    em vez de cair direto na média da cidade inteira.
    """
    d = _cep5(cep)  # já normalizado com 8 dígitos e cortado em 5
    chaves = [f"cep{n}:{d[:n]}" for n in NIVEIS_CEP if len(d) >= n]
    chaves.append("cidade")
    return chaves


def _cep5(valor) -> str:
    """Prefixo de CEP (5 dígitos) — a 'região' usada como localização.

    CUIDADO, armadilha real: o ITBI guarda o CEP como número, com 7 dígitos e
    SEM o zero à esquerda ('5128000'); o anúncio guarda com 8 ('05128000').
    Um `substr(cep,1,5)` cru devolveria '51280' de um lado e '05128' do outro
    — e NENHUMA região casaria. Aconteceu: todos os CEPs dos nossos anúncios
    saíram "sem dado". O `normalizar_cep` põe o zero de volta antes de cortar.
    """
    import endereco

    if not valor:
        return ""
    d = endereco.normalizar_cep(valor) or re.sub(r"\D", "", str(valor))
    return d[:5] if len(d) >= 5 else ""


def _mediana(v: list[float]) -> float:
    v = sorted(v)
    n = len(v)
    if not n:
        return 0.0
    return v[n // 2] if n % 2 else (v[n // 2 - 1] + v[n // 2]) / 2


def _e_terreno(compl) -> bool:
    """Heurística: o registro é TERRENO, não unidade em vila/conjunto?

    Não existe coluna confiável para isso no ITBI, então uso o COMPLEMENTO:
    quando ele traz 'A', 'B', 'CS 1'..'CS 7', 'FDS', 'CONJ ...', o imóvel é
    uma unidade dentro de um conjunto — o preço inclui a construção vizinha e
    não serve como preço de terreno.

    Efeito medido: sem o filtro a mediana dá R$ 1.321/m²; excluindo esses
    casos, R$ 1.509/m² (e R$ 1.805 com ANO_MINIMO).
    """
    return not str(compl or "").strip()


def estimar_elasticidade(conn: sqlite3.Connection) -> float:
    """Estima do dado como o R$/m² varia com o tamanho do lote.

    Ajusta `log(R$/m²) = k + b*log(area)` por mínimos quadrados. Devolve `b`.
    Medido: b = -0,662 (R² 0,16 — a localização explica mais que o tamanho,
    mas a relação de tamanho é clara).
    """
    import numpy as np

    linhas = conn.execute(
        """SELECT area_terreno, valor_transacao_corrigido/area_terreno
           FROM itbi
           WHERE natureza LIKE '1.%' AND proporcao >= 99.9
             AND COALESCE(area_construida,0) <= ?
             AND area_terreno BETWEEN ? AND ?
             AND ano_arquivo >= ?
             AND COALESCE(valor_transacao_corrigido,0) > 0""",
        (CONSTRUCAO_RESIDUAL, AREA_TERRENO_MIN, AREA_TERRENO_MAX, ANO_MINIMO),
    ).fetchall()

    t, m2 = [], []
    for a, v in linhas:
        if a and a > 0 and v and 50 < v < 60000:
            t.append(a)
            m2.append(v)
    if len(t) < MIN_AMOSTRA_ELASTICIDADE:
        return ELASTICIDADE_AREA

    t = np.array(t, float)
    m2 = np.array(m2, float)
    A = np.column_stack([np.log(t), np.ones_like(t)])
    coef, *_ = np.linalg.lstsq(A, np.log(m2), rcond=None)
    return float(coef[0])


def _quantil(v: list[float], q: float) -> float:
    v = sorted(v)
    if not v:
        return 0.0
    return v[min(int(q * len(v)), len(v) - 1)]


def criar_tabelas(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS preco_terreno (
            regiao        TEXT PRIMARY KEY,
            n             INTEGER,
            m2_mediana    REAL,
            p25           REAL,
            p75           REAL,
            area_mediana  REAL,
            ajustado_em   TEXT
        );
        CREATE TABLE IF NOT EXISTS preco_construcao (
            regiao        TEXT PRIMARY KEY,
            n             INTEGER,
            m2_mediana    REAL,
            m2_p25        REAL,
            m2_p75        REAL,
            ajustado_em   TEXT
        );
        CREATE TABLE IF NOT EXISTS depreciacao (
            ano                 INTEGER PRIMARY KEY,
            n                   INTEGER,
            m2_construcao       REAL,
            m2_terreno          REAL,
            indice_construcao   REAL
        );
        -- parâmetros medidos (elasticidade do terreno, etc.)
        CREATE TABLE IF NOT EXISTS modelo_param (
            chave   TEXT PRIMARY KEY,
            valor   REAL,
            n       INTEGER,
            nota    TEXT
        );
        """
    )
    conn.commit()
    # migração: bancos antigos não têm `area_mediana`
    cols = {r[1] for r in conn.execute("PRAGMA table_info(preco_terreno)")}
    if "area_mediana" not in cols:
        conn.execute("ALTER TABLE preco_terreno ADD COLUMN area_mediana REAL")
        conn.commit()


def _param(conn: sqlite3.Connection, chave: str, padrao: float) -> float:
    """Lê um parâmetro medido da tabela, com valor padrão."""
    try:
        r = conn.execute("SELECT valor FROM modelo_param WHERE chave = ?",
                         (chave,)).fetchone()
        return r[0] if r and r[0] is not None else padrao
    except sqlite3.Error:
        return padrao


# ---------------------------------------------------------------------------
# 1) R$/m² do TERRENO — de imóveis praticamente sem construção
# ---------------------------------------------------------------------------
def ajustar_terreno(conn: sqlite3.Connection, verbose: bool = True) -> int:
    """Estima o R$/m² do terreno por região, de imóveis sem construção.

    Por que destes imóveis: sem construção, o preço pago É o preço do
    terreno. Qualquer outro grupo teria a construção embutida e contaminaria
    o número.
    """
    linhas = conn.execute(
        """SELECT valor_transacao_corrigido / area_terreno AS m2,
                  area_terreno, cep AS regiao, complemento
           FROM itbi
           WHERE natureza LIKE '1.%'
             AND proporcao >= 99.9
             AND COALESCE(area_construida,0) <= ?
             AND area_terreno BETWEEN ? AND ?
             AND ano_arquivo >= ?
             AND COALESCE(valor_transacao_corrigido,0) > 0
             AND COALESCE(cep,'') <> ''""",
        (CONSTRUCAO_RESIDUAL, AREA_TERRENO_MIN, AREA_TERRENO_MAX, ANO_MINIMO),
    ).fetchall()

    por_regiao: dict[str, list[tuple[float, float]]] = {}
    todos: list[tuple[float, float]] = []
    for m2, area, regiao, compl in linhas:
        if not m2 or m2 <= 0 or m2 < 50 or m2 > 60000 or not area:
            continue
        # descarta unidades em vila/conjunto ('CS 1', 'FDS', ...): o preço
        # delas inclui a construção vizinha e não serve como preço de terreno
        if not _e_terreno(compl):
            continue
        todos.append((area, m2))
        # grava o mesmo par em TODAS as granularidades de CEP
        d = _cep5(regiao)
        for n in NIVEIS_CEP:
            if len(d) >= n:
                por_regiao.setdefault(f"cep{n}:{d[:n]}", []).append((area, m2))

    conn.execute("DELETE FROM preco_terreno")
    gravadas = 0
    for regiao, pares in por_regiao.items():
        # granularidade grossa aceita amostra menor (cobre mais área)
        n_nivel = int(regiao.split(":")[0].replace("cep", ""))
        minimo = MIN_POR_REGIAO if n_nivel == 5 else MIN_POR_REGIAO_GROSSO
        if len(pares) < minimo:
            continue
        areas = [a for a, _ in pares]
        vals = [v for _, v in pares]
        conn.execute(
            """INSERT OR REPLACE INTO preco_terreno
               (regiao, n, m2_mediana, p25, p75, area_mediana, ajustado_em)
               VALUES (?,?,?,?,?,?,datetime('now'))""",
            (regiao, len(pares), _mediana(vals),
             _quantil(vals, 0.25), _quantil(vals, 0.75), _mediana(areas)),
        )
        gravadas += 1

    if todos:
        areas = [a for a, _ in todos]
        vals = [v for _, v in todos]
        conn.execute(
            """INSERT OR REPLACE INTO preco_terreno
               (regiao, n, m2_mediana, p25, p75, area_mediana, ajustado_em)
               VALUES ('cidade',?,?,?,?,?,datetime('now'))""",
            (len(todos), _mediana(vals), _quantil(vals, .25),
             _quantil(vals, .75), _mediana(areas)),
        )
        gravadas += 1
    conn.commit()

    if verbose:
        print(f"terreno: {gravadas} regiões ({len(todos):,d} transações "
              f"quase sem construção)")
        if todos:
            vals = [v for _, v in todos]
            areas = [a for a, _ in todos]
            print(f"  mediana da cidade: R$ {_mediana(vals):,.0f}/m² "
                  f"(em lote de {_mediana(areas):,.0f} m²)")
    return gravadas


# ---------------------------------------------------------------------------
# 2) R$/m² da CONSTRUÇÃO — do resíduo, dentro da mesma região
# ---------------------------------------------------------------------------
def ajustar_construcao(conn: sqlite3.Connection, verbose: bool = True) -> int:
    """Estima o R$/m² da construção por região.

    Método: dentro de uma região, `valor ~ terreno*p_terreno + constr*p_constr`.
    Como o preço do terreno da região já é conhecido, o resíduo
    (`valor - terreno*p_terreno`) é atribuído à construção.

    O preço do terreno é AJUSTADO ao tamanho do lote pela elasticidade medida
    — sem isso o resíduo fica errado justamente nos lotes grandes, e consumo
    o valor da construção para compensar.
    """
    el = _param(conn, "elasticidade_area", ELASTICIDADE_AREA)
    # regiao -> (R$/m² na área mediana, área mediana da amostra)
    terreno_reg: dict[str, tuple[float, float]] = {
        r[0]: (r[1], r[2] or 0.0) for r in conn.execute(
            """SELECT regiao, m2_mediana, area_mediana FROM preco_terreno
               WHERE regiao LIKE 'cep%'""")}
    geral = conn.execute(
        "SELECT m2_mediana, area_mediana FROM preco_terreno WHERE regiao='cidade'"
    ).fetchone()
    p_terreno_geral = (geral[0], geral[1] or 0.0) if geral else (0.0, 0.0)

    def _p_terreno(cep, area_terreno) -> float:
        """R$/m² do terreno da região, corrigido para ESTE tamanho de lote."""
        p, area_ref = p_terreno_geral
        for k in _regioes(cep):
            if k in terreno_reg:
                p, area_ref = terreno_reg[k]
                break
        if not p:
            return 0.0
        if area_ref and area_ref > 0 and area_terreno > 0:
            return p * (area_terreno / area_ref) ** el
        return p

    linhas = conn.execute(
        """SELECT area_terreno, area_construida, valor_transacao_corrigido,
                  cep AS regiao
           FROM itbi
           WHERE natureza LIKE '1.%'
             AND proporcao >= 99.9
             AND area_terreno BETWEEN ? AND ?
             AND area_construida BETWEEN ? AND ?
             AND ano_arquivo >= ?
             AND COALESCE(valor_transacao_corrigido,0) > 0
             AND COALESCE(cep,'') <> ''""",
        (AREA_TERRENO_MIN, AREA_TERRENO_MAX,
         AREA_CONSTRUIDA_MIN, AREA_CONSTRUIDA_MAX, ANO_MINIMO),
    ).fetchall()

    por_regiao: dict[str, list[float]] = {}
    todos: list[float] = []
    for terr, constr, valor, regiao in linhas:
        p_t = _p_terreno(regiao, terr)
        if not p_t:
            continue
        residuo = valor - terr * p_t
        if residuo <= 0:
            continue
        m2_c = residuo / constr
        if m2_c < 100 or m2_c > 40000:
            continue
        todos.append(m2_c)
        d = _cep5(regiao)
        for n in NIVEIS_CEP:
            if len(d) >= n:
                por_regiao.setdefault(f"cep{n}:{d[:n]}", []).append(m2_c)

    conn.execute("DELETE FROM preco_construcao")
    gravadas = 0
    for regiao, vals in por_regiao.items():
        n_nivel = int(regiao.split(":")[0].replace("cep", ""))
        minimo = MIN_POR_REGIAO if n_nivel == 5 else MIN_POR_REGIAO_GROSSO
        if len(vals) < minimo:
            continue
        conn.execute(
            """INSERT OR REPLACE INTO preco_construcao
               (regiao, n, m2_mediana, m2_p25, m2_p75, ajustado_em)
               VALUES (?,?,?,?,?,datetime('now'))""",
            (regiao, len(vals), _mediana(vals),
             _quantil(vals, .25), _quantil(vals, .75)),
        )
        gravadas += 1

    if todos:
        conn.execute(
            """INSERT OR REPLACE INTO preco_construcao
               (regiao, n, m2_mediana, m2_p25, m2_p75, ajustado_em)
               VALUES ('cidade',?,?,?,?,datetime('now'))""",
            (len(todos), _mediana(todos), _quantil(todos, .25),
             _quantil(todos, .75)),
        )
        gravadas += 1
    conn.commit()

    if verbose:
        print(f"construção: {gravadas} regiões ({len(todos):,d} transações)")
        if todos:
            print(f"  mediana da cidade: R$ {_mediana(todos):,.0f}/m²")
    return gravadas


# ---------------------------------------------------------------------------
# 3) Depreciação — a série anual MEDE o que aconteceu
# ---------------------------------------------------------------------------
def ajustar_depreciacao(conn: sqlite3.Connection, verbose: bool = True) -> None:
    """Série anual do R$/m² da construção, para medir a depreciação.

    O ITBI não traz idade do imóvel, então não dá para estimar uma taxa por
    idade. O que a série dá é o efeito AGREGADO ano a ano — e é isso que
    permite afirmar (ou não) que a construção perde valor em termos reais.
    """
    conn.execute("DELETE FROM depreciacao")
    anos = [r[0] for r in conn.execute(
        "SELECT DISTINCT ano_arquivo FROM itbi WHERE ano_arquivo >= 2006 "
        "ORDER BY ano_arquivo")]

    base = None
    n_gravados = 0
    for a in anos:
        tv = [r[0] for r in conn.execute(
            """SELECT valor_transacao_corrigido/area_terreno FROM itbi
               WHERE natureza LIKE '1.%' AND proporcao>=99.9 AND ano_arquivo=?
                 AND COALESCE(area_construida,0) <= ?
                 AND area_terreno BETWEEN ? AND ?
                 AND COALESCE(valor_transacao_corrigido,0)>0""",
            (a, CONSTRUCAO_RESIDUAL, AREA_TERRENO_MIN, AREA_TERRENO_MAX))]
        tv = [x for x in tv if x and 50 < x < 60000]
        if len(tv) < MIN_POR_ANO // 4:
            continue
        p_t = _mediana(tv)

        cv = []
        for terr, constr, valor in conn.execute(
            """SELECT area_terreno, area_construida, valor_transacao_corrigido
               FROM itbi WHERE natureza LIKE '1.%' AND proporcao>=99.9
                 AND ano_arquivo=? AND area_terreno BETWEEN ? AND ?
                 AND area_construida BETWEEN ? AND ?
                 AND COALESCE(valor_transacao_corrigido,0)>0""",
            (a, AREA_TERRENO_MIN, AREA_TERRENO_MAX,
             AREA_CONSTRUIDA_MIN, AREA_CONSTRUIDA_MAX)):
            res = valor - terr * p_t
            if res > 0:
                m2 = res / constr
                if 100 < m2 < 40000:
                    cv.append(m2)
        if len(cv) < MIN_POR_ANO:
            continue

        p_c = _mediana(cv)
        if base is None:
            base = p_c
        conn.execute(
            """INSERT OR REPLACE INTO depreciacao
               (ano, n, m2_construcao, m2_terreno, indice_construcao)
               VALUES (?,?,?,?,?)""",
            (a, len(cv), p_c, p_t, p_c / base if base else 1.0),
        )
        n_gravados += 1
    conn.commit()

    if verbose:
        print(f"depreciação: {n_gravados} anos")
        print(f"\n{'ano':>5} {'n':>7} {'R$/m2 constr':>13} {'R$/m2 terreno':>14} "
              f"{'indice':>8}")
        print("-" * 52)
        for r in conn.execute(
            """SELECT ano, n, m2_construcao, m2_terreno, indice_construcao
               FROM depreciacao ORDER BY ano"""):
            print(f"{r[0]:>5} {r[1]:>7,d} {r[2]:>13,.0f} {r[3]:>14,.0f} "
                  f"{r[4]:>8.3f}")


# ---------------------------------------------------------------------------
# 4) Aplicar
# ---------------------------------------------------------------------------
def valor_estimado(conn: sqlite3.Connection, cep: str,
                   area_terreno: float, area_construida: float) -> dict | None:
    """Aplica o modelo: terreno.*preco_terreno + constr.*preco_constr.

    Desce a cascata CEP-5 -> CEP-4 -> CEP-3 -> cidade, usando o nível mais
    específico que tiver dado. Sem isso, um CEP sem transações próprias cairia
    direto na média da cidade e perderia a informação de localização — que é
    justamente o que dá valor ao terreno.

    O preço do terreno é então AJUSTADO ao tamanho do lote pela elasticidade
    medida (`ELASTICIDADE_AREA`), porque o R$/m² cai com o tamanho.
    """
    regioes = _regioes(cep)
    el = _param(conn, "elasticidade_area", ELASTICIDADE_AREA)

    lin = None
    for k in regioes:
        lin = conn.execute("SELECT * FROM preco_terreno WHERE regiao=?",
                           (k,)).fetchone()
        if lin:
            break
    if not lin:
        return None

    lin_c = None
    for k in regioes:
        lin_c = conn.execute("SELECT * FROM preco_construcao WHERE regiao=?",
                             (k,)).fetchone()
        if lin_c:
            break
    if not lin_c:
        return None

    p_t, p_c = lin["m2_mediana"], lin_c["m2_mediana"]

    # O R$/m² do terreno cai conforme o lote cresce: R$/m² = base * area^b,
    # com b negativo (medido: -0,662). Sem este ajuste, um lote de 400 m² e um
    # de 100 m² receberiam o mesmo preço por m² — e o valor do grande ficaria
    # 4x o do pequeno, o que os dados não mostram.
    area_ref = lin["area_mediana"] if "area_mediana" in lin.keys() else None
    if area_ref and area_ref > 0 and area_terreno > 0:
        p_t = p_t * (area_terreno / area_ref) ** el

    v_t, v_c = area_terreno * p_t, area_construida * p_c
    total = v_t + v_c
    return {
        "regiao": lin["regiao"],
        "regiao_construcao": lin_c["regiao"],
        "n_terreno": lin["n"],
        "n_construcao": lin_c["n"],
        "area_mediana_regiao": area_ref,
        "elasticidade": el,
        "preco_m2_terreno": p_t,
        "preco_m2_terreno_medio": lin["m2_mediana"],
        "preco_m2_construcao": p_c,
        "valor_terreno": v_t,
        "valor_construcao": v_c,
        "valor": total,
        "pct_terreno": 100 * v_t / total if total else 0,
        "pct_construcao": 100 * v_c / total if total else 0,
    }


def validar(conn: sqlite3.Connection, ano: int = 2022) -> None:
    """Mede o erro do modelo em transações reais.

    Sem isto não há como dizer se o valor estimado serve para alguma coisa.
    """
    linhas = conn.execute(
        """SELECT area_terreno, area_construida, valor_transacao_corrigido, cep
           FROM itbi
           WHERE natureza LIKE '1.%' AND proporcao>=99.9 AND ano_arquivo=?
             AND area_terreno BETWEEN ? AND ?
             AND area_construida BETWEEN ? AND ?
             AND COALESCE(valor_transacao_corrigido,0)>0
             AND COALESCE(cep,'')<>'' LIMIT 4000""",
        (ano, AREA_TERRENO_MIN, AREA_TERRENO_MAX,
         AREA_CONSTRUIDA_MIN, AREA_CONSTRUIDA_MAX),
    ).fetchall()

    erros = []
    for terr, constr, real, cep in linhas:
        est = valor_estimado(conn, cep, terr, constr)
        if est and est["valor"] and real:
            erros.append(est["valor"] / real)

    if not erros:
        print("Sem dados para validar.")
        return
    erros.sort()
    n = len(erros)
    print(f"=== validação em {n:,d} transações de {ano} ===")
    print(f"  razão estimado/real: mediana {erros[n//2]:.2f} | "
          f"p25 {erros[int(.25*n)]:.2f} | p75 {erros[int(.75*n)]:.2f}")
    print(f"  dentro de +-20%: {100*sum(1 for x in erros if .8<=x<=1.2)/n:.0f}%")
    print(f"  dentro de +-30%: {100*sum(1 for x in erros if .7<=x<=1.3)/n:.0f}%")
    print("\n  Mediana perto de 1,00 = modelo sem viés. O espalhamento")
    print("  (p25-p75) é o que limita o uso: serve para dizer 'esta faixa',")
    print("  não 'este valor'.")


def main() -> int:
    p = argparse.ArgumentParser(description="Modelo terreno+construção (casa)")
    p.add_argument("--ajustar", action="store_true", help="ajusta tudo")
    p.add_argument("--serie", action="store_true", help="só a série anual")
    p.add_argument("--validar", action="store_true", help="erro em casos reais")
    p.add_argument("--testar", nargs=3, metavar=("CEP", "TERRENO", "CONSTR"))
    args = p.parse_args()

    conn = sqlite3.connect(config.DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=300000")
    criar_tabelas(conn)
    t0 = time.time()

    if args.serie:
        ajustar_depreciacao(conn)
    elif args.ajustar:
        # a elasticidade é MEDIDA, não fixada — se a base mudar, o parâmetro
        # se reajusta sozinho em vez de ficar desatualizado no código
        el = estimar_elasticidade(conn)
        conn.execute(
            """INSERT OR REPLACE INTO modelo_param (chave, valor, n, nota)
               VALUES ('elasticidade_area',?,?, 'R$/m2 do terreno cai com o tamanho do lote')""",
            (el, 0))
        conn.commit()
        print(f"elasticidade medida: {el:+.3f} "
              f"(R$/m² do terreno cai com o tamanho do lote)")
        ajustar_terreno(conn)
        ajustar_construcao(conn)
        ajustar_depreciacao(conn)
        print(f"\ntempo: {time.time()-t0:.0f}s")
    elif args.validar:
        validar(conn)
    elif args.testar:
        cep, terr, constr = args.testar
        r = valor_estimado(conn, cep, float(terr), float(constr))
        if not r:
            print("Nenhum modelo. Rode --ajustar primeiro.")
        else:
            print(f"região {r['regiao']} (terreno n={r['n_terreno']:,d}, "
                  f"construção n={r['n_construcao']:,d})")
            print(f"  R$/m² terreno    : {r['preco_m2_terreno']:>10,.0f}")
            print(f"  R$/m² construção : {r['preco_m2_construcao']:>10,.0f}")
            print(f"  terreno    {float(terr):>6,.0f} m² -> "
                  f"R$ {r['valor_terreno']:>12,.0f}  ({r['pct_terreno']:.0f}%)")
            print(f"  construção {float(constr):>6,.0f} m² -> "
                  f"R$ {r['valor_construcao']:>12,.0f}  ({r['pct_construcao']:.0f}%)")
            print(f"  {'VALOR ESTIMADO':<19}     R$ {r['valor']:>12,.0f}")
    else:
        print(__doc__)
    conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
