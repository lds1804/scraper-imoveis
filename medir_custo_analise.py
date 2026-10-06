"""Responde com precisao: quanto custa rodar a analise para o banco?

O numero de R$ 4,72 que dei tem TRES condicoes embutidas, e todas importam:
  1. reaproveitamento por duplicata  -> AINDA NAO EXISTE no codigo
  2. detail=low em vez de original   -> muda a QUALIDADE
  3. escolher 1 membro por grupo     -> qual membro? (eu usei MIN(url), que
                                        pode ser o de menos fotos)

E ha um ponto que so apareceu depois: a CONSERVACAO virou criterio da ordem
padrao. Se a lacuna for analisada com `low` e os 1.829 antigos com `original`,
a nota de encaixe compara dois niveis de qualidade diferentes -- o ranking
fica injusto justamente onde ele decide.

Uso: python medir_custo_analise.py
"""

from __future__ import annotations

import os
import sqlite3
import sys

try:
    sys.stdout.reconfigure(errors="replace")
except Exception:  # noqa: BLE001
    pass

_RAIZ = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_RAIZ, "src"))
os.chdir(_RAIZ)

import config  # noqa: E402

TOK_ORIG, TOK_LOW = 458, 203
TOK_PROMPT, TOK_SAIDA = 907, 265
USD_EO, USD_EL = 0.30 / 1e6, 0.15 / 1e6
USD_SO, USD_SL = 1.20 / 1e6, 0.60 / 1e6
BRL = 5.42


def custo(n_fotos, n_ads, tk, peak=False):
    ent = n_fotos * tk + n_ads * TOK_PROMPT
    sai = n_ads * TOK_SAIDA
    usd = ent * (USD_EO if peak else USD_EL) + sai * (USD_SO if peak else USD_SL)
    return usd, usd * BRL


conn = sqlite3.connect(config.DB_PATH)
conn.row_factory = sqlite3.Row

PEND = "COALESCE(foto_analisada_em,'')=''"
CONST = "COALESCE(foto_analisada_em,'')<>''"

tot = list(conn.execute("SELECT COUNT(*) FROM anuncios"))[0][0]
feitos = list(conn.execute(f"SELECT COUNT(*) FROM anuncios WHERE {CONST}"))[0][0]
faltam = tot - feitos
print(f"banco: {tot:,} anuncios | analisados {feitos:,} | faltam {faltam:,}")

# ---------------------------------------------------------------- grupos
grupos = {}
for r in conn.execute(f"""
    SELECT dup_grupo,
           SUM(CASE WHEN {CONST} THEN 1 ELSE 0 END) ok,
           SUM(CASE WHEN {PEND} THEN 1 ELSE 0 END) pend
    FROM anuncios WHERE dup_grupo IS NOT NULL GROUP BY dup_grupo"""):
    grupos[r["dup_grupo"]] = (r["ok"], r["pend"])

vazios = [g for g, (ok, pend) in grupos.items() if ok == 0 and pend > 0]
mistos = [g for g, (ok, pend) in grupos.items() if ok > 0 and pend > 0]
print(f"grupos: {len(grupos):,} | 100% analisados: "
      f"{len(grupos)-len(vazios)-len(mistos):,} | MISTOS: {len(mistos)} | "
      f"sem nenhuma: {len(vazios):,}")

# quantos dos pendentes herdam de grupo ja analisado?
herdam = list(conn.execute(f"""
    SELECT COUNT(*) FROM anuncios a WHERE {PEND} AND a.dup_grupo IS NOT NULL
      AND EXISTS (SELECT 1 FROM anuncios b WHERE b.dup_grupo=a.dup_grupo
                  AND {CONST})"""))[0][0]
sozinhos = list(conn.execute(f"""
    SELECT COUNT(*) FROM anuncios WHERE {PEND} AND dup_grupo IS NULL"""))[0][0]
print(f"  herdam de grupo ja analisado : {herdam:,} (custo ZERO)")
print(f"  grupos novos (1 por grupo)   : {len(vazios):,}")
print(f"  sem grupo (todos)            : {sozinhos:,}")
precisa_api = len(vazios) + sozinhos
print(f"  -> EXIGEM API: {precisa_api:,}  (de {faltam:,} pendentes)")

print()
print("=" * 74)
print("QUAL MEMBRO DO GRUPO ANALISAR? (eu tinha usado MIN(url))")
print("=" * 74)
# o membro com MAIS fotos cobre melhor o imovel
melhor_fotos = melhor_ads = 0
for g in vazios:
    r = conn.execute(f"""
        SELECT a.url, (SELECT COUNT(*) FROM fotos f WHERE f.anuncio_url=a.url) n
        FROM anuncios a WHERE a.dup_grupo=? AND {PEND}
        ORDER BY n DESC LIMIT 1""", (g,)).fetchone()
    if r:
        melhor_fotos += r["n"]
        melhor_ads += 1
minurl_fotos = 0
for g in vazios:
    r = conn.execute(f"""
        SELECT a.url, (SELECT COUNT(*) FROM fotos f WHERE f.anuncio_url=a.url) n
        FROM anuncios a WHERE a.dup_grupo=? AND {PEND}
        ORDER BY a.url ASC LIMIT 1""", (g,)).fetchone()
    if r:
        minurl_fotos += r["n"]
# fotos dos sozinhos
fotos_soz = list(conn.execute(f"""
    SELECT COUNT(*) FROM fotos WHERE anuncio_url IN (
      SELECT url FROM anuncios WHERE {PEND} AND dup_grupo IS NULL)"""))[0][0]
print(f"  1 por grupo escolhendo o de MAIS fotos : {melhor_fotos:,} fotos")
print(f"  1 por grupo escolhendo MIN(url)        : {minurl_fotos:,} fotos")
print(f"  + os {sozinhos:,} sem grupo            : {fotos_soz:,} fotos")
print(f"  -> total com MAIS fotos: {melhor_fotos + fotos_soz:,}")
print(f"  -> total com MIN(url)  : {minurl_fotos + fotos_soz:,}")

# -------------------------------------------------------------- cenarios
print()
print("=" * 74)
print("CUSTO DE FECHAR A LACUNA (os 2.964 pendentes)")
print("=" * 74)
tot_fotos_pend = list(conn.execute(f"""
    SELECT COUNT(*) FROM fotos WHERE anuncio_url IN (
      SELECT url FROM anuncios WHERE {PEND})"""))[0][0]
com_reuso = melhor_fotos + fotos_soz
print(f"  {'cenario':<52} {'US$':>7} {'R$':>8}")
print("  " + "-" * 69)
cen = [
    ("sem reuso, ALL fotos, original, off-peak", tot_fotos_pend, faltam, TOK_ORIG, False),
    ("sem reuso, ALL fotos, low, off-peak", tot_fotos_pend, faltam, TOK_LOW, False),
    ("COM reuso, ALL fotos, original, off-peak", com_reuso, precisa_api, TOK_ORIG, False),
    ("COM reuso, ALL fotos, low, off-peak", com_reuso, precisa_api, TOK_LOW, False),
    ("COM reuso, teto 12 fotos, original, off-peak",
     min(com_reuso, precisa_api * 12), precisa_api, TOK_ORIG, False),
    ("COM reuso, teto 12 fotos, low, off-peak",
     min(com_reuso, precisa_api * 12), precisa_api, TOK_LOW, False),
]
for nome, f, a, tk, pk in cen:
    usd, brl = custo(f, a, tk, pk)
    print(f"  {nome:<52} {usd:>7.2f} {brl:>8.2f}")

print()
print("=" * 74)
print("CUSTO DE RE-ANALISAR **TODO O BANCO** (as 4.793, do zero)")
print("=" * 74)
# com reuso: 1 por grupo em TODOS os grupos + todos os sem grupo
todos_grupos = list(grupos)
fotos_rep = 0
for g in todos_grupos:
    r = conn.execute("""
        SELECT (SELECT COUNT(*) FROM fotos f WHERE f.anuncio_url=a.url) n
        FROM anuncios a WHERE a.dup_grupo=?
        ORDER BY n DESC LIMIT 1""", (g,)).fetchone()
    fotos_rep += r["n"] if r else 0
fotos_todos_soz = list(conn.execute("""
    SELECT COUNT(*) FROM fotos WHERE anuncio_url IN (
      SELECT url FROM anuncios WHERE dup_grupo IS NULL)"""))[0][0]
ads_rep = len(todos_grupos) + list(conn.execute(
    "SELECT COUNT(*) FROM anuncios WHERE dup_grupo IS NULL"))[0][0]
fotos_rep_total = fotos_rep + fotos_todos_soz
fotos_tudo = list(conn.execute("SELECT COUNT(*) FROM fotos"))[0][0]
print(f"  analisando 1 por grupo: {ads_rep:,} anuncios, {fotos_rep_total:,} fotos")
print()
print(f"  {'cenario':<52} {'US$':>7} {'R$':>8}")
print("  " + "-" * 69)
for nome, f, a, tk in [
    ("TUDO sem reuso, original, off-peak", fotos_tudo, tot, TOK_ORIG),
    ("TUDO sem reuso, low, off-peak", fotos_tudo, tot, TOK_LOW),
    ("TUDO com reuso, original, off-peak", fotos_rep_total, ads_rep, TOK_ORIG),
    ("TUDO com reuso, low, off-peak", fotos_rep_total, ads_rep, TOK_LOW),
    ("TUDO com reuso, teto 12, original, off-peak",
     min(fotos_rep_total, ads_rep * 12), ads_rep, TOK_ORIG),
    ("TUDO com reuso, teto 12, low, off-peak",
     min(fotos_rep_total, ads_rep * 12), ads_rep, TOK_LOW),
]:
    usd, brl = custo(f, a, tk, False)
    print(f"  {nome:<52} {usd:>7.2f} {brl:>8.2f}")

print()
print("=" * 74)
print("A QUESTAO DA CONSISTENCIA (o ponto que decide)")
print("=" * 74)
print("  A conservacao das fotos virou criterio da ORDEM PADRAO (nota de")
print("  encaixe). Se a lacuna for analisada com `detail: low` e os 1.829")
print("  antigos com `original`, a nota compara dois niveis de qualidade:")
print()
u1, b1 = custo(com_reuso, precisa_api, TOK_ORIG, False)
u2, b2 = custo(com_reuso, precisa_api, TOK_LOW, False)
print(f"    original (igual ao que ja existe): R$ {b1:>6.2f}")
print(f"    low      (mais barato)           : R$ {b2:>6.2f}")
print(f"    DIFERENCA                        : R$ {b1-b2:>6.2f}")
print()
print("  Economizar isso criaria DUAS populacoes na mesma nota -- e o ranking")
print("  passaria a favorecer/penalizar conforme o detalhe usado na analise,")
print("  nao conforme o imovel. Por R$ %.2f nao vale." % (b1 - b2))
conn.close()
