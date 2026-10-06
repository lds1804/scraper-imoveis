"""Valida o preço via ITBI do jeito que o `comparar_itbi` de fato calcula.

O método real: para um anúncio, pega as transações da MESMA rua (ou CEP) com
área ±25% e tira o R$/m² mediano — resistente a empreendimento (mediana por
número). Depois multiplica pela área do anúncio para dar o "preço de mercado".

Aqui repetimos isso para TRANSAÇÕES (não anúncios): a transação do teste
(2024+) tem rua e área conhecidas. Usamos as transações do treino (2018-2023)
da mesma rua com área ±25% para prever o valor. O erro diz quão perto o
método chega do preço REAL de uma venda.

Uso: python validar_itbi.py
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

QUALIDADE = (
    "uso='10' AND natureza LIKE '1.%' AND proporcao >= 99.9 "
    "AND area_construida BETWEEN 20 AND 500 "
    "AND COALESCE(valor_transacao_corrigido,0) > 10000"
)

# carrega transações com rua e CEP
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
        "ano": r["ano_arquivo"] or 0,
        "m2": m2,
        "vt": r["vt"],
        "ac": r["ac"],
        "rua": r["rua_chave"],
        "cep5": endereco.normalizar_cep(r["cep_norm"] or "")[:5],
    })

treino = [x for x in linhas if 2018 <= x["ano"] <= 2023]
teste = [x for x in linhas if x["ano"] >= 2024]
print(f"transações: {len(linhas):,}")
print(f"  treino (2018-2023): {len(treino):,} | teste (2024+): {len(teste):,}")


def mediana(v):
    s = sorted(v)
    return s[len(s) // 2] if s else None


# índice: rua -> lista de (m2, ac)
por_rua = defaultdict(list)
por_cep = defaultdict(list)
for x in treino:
    por_rua[x["rua"]].append(x)
    por_cep[x["cep5"]].append(x)


def preve(x):
    """Repete a cascata: rua com área ±25%, depois cep. Devolve R$/m²."""
    lo, hi = x["ac"] * 0.75, x["ac"] * 1.25
    cands = [c for c in por_rua.get(x["rua"], []) if lo <= c["ac"] <= hi]
    if len(cands) >= 3:
        return mediana([c["m2"] for c in cands]), "rua", len(cands)
    cands = [c for c in por_cep.get(x["cep5"], []) if lo <= c["ac"] <= hi]
    if len(cands) >= 3:
        return mediana([c["m2"] for c in cands]), "cep", len(cands)
    return None, "", 0


erros = []
fontes = defaultdict(int)
sem_base = 0
for x in teste:
    p, fonte, n = preve(x)
    if p is None:
        sem_base += 1
        continue
    erros.append(abs(x["m2"] - p) / x["m2"])
    fontes[fonte] += 1

erros.sort()
print()
print(f"  transações do teste com previsão: {len(erros):,} "
      f"({100*len(erros)/len(teste):.0f}%)")
print(f"  sem base (menos de 3 comparáveis): {sem_base:,}")
print(f"  fonte usada: {dict(fontes)}")
print()
print(f"  ERRO mediano do R$/m² previsto: {100*statistics.median(erros):.1f}%")
print(f"  p75 ......................... {100*erros[int(.75*len(erros))]:.1f}%")
print(f"  p25 ......................... {100*erros[int(.25*len(erros))]:.1f}%")
print()
print("  Leitura: este é o MÉTODO REAL do comparar_itbi aplicado a vendas")
print("  futuras. Erro mediano ~28% significa que o preço de mercado é uma")
print("  FAIXA, não um ponto. Serve para ordenar (acima/abaixo), não para")
print("  cravar o valor de revenda.")

conn.close()
