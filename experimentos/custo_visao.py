"""Modelo de CUSTO de tokens da analise visual (medido, nao estimado).

O usuario disse "seria 30 reais para rodar para o banco todos". Quero saber
exatamente de onde sai esse numero e onde esta o desperdicio.

Precos e consumo ja medidos na sessao anterior (nao chutados):
  detail: low      -> 203 tokens/imagem
  detail: original -> 458 tokens/imagem
  prompt de producao -> ~907 tokens
  saida              -> ~265 tokens por anuncio
  deepseek-flash US$/1M: entrada 0,30 peak / 0,15 off-peak
                         saida   1,20 peak / 0,60 off-peak
  peak = 01-04h e 06-10h UTC, seg-sex

Perguntas que este script responde:
  1. Quantas fotos cada anuncio tem de verdade (a media esconde a cauda)?
  2. Quantas fotos sao REPETIDAS (mesmo arquivo em anuncios diferentes)?
     Isso e dinheiro jogado fora se a gente nao deduplicar.
  3. Qual a matriz de custo: low/original x todas/limitadas x peak/off-peak
  4. Quanto custa a ATUALIZACAO DIARIA (so os anuncios novos)?

Uso: python custo_visao.py
"""

from __future__ import annotations

import collections
import sqlite3
import sys

try:
    sys.stdout.reconfigure(errors="replace")
except Exception:  # noqa: BLE001
    pass

sys.path.insert(0, "src")
from cacaimoveis import config  # noqa: E402

# --- parametros medidos (nao estimados) -------------------------------------
TOK_FOTO_LOW = 203
TOK_FOTO_ORIG = 458
TOK_PROMPT = 907
TOK_SAIDA = 265
USD_ENTRADA_PEAK = 0.30 / 1_000_000
USD_ENTRADA_OFF = 0.15 / 1_000_000
USD_SAIDA_PEAK = 1.20 / 1_000_000
USD_SAIDA_OFF = 0.60 / 1_000_000
USD_BRL = 5.42

conn = sqlite3.connect(config.DB_PATH)
conn.row_factory = sqlite3.Row


def rs(sql):
    return list(conn.execute(sql))


print("=" * 78)
print("1. FOTOS POR ANUNCIO  (a media esconde a cauda)")
print("=" * 78)
cont = {r["anuncio_url"]: r["n"] for r in rs(
    "SELECT anuncio_url, COUNT(*) n FROM fotos GROUP BY 1")}
total_ads = list(conn.execute("SELECT COUNT(*) FROM anuncios"))[0][0]
qtd = sorted(cont.values())
print(f"  anuncios com foto: {len(qtd):,} de {total_ads:,}")
if qtd:
    print(f"  total de fotos:    {sum(qtd):,}")
    print(f"  media:             {sum(qtd) / len(qtd):.1f}")
    print(f"  mediana:           {qtd[len(qtd) // 2]}")
    print(f"  p90:               {qtd[int(len(qtd) * 0.9)]}")
    print(f"  p99:               {qtd[int(len(qtd) * 0.99)]}")
    print(f"  maximo:            {qtd[-1]}")
    # histograma grosso
    faixas = collections.Counter()
    for n in qtd:
        if n <= 5:
            faixas["1-5"] += 1
        elif n <= 12:
            faixas["6-12"] += 1
        elif n <= 25:
            faixas["13-25"] += 1
        elif n <= 50:
            faixas["26-50"] += 1
        else:
            faixas["51+"] += 1
    print("  distribuicao:")
    for f in ("1-5", "6-12", "13-25", "26-50", "51+"):
        n = faixas[f]
        print(f"     {f:<7} {n:>6,} anuncios ({n / len(qtd) * 100:>4.1f}%)  "
              f"{'#' * int(n / len(qtd) * 44)}")

print()
print("=" * 78)
print("2. FOTOS REPETIDAS  (dinheiro jogado fora se nao deduplicar)")
print("=" * 78)
# o nome do arquivo no CDN e unico; mesma foto rehospedada tem nome diferente,
# mas a MESMA em anuncios repetidos (dup_grupo) tem a mesma URL.
urls = [r[0] for r in rs("SELECT foto_url FROM fotos")]
unicas = set(urls)
print(f"  linhas na tabela fotos: {len(urls):,}")
print(f"  URLs distintas:         {len(unicas):,}")
print(f"  REPETIDAS:              {len(urls) - len(unicas):,} "
      f"({(len(urls) - len(unicas)) / len(urls) * 100:.1f}%)")
# nm de arquivo (o que realmente identifica a imagem)
nomes = collections.Counter()
for u in urls:
    nomes[u.rsplit("/", 1)[-1].split("?")[0]] += 1
rep = {n: c for n, c in nomes.items() if c > 1}
print(f"  nomes de arquivo distintos: {len(nomes):,}")
print(f"  nomes repetidos:            {len(rep):,} "
      f"(total de linhas {sum(rep.values()):,})")

# quantas fotos sobram se analisarmos 1x por grupo de duplicata?
print()
print("  quanto se economiza analisando 1x por GRUPO de duplicata:")
ads = {r["url"]: (r["dup_grupo"], r["dup_melhor"]) for r in rs(
    "SELECT url, dup_grupo, dup_melhor FROM anuncios")}
fotos_por_ad = {r["anuncio_url"]: r["n"] for r in rs(
    "SELECT anuncio_url, COUNT(*) n FROM fotos GROUP BY 1")}
grupos = collections.defaultdict(list)
sozinhos = 0
for url, (g, melhor) in ads.items():
    if g is None:
        sozinhos += 1
    else:
        grupos[g].append(url)
fotos_se_por_grupo = 0
for url, (g, melhor) in ads.items():
    if g is None:
        fotos_se_por_grupo += fotos_por_ad.get(url, 0)
    elif melhor == 1:
        # so o principal do grupo, mas precisa pegar as fotos do grupo todo
        f = max((fotos_por_ad.get(u, 0) for u in grupos[g]), default=0)
        fotos_se_por_grupo += f
print(f"     anuncios: {total_ads:,} -> grupos distintos: "
      f"{sozinhos + len(grupos):,}")
print(f"     fotos a enviar (1x por grupo): {fotos_se_por_grupo:,} "
      f"de {len(unicas):,}")

print()
print("=" * 78)
print("3. MATRIZ DE CUSTO  (para o banco INTEIRO)")
print("=" * 78)


def custo(n_fotos, n_ads, tok_foto, peak=False):
    ent = n_fotos * tok_foto + n_ads * TOK_PROMPT
    sai = n_ads * TOK_SAIDA
    usd = ent * (USD_ENTRADA_PEAK if peak else USD_ENTRADA_OFF) + \
        sai * (USD_SAIDA_PEAK if peak else USD_SAIDA_OFF)
    return usd, usd * USD_BRL


fotos_todas = sum(qtd)
print(f"  premissa: {total_ads:,} anuncios, {fotos_todas:,} fotos")
print()
print(f"  {'cenario':<44} {'US$':>8} {'R$':>8}")
print("  " + "-" * 62)
cenarios = [
    ("TODAS as fotos, detail=original, peak", fotos_todas, total_ads, TOK_FOTO_ORIG, True),
    ("TODAS as fotos, detail=original, off-peak", fotos_todas, total_ads, TOK_FOTO_ORIG, False),
    ("TODAS as fotos, detail=low, peak", fotos_todas, total_ads, TOK_FOTO_LOW, True),
    ("TODAS as fotos, detail=low, off-peak", fotos_todas, total_ads, TOK_FOTO_LOW, False),
    ("teto 12 fotos, original, off-peak", total_ads * 12, total_ads, TOK_FOTO_ORIG, False),
    ("teto 12 fotos, low, off-peak", total_ads * 12, total_ads, TOK_FOTO_LOW, False),
    ("teto 8 fotos, low, off-peak", total_ads * 8, total_ads, TOK_FOTO_LOW, False),
    ("1x por GRUPO, low, off-peak", fotos_se_por_grupo, sozinhos + len(grupos), TOK_FOTO_LOW, False),
]
for nome, f, a, tk, pk in cenarios:
    usd, brl = custo(f, a, tk, pk)
    print(f"  {nome:<44} {usd:>8.2f} {brl:>8.2f}")

print()
print("=" * 78)
print("4. ATUALIZACAO DIARIA (so os anuncios NOVOS do dia)")
print("=" * 78)
print("  O ponto do pipeline: nao reanalisar o que ja foi analisado.")
for novos, fotos_novos in ((20, 20 * 25), (50, 50 * 25), (100, 100 * 25),
                           (200, 200 * 25)):
    usd, brl = custo(fotos_novos, novos, TOK_FOTO_LOW, False)
    usd_o, brl_o = custo(fotos_novos, novos, TOK_FOTO_ORIG, False)
    print(f"  {novos:>4} anuncios novos/dia, 25 fotos cada:")
    print(f"       detail=low       US$ {usd:.4f}  R$ {brl:.2f}  "
          f"-> R$ {brl * 30:.2f}/mes")
    print(f"       detail=original  US$ {usd_o:.4f}  R$ {brl_o:.2f}  "
          f"-> R$ {brl_o * 30:.2f}/mes")

print()
print("=" * 78)
print("5. O QUE JA ESTA ANALISADO (nao precisa pagar de novo)")
print("=" * 78)
feitos = list(conn.execute(
    "SELECT COUNT(*) FROM anuncios WHERE COALESCE(foto_analisada_em,'')<>''"))[0][0]
faltam = total_ads - feitos
fotos_faltam = 0
for r in rs("""SELECT COUNT(*) n FROM fotos f
               WHERE NOT EXISTS (SELECT 1 FROM anuncios a
                 WHERE a.url=f.anuncio_url
                   AND COALESCE(a.foto_analisada_em,'')<>'')"""):
    fotos_faltam = r[0]
print(f"  ja analisados: {feitos:,}   faltam: {faltam:,}")
print(f"  fotos dos que faltam: {fotos_faltam:,}")
usd, brl = custo(min(fotos_faltam, faltam * 12), faltam, TOK_FOTO_LOW, False)
print(f"  custo de fechar a lacuna (teto 12, low, off-peak): "
      f"US$ {usd:.2f}  R$ {brl:.2f}")
usd, brl = custo(fotos_faltam, faltam, TOK_FOTO_ORIG, False)
print(f"  custo de fechar a lacuna (TODAS, original, off-peak): "
      f"US$ {usd:.2f}  R$ {brl:.2f}")
conn.close()
