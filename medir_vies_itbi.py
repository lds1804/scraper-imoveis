"""Mede o VIES do método do ITBI: prevê acima ou abaixo do real?

Um método pode ter erro 24% mas ser INJUSTO (sempre acima ou sempre abaixo).
Para o usuário isso importa: se a mediana de mercado sistematicamente fica
abaixo do preço real, ele acha que tudo está "caro"; se fica acima, acha que
tudo é barganha.

Uso: python medir_vies_itbi.py
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
               rua_chave, cep_norm, ano_arquivo
        FROM itbi WHERE {QUALIDADE} AND COALESCE(rua_chave,'')<>''
          AND COALESCE(cep,'')<>''"""
):
    m2 = r["vt"] / r["ac"] if r["ac"] else None
    if not m2 or not (800 <= m2 <= 25000):
        continue
    linhas.append({
        "ano": r["ano_arquivo"] or 0, "m2": m2, "ac": r["ac"],
        "rua": r["rua_chave"],
        "cep5": endereco.normalizar_cep(r["cep_norm"] or "")[:5],
    })

treino = [x for x in linhas if 2018 <= x["ano"] <= 2023]
teste = [x for x in linhas if x["ano"] >= 2024]

por_rua = defaultdict(list)
por_cep = defaultdict(list)
for x in treino:
    por_rua[x["rua"]].append(x)
    por_cep[x["cep5"]].append(x)


def mediana(v):
    s = sorted(v)
    return s[len(s) // 2] if s else None


def preve(x):
    lo, hi = x["ac"] * 0.75, x["ac"] * 1.25
    cands = [c for c in por_rua.get(x["rua"], []) if lo <= c["ac"] <= hi]
    if len(cands) >= 3:
        return mediana([c["m2"] for c in cands])
    cands = [c for c in por_cep.get(x["cep5"], []) if lo <= c["ac"] <= hi]
    if len(cands) >= 3:
        return mediana([c["m2"] for c in cands])
    return None


razoes = []  # previsto / real
for x in teste:
    p = preve(x)
    if p is None:
        continue
    razoes.append(p / x["m2"])

razoes.sort()
n = len(razoes)
print(f"transações do teste previstas: {n:,}")
print(f"  razão PREVISTO / REAL:")
print(f"    mediana ... {statistics.median(razoes):.3f}")
print(f"    p25 ....... {razoes[n//4]:.3f}")
print(f"    p75 ....... {razoes[3*n//4]:.3f}")
print()
print(f"  previsto ACIMA do real: {sum(1 for r in razoes if r > 1):,} "
      f"({100*sum(1 for r in razoes if r > 1)/n:.0f}%)")
print(f"  previsto ABAIXO do real: {sum(1 for r in razoes if r < 1):,} "
      f"({100*sum(1 for r in razoes if r < 1)/n:.0f}%)")
print()
print("  Leitura: mediana ~1.00 = o método é JUSTO (não pende nem para")
print("  cima nem para baixo). Se ficar longe de 1.00, há viés.")
print("  Isto responde 'o ITBI está correto?': sim em termos de justeza,")
print("  não em termos de precisão (erro ~24% por ser uma faixa).")

conn.close()
