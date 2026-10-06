"""Investiga o vies de +8% do preco via ITBI.

A mediana previsto/real ficou em 1.083. Hipoteses a medir:

  1. Depreciacao: a construcao envelhece. Uma venda de 2024 numa rua cujas
     vendas de comparacao sao de 2018-2023 usa casas que eram MAIS NOVAS
     entao. O IPCA corrige a inflacao, mas nao o envelhecimento.

  2. Financiamento: o metodo prioriza financiadas, que sao avaliadas pelo
     banco e tendem a ficar ACIMA (medido antes: financiadas 5.927 vs
     4.407 diretas, +26%). Se o teste tem mais diretas, o previsto fica alto.

  3. R$/m² medio cai com a area (elasticidade). Se o metodo usa a mesma
     faixa de area, nao deveria viesar — mas se as casas do teste forem
     maiores que as do treino, o m² real e menor e o previsto fica alto.

Uso: python medir_causa_vies.py
"""

from __future__ import annotations

import sqlite3
import statistics
import sys
from collections import defaultdict

sys.path.insert(0, "src")
from cacaimoveis import config
from cacaimoveis import endereco

try:
    sys.stdout.reconfigure(errors="replace")
except Exception:  # noqa: BLE001
    pass

conn = sqlite3.connect(config.DB_PATH)
conn.row_factory = sqlite3.Row

# por ANO de venda: o R$/m² corrigido muda com o tempo?
print("=" * 70)
print("HIPOTESE 1: o R$/m² corrigido cai com o tempo (depreciacao)?")
print("=" * 70)
for ano in range(2018, 2026):
    r = conn.execute(
        """SELECT COUNT(*) n,
                  ROUND(SUM(valor_transacao_corrigido) / SUM(area_construida)) m2
           FROM itbi
           WHERE uso='10' AND natureza LIKE '1.%' AND proporcao >= 99.9
             AND area_construida BETWEEN 20 AND 500
             AND COALESCE(valor_transacao_corrigido,0) > 10000
             AND ano_arquivo=?""",
        (ano,),
    ).fetchone()
    if r["n"]:
        print(f"  {ano}: {r['n']:>7,} vendas | R$ {r['m2']:>7,.0f}/m² médio")

print()
print("  Se o m² médio CAI de 2018 para 2024, o treino (2018-2023) prevê")
print("  com casas mais valiosas que as do teste (2024+), o que explicaria")
print("  o viés de +8%.")

print()
print("=" * 70)
print("HIPOTESE 2: financiadas são mais caras?")
print("=" * 70)
for rotulo, cond in [("financiadas", "financiamento IS NOT NULL AND financiamento<>''"),
                     ("diretas", "(financiamento IS NULL OR financiamento='')")]:
    r = conn.execute(
        f"""SELECT COUNT(*) n,
                   ROUND(SUM(valor_transacao_corrigido) / SUM(area_construida)) m2
            FROM itbi WHERE uso='10' AND natureza LIKE '1.%'
              AND proporcao >= 99.9 AND area_construida BETWEEN 20 AND 500
              AND COALESCE(valor_transacao_corrigido,0) > 10000
              AND ano_arquivo >= 2020 AND {cond}""",
    ).fetchone()
    print(f"  {rotulo:<12} {r['n']:>7,} vendas | R$ {r['m2']:>7,.0f}/m² médio")

conn.close()
