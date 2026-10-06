"""Mede o impacto do bug de acumulacao em `cep_da_rua`.

O SQL em `valor_venal.ajustar_ruas()` agrupa por (rua_chave, cep_norm) com o
CEP de 8 digitos. Mas a atribuicao `[c[:5]] = r["n"]` SOBRESCREVE em vez de
somar: quando uma rua tem varios CEPs de 8 digitos com o mesmo prefixo de 5,
fica so o ULTIMO contador em vez do total.

Isso importa porque `n` e o total usado para decidir se a rua e confiavel
(MIN_CASOS_POR_RUA).

Uso: python medir_bug_cep.py
"""

from __future__ import annotations

import sqlite3
import sys
from collections import Counter

sys.path.insert(0, "src")
from cacaimoveis import config
from cacaimoveis import endereco

try:
    sys.stdout.reconfigure(errors="replace")
except Exception:  # noqa: BLE001
    pass

conn = sqlite3.connect(config.DB_PATH)
conn.row_factory = sqlite3.Row

# jeito ERRADO (o do codigo): sobrescreve
errado: dict[str, dict[str, int]] = {}
# jeito CERTO: soma
certo: dict[str, Counter] = {}

for r in conn.execute(
    """SELECT rua_chave AS k, cep_norm AS c, COUNT(*) AS n
       FROM itbi
       WHERE COALESCE(rua_chave,'') <> '' AND COALESCE(cep_norm,'') <> ''
       GROUP BY 1, 2"""
):
    c = endereco.normalizar_cep(r["c"])
    if len(c) < 5:
        continue
    errado.setdefault(r["k"], {})[c[:5]] = r["n"]
    certo.setdefault(r["k"], Counter())[c[:5]] += r["n"]

print("=" * 70)
print("BUG DA ACUMULACAO EM cep_da_rua")
print("=" * 70)
print(f"  ruas avaliadas: {len(errado):,}")

diferenca = 0
exemplos = []
# quantas ruas representam o TOTAL de outra rua por causa do bug?
total_errado_menor = 0
for chave in errado:
    cep_e, n_e = max(errado[chave].items(), key=lambda x: x[1])
    cep_c, n_c = certo[chave].most_common(1)[0]
    if cep_e != cep_c or n_e != n_c:
        diferenca += 1
        if n_e != n_c:
            total_errado_menor += 1
        if len(exemplos) < 8:
            exemplos.append((chave, cep_e, n_e, cep_c, n_c))

print(f"  ruas em que o resultado DIFERE: {diferenca:,}"
      f" ({100*diferenca/max(len(errado),1):.1f}%)")
print(f"  dessas, o total ficou MENOR ..: {total_errado_menor:,}")
print()
print("  O total menor e o que dói: a rua pode cair abaixo de")
print("  MIN_CASOS_POR_RUA e ser descartada, ou o CEP dominante pode")
print("  ser trocado pelo errado.")
print()
print("  exemplos (rua | cep ERRADO n | cep CERTO n):")
for chave, ce, ne, cc, nc in exemplos:
    marca = "  <-- CEP trocado" if ce != cc else "  <-- so o n"
    print(f"    {chave[:30]:<30} {ce} n={ne:<4} | {cc} n={nc:<4}{marca}")

# quantas ruas cruzam o limiar por causa do bug?
MIN = 3
era = sum(1 for k in errado
          if max(errado[k].values()) >= MIN and certo[k].most_common(1)[0][1] >= MIN)
era_antes = sum(1 for k in errado if max(errado[k].values()) >= MIN)
agora = sum(1 for k in certo if certo[k].most_common(1)[0][1] >= MIN)
print()
print(f"  ruas com total >= {MIN} (jeito ERRADO): {era_antes:,}")
print(f"  ruas com total >= {MIN} (jeito CERTO) : {agora:,}")
print(f"  ruas que o bug FAZIA PASSAR sem ter o minimo: {max(0, era_antes - agora):,}")

conn.close()
