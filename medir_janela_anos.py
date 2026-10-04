"""Mede se a JANELA DE ANOS do comparar_itbi importa.

A revisão apontou que a consulta de transações NÃO filtra ano (usa 2006-2026,
só LIMIT 500 ordenado por data), enquanto o modelo de casa usa 2018+. A
hipótese: valores antigos, mesmo corrigidos pelo IPCA, saem da faixa de
mercado atual e pioram a mediana.

Teste honesto: mede o erro do R$/m² por CEP no TESTE (transações de 2024+),
usando treino com janelas diferentes (todo mundo, 2018+, 2020+). Se a janela
menor errar menos, a crítica procede.

Uso: python medir_janela_anos.py
"""

from __future__ import annotations

import sqlite3
import statistics
import sys
from collections import defaultdict

sys.path.insert(0, "src")
import config
import endereco

try:
    sys.stdout.reconfigure(errors="replace")
except Exception:  # noqa: BLE001
    pass

conn = sqlite3.connect(config.DB_PATH)
conn.row_factory = sqlite3.Row

QUALIDADE = (
    "uso='10' AND natureza LIKE '1.%' AND proporcao >= 99.9 "
    "AND area_construida BETWEEN 20 AND 500 "
    "AND COALESCE(valor_transacao_corrigido,0) > 10000"
)

linhas = []
for r in conn.execute(
    f"""SELECT valor_transacao_corrigido vt, area_construida ac,
               cep_norm, ano_arquivo
        FROM itbi WHERE {QUALIDADE} AND COALESCE(cep,'')<>''"""
):
    m2 = r["vt"] / r["ac"] if r["ac"] else None
    if not m2 or not (800 <= m2 <= 25000):
        continue
    d = endereco.normalizar_cep(r["cep_norm"] or "")
    if len(d) < 5:
        continue
    linhas.append({"ano": r["ano_arquivo"] or 0, "m2": m2, "cep5": d[:5]})

teste = [x for x in linhas if x["ano"] >= 2024]
print(f"transações: {len(linhas):,} | teste (2024+): {len(teste):,}")

# quantos anos o treino inclui em cada cenário
cenarios = [
    ("2006-2023 (o que o app usa)", 0),
    ("2018-2023 (como o modelo de casa)", 2018),
    ("2020-2023", 2020),
    ("2022-2023", 2022),
]


def mediana(v):
    s = sorted(v)
    return s[len(s) // 2] if s else None


def erro_do_teste(ano_min):
    treino = [x for x in linhas if 0 < x["ano"] <= 2023 and x["ano"] >= ano_min]
    m_cep = {}
    g = defaultdict(list)
    for x in treino:
        g[x["cep5"]].append(x["m2"])
    for cep5, xs in g.items():
        m_cep[cep5] = mediana(xs)
    m_cidade = mediana([x["m2"] for x in treino])

    erros = []
    usou = 0
    for x in teste:
        m = m_cep.get(x["cep5"])
        if m is None:
            m = m_cidade
        else:
            usou += 1
        erros.append(abs(x["m2"] - m) / x["m2"])
    erros.sort()
    return statistics.median(erros), usou / len(teste), len(treino)


print()
print(f"{'janela do treino':<34} {'transações':>11} {'cob CEP':>7} {'erro mediano':>12}")
print("-" * 68)
for nome, ano_min in cenarios:
    err, cob, n = erro_do_teste(ano_min)
    print(f"{nome:<34} {n:>11,} {100*cob:>6.0f}% {100*err:>11.1f}%")

print()
print("  Leitura: se '2018+' errar MENOS que '2006-2023', a crítica da")
print("  revisão procede e vale filtrar o ano no comparar_itbi.")

conn.close()
