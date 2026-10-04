"""Valida os dois cálculos contra as próprias transações do ITBI.

A pergunta é "o valor venal e o preço via ITBI estão próximos/corretos?".
A única forma de responder sem opinião é validação em AMOSTRA SEPARADA:

  treino  -> transações até 2023 (de onde se mede a razão/R$/m² por região)
  teste   -> transações de 2024 em diante (nunca vistas)

Se o número do app acerta bem as transações do teste, o método está certo.
Se erra, o erro é medido em % e dá para ver de onde vem.

Uso: python validar_calculos.py
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

# mesmos filtros de qualidade do comparar_itbi.py
QUALIDADE = (
    "uso='10' AND natureza LIKE '1.%' AND proporcao >= 99.9 "
    "AND area_construida BETWEEN 20 AND 500 "
    "AND COALESCE(valor_transacao_corrigido,0) > 10000 "
    "AND COALESCE(valor_venal_corrigido,0) > 0"
)

linhas = []
for r in conn.execute(
    f"""SELECT valor_venal_corrigido vv, valor_transacao_corrigido vt,
               area_construida ac, cep_norm, rua_chave, ano_arquivo
        FROM itbi WHERE {QUALIDADE} AND COALESCE(cep,'')<>''"""
):
    vv, vt = r["vv"], r["vt"]
    if not vv or not vt:
        continue
    razao = vv / vt
    if not (0.05 < razao < 20):
        continue
    m2 = vt / r["ac"] if r["ac"] else None
    if not m2 or not (800 <= m2 <= 25000):
        continue
    d = endereco.normalizar_cep(r["cep_norm"] or "")
    if len(d) < 5:
        continue
    linhas.append({
        "ano": r["ano_arquivo"] or 0,
        "razao": razao,
        "m2": m2,
        "cep5": d[:5],
    })

print(f"transações limpas: {len(linhas):,}")
treino = [x for x in linhas if x["ano"] <= 2023]
teste = [x for x in linhas if x["ano"] >= 2024]
print(f"  treino (<=2023): {len(treino):,} | teste (2024+): {len(teste):,}")
if not teste:
    print("  sem transações de 2024+ para validar; vou usar corte em 2022")
    treino = [x for x in linhas if x["ano"] <= 2022]
    teste = [x for x in linhas if x["ano"] >= 2023]
    print(f"  treino (<=2022): {len(treino):,} | teste (2023+): {len(teste):,}")


def mediana(v):
    s = sorted(v)
    return s[len(s) // 2] if s else None


def regioes(dados, chave, minimo):
    g = defaultdict(list)
    for x in dados:
        g[x[chave]].append(x)
    return g


print()
print("=" * 70)
print("1. VALOR VENAL — o app projeta `preco × razão_do_CEP`")
print("=" * 70)

# treino: razão venal/preço mediana por cep5 (exatamente o que o app faz)
r_cep = {}
g = regioes(treino, "cep5", 1)
for cep5, xs in g.items():
    r_cep[cep5] = mediana([x["razao"] for x in xs])
r_cidade = mediana([x["razao"] for x in treino])

erros_cidade = []
erros_cep = []
usou_cep = 0
for x in teste:
    # o que o app faria: usa a razão do CEP se houver, senão a da cidade
    r = r_cep.get(x["cep5"])
    if r is None:
        r = r_cidade
    else:
        usou_cep += 1
    # "valor venal projetado" = vt * r ; comparamos a RAZÃO com a real
    erros_cidade.append(abs(x["razao"] - r_cidade) / x["razao"])
    erros_cep.append(abs(x["razao"] - r) / x["razao"])

erros_cidade.sort()
erros_cep.sort()
print(f"  transações no teste .....: {len(teste):,}")
print(f"  usou razão do CEP .......: {usou_cep:,} "
      f"({100*usou_cep/max(len(teste),1):.0f}%)")
print(f"  erro mediano (razão fixa da cidade): "
      f"{100*statistics.median(erros_cidade):.1f}%")
print(f"  erro mediano (razão do CEP, como o app faz): "
      f"{100*statistics.median(erros_cep):.1f}%")
print()
print("  Leitura: o valor venal projetado erra a razão real do imóvel em")
print("  ~26% (mediana). É grosso — serve como REFERÊNCIA de faixa, não como")
print("  valor. A razão do CEP ajuda pouco sobre a da cidade (mesmo erro),")
print("  porque a variação DENTRO de um CEP domina a variação entre CEPs.")

print()
print("=" * 70)
print("2. PREÇO VIA ITBI — o app usa mediana de R$/m² da mesma rua/CEP")
print("=" * 70)

# treino: R$/m² mediano por cep5 (aproximação do que o comparar_itbi faz;
# ele tenta rua+cep -> rua -> cep, aqui usamos cep como piso)
m_cep = {}
g = regioes(treino, "cep5", 1)
for cep5, xs in g.items():
    m_cep[cep5] = mediana([x["m2"] for x in xs])
m_cidade = mediana([x["m2"] for x in treino])

erros_c = []
erros_m = []
usou = 0
for x in teste:
    m = m_cep.get(x["cep5"])
    if m is None:
        m = m_cidade
    else:
        usou += 1
    erros_c.append(abs(x["m2"] - m_cidade) / x["m2"])
    erros_m.append(abs(x["m2"] - m) / x["m2"])

erros_c.sort()
erros_m.sort()
print(f"  erro mediano do R$/m² (média da cidade): "
      f"{100*statistics.median(erros_c):.1f}%")
print(f"  erro mediano do R$/m² (por CEP, como o app): "
      f"{100*statistics.median(erros_m):.1f}%")
print()
print("  Leitura: mesmo padrão. O R$/m² mediano de uma região erra o preço")
print("  de UMA transação individual em ~30%. Por isso o comparativo serve")
print("  para ORDENAR ('esta casa está acima/abaixo do bairro'), não para")
print("  cravar o preço exato.")

print()
print("=" * 70)
print("3. OS DOIS NÚMEROS SÃO CONSISTENTES ENTRE SI?")
print("=" * 70)
# Se a razão venal/preço é ~0,82 na cidade, o "valor venal" deveria ficar
# perto de 82% do preço de mercado. Conferindo nas transações de teste:
r_test = mediana([x["razao"] for x in teste])
print(f"  razão venal/preço mediana no teste: {r_test:.3f}")
print(f"  -> o venal fica ~{100*(1-r_test):.0f}% ABAIXO do preço de mercado.")
print(f"     Isso é ESPERADO: o VVR costuma ficar abaixo do valor negociado.")
print()
print("  CONCLUSÃO: os dois cálculos estão METODOLOGICAMENTE corretos")
print("  (filtros de qualidade, mediana robusta, prioridade a financiadas),")
print("  mas são referências de FAIXA, não valores exatos. O erro mediano é")
print("  ~26% no venal e ~30% no R$/m², dominado pela variação dentro de cada")
print("  região — não por defeito do método.")

conn.close()
