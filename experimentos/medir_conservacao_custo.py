"""Duas perguntas: (1) peso para imoveis bem conservados, (2) custo da lacuna.

PARTE 1 — CONSERVACAO x DESCONTO
   O usuario quer que imovel bem conservado pese mais na busca, mesmo com
   desconto pequeno. Antes de inventar peso, medir: conservacao e desconto
   sao independentes, ou um explica o outro? Se sao independentes, a nota
   tem de somar os dois (nao substituir).

PARTE 2 — CUSTO DE FECHAR A LACUNA
   Quantos anuncios faltam, quantas fotos, e quanto custa em cada cenario.
   Inclui o REAPROVEITAMENTO por duplicata, que e' a maior alavanca.

Uso: python medir_conservacao_custo.py
"""

from __future__ import annotations

import os
import sqlite3
import statistics as st
import sys

try:
    sys.stdout.reconfigure(errors="replace")
except Exception:  # noqa: BLE001
    pass

_RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # experimentos/ -> raiz
sys.path.insert(0, os.path.join(_RAIZ, "src"))
os.chdir(_RAIZ)

from cacaimoveis import config  # noqa: E402

# --- parametros MEDIDOS (nao estimados) -------------------------------------
TOK_FOTO_ORIG = 458
TOK_FOTO_LOW = 203
TOK_PROMPT = 907
TOK_SAIDA = 265
USD_ENT_PEAK, USD_ENT_OFF = 0.30 / 1e6, 0.15 / 1e6
USD_SAI_PEAK, USD_SAI_OFF = 1.20 / 1e6, 0.60 / 1e6
BRL = 5.42


def custo(n_fotos, n_ads, tok_foto, peak=False):
    ent = n_fotos * tok_foto + n_ads * TOK_PROMPT
    sai = n_ads * TOK_SAIDA
    usd = (ent * (USD_ENT_PEAK if peak else USD_ENT_OFF)
           + sai * (USD_SAI_PEAK if peak else USD_SAI_OFF))
    return usd, usd * BRL


conn = sqlite3.connect(config.DB_PATH)
conn.row_factory = sqlite3.Row

print("=" * 76)
print("PARTE 1 — CONSERVACAO x DESCONTO: sao a mesma coisa?")
print("=" * 76)

# razao = preco pedido / mediana do ITBI. <1 = abaixo do mercado.
linhas = conn.execute("""
    SELECT a.foto_cuidado, c.razao, a.preco, a.score_quintal,
           a.foto_problemas, a.quartos, a.area_construida
    FROM anuncios a
    JOIN comparacoes c ON c.anuncio_url = a.url
    WHERE a.foto_cuidado IS NOT NULL AND c.razao IS NOT NULL
      AND a.foto_cuidado BETWEEN 1 AND 5""").fetchall()

print(f"  anuncios com analise visual E comparacao ITBI: {len(linhas):,}")
print()
print("  conservacao (foto_cuidado) x desconto:")
print(f"  {'cuidado':>8} {'n':>6} {'razao mediana':>15} {'abaixo do merc.':>17}")
print("  " + "-" * 52)
por_cuidado = {}
for r in linhas:
    por_cuidado.setdefault(r["foto_cuidado"], []).append(r["razao"])
for c in sorted(por_cuidado):
    v = por_cuidado[c]
    abaixo = sum(1 for x in v if x < 1) / len(v) * 100
    print(f"  {c:>8} {len(v):>6,} {st.median(v):>15.3f} {abaixo:>16.1f}%")

# correlacao: conservacao explica o desconto?
import math
xs = [r["foto_cuidado"] for r in linhas]
ys = [r["razao"] for r in linhas]


def corr(a, b):
    ma, mb = st.mean(a), st.mean(b)
    num = sum((x - ma) * (y - mb) for x, y in zip(a, b))
    den = math.sqrt(sum((x - ma) ** 2 for x in a) * sum((y - mb) ** 2 for y in b))
    return num / den if den else 0


print()
print(f"  correlacao(cuidado, razao)   = {corr(xs, ys):+.3f}")
print(f"  correlacao(cuidado, preco)   = {corr(xs, [r['preco'] or 0 for r in linhas]):+.3f}")
print()
print("  Leitura: correlacao perto de ZERO significa que conservacao e desconto")
print("  sao INDEPENDENTES. Nesse caso a nota tem de SOMAR os dois criterios, e")
print("  nao usar um no lugar do outro.")

print()
print("  os melhores casos (bem conservado E abaixo do mercado):")
bons = [r for r in linhas if r["foto_cuidado"] >= 4 and r["razao"] < 0.9]
print(f"     bem conservado (>=4) E 10%+ abaixo: {len(bons):,} anuncios")
bons2 = [r for r in linhas if r["foto_cuidado"] >= 4 and r["razao"] < 0.95]
print(f"     bem conservado (>=4) E 5%+ abaixo : {len(bons2):,} anuncios")
ruins = [r for r in linhas if r["foto_cuidado"] <= 2 and r["razao"] < 0.9]
print(f"     mal conservado (<=2) E 10%+ abaixo : {len(ruins):,} anuncios")

print()
print("  com problema visivel (mofo/infiltracao), por faixa de desconto:")
for lim in (0.85, 0.90, 0.95, 1.0):
    sub = [r for r in linhas if r["razao"] < lim]
    com = sum(1 for r in sub if r["foto_problemas"])
    if sub:
        print(f"     razao < {lim}: {com:>4,} de {len(sub):>4,} "
              f"({com/len(sub)*100:>4.1f}%) com problema")

print()
print("=" * 76)
print("PARTE 2 — CUSTO DE FECHAR A LACUNA")
print("=" * 76)
tot = list(conn.execute("SELECT COUNT(*) FROM anuncios"))[0][0]
faltam = list(conn.execute(
    "SELECT COUNT(*) FROM anuncios WHERE COALESCE(foto_analisada_em,'')=''"))[0][0]
print(f"  analisados: {tot-faltam:,}   faltam: {faltam:,}")

# fotos dos que faltam
n_fotos = list(conn.execute("""
    SELECT COUNT(*) FROM fotos f
    WHERE f.anuncio_url IN (SELECT url FROM anuncios
                            WHERE COALESCE(foto_analisada_em,'')='')"""))[0][0]
print(f"  fotos dos que faltam: {n_fotos:,}")

# REAPROVEITAMENTO: quantos dos que faltam estao em grupo que JA tem
# alguem analisado? Esses herdam a analise sem chamar a API.
reaproveitaveis = list(conn.execute("""
    SELECT COUNT(*) FROM anuncios a
    WHERE COALESCE(a.foto_analisada_em,'')=''
      AND a.dup_grupo IS NOT NULL
      AND EXISTS (SELECT 1 FROM anuncios b
                  WHERE b.dup_grupo = a.dup_grupo
                    AND COALESCE(b.foto_analisada_em,'')<>'')"""))[0][0]
# e os que estao em grupo mas o grupo INTEIRO nao tem analise: basta analisar 1
grupos_novos = list(conn.execute("""
    SELECT COUNT(DISTINCT a.dup_grupo) FROM anuncios a
    WHERE COALESCE(a.foto_analisada_em,'')=''
      AND a.dup_grupo IS NOT NULL
      AND NOT EXISTS (SELECT 1 FROM anuncios b
                      WHERE b.dup_grupo = a.dup_grupo
                        AND COALESCE(b.foto_analisada_em,'')<>'')"""))[0][0]
sem_grupo = list(conn.execute("""
    SELECT COUNT(*) FROM anuncios
    WHERE COALESCE(foto_analisada_em,'')='' AND dup_grupo IS NULL"""))[0][0]

print()
print("  decomposicao da lacuna:")
print(f"     herdam de grupo ja analisado : {reaproveitaveis:,}  (custo ZERO)")
print(f"     grupos novos (analisa 1 cada): {grupos_novos:,}  (custo de 1)")
print(f"     sem grupo (analisa todos)    : {sem_grupo:,}")
precisa_api = grupos_novos + sem_grupo
print(f"     -> anuncios que EXIGEM API   : {precisa_api:,} "
      f"(era {faltam:,} sem o reaproveitamento)")

# fotos apenas dos que exigem API
fotos_api = list(conn.execute("""
    SELECT COUNT(*) FROM fotos f
    WHERE f.anuncio_url IN (
        SELECT url FROM anuncios a
        WHERE COALESCE(a.foto_analisada_em,'')=''
          AND (a.dup_grupo IS NULL
               OR NOT EXISTS (SELECT 1 FROM anuncios b
                              WHERE b.dup_grupo = a.dup_grupo
                                AND COALESCE(b.foto_analisada_em,'')<>''))
          AND (a.dup_grupo IS NULL
               OR a.url = (SELECT MIN(url) FROM anuncios c
                           WHERE c.dup_grupo = a.dup_grupo
                             AND COALESCE(c.foto_analisada_em,'')='')))"""))[0][0]
print(f"     fotos desses anuncios        : {fotos_api:,}")

print()
print("  " + "=" * 62)
print(f"  {'cenario':<44} {'US$':>7} {'R$':>8}")
print("  " + "=" * 62)
cenarios = [
    ("TUDO (sem reaproveitar), original, peak", n_fotos, faltam, TOK_FOTO_ORIG, True),
    ("TUDO (sem reaproveitar), original, off-peak", n_fotos, faltam, TOK_FOTO_ORIG, False),
    ("TUDO (sem reaproveitar), low, off-peak", n_fotos, faltam, TOK_FOTO_LOW, False),
    ("COM reaproveitamento, original, off-peak", fotos_api, precisa_api, TOK_FOTO_ORIG, False),
    ("COM reaproveitamento, low, off-peak", fotos_api, precisa_api, TOK_FOTO_LOW, False),
    ("REAPROVEIT. + teto 12 fotos, low, off-peak",
     min(fotos_api, precisa_api * 12), precisa_api, TOK_FOTO_LOW, False),
]
for nome, f, a, tk, pk in cenarios:
    usd, brl = custo(f, a, tk, pk)
    print(f"  {nome:<44} {usd:>7.2f} {brl:>8.2f}")

print()
print("  cenario recomendado detalhado (reaproveitamento + teto 12 + low, off-peak):")
f2 = min(fotos_api, precisa_api * 12)
usd, brl = custo(f2, precisa_api, TOK_FOTO_LOW, False)
print(f"     anuncios: {precisa_api:,}   fotos enviadas: {f2:,}")
print(f"     US$ {usd:.4f}  =  R$ {brl:.2f}")
print()
print("  o que o reaproveitamento economiza (mesmo cenario, sem ele):")
usd2, brl2 = custo(min(n_fotos, faltam * 12), faltam, TOK_FOTO_LOW, False)
print(f"     sem reaproveitar: R$ {brl2:.2f}   ->  com: R$ {brl:.2f}"
      f"   (economia de R$ {brl2-brl:.2f}, {(1-brl/brl2)*100:.0f}%)")

print()
print("=" * 76)
print("  TEMPO (a coleta leva ~0,9s por anuncio com 10 em paralelo)")
print("=" * 76)
for n in (precisa_api, faltam):
    s = n * 0.9
    print(f"     {n:>6,} anuncios -> {s/60:>5.1f} min ({s/3600:.1f} h)")
conn.close()
