"""Testa o risco real de LINKAR a foto: o link continua vivo?

Linkar em vez de hospedar tem um custo escondido: a imobiliária pode remover o
anuncio a qualquer momento, e nesse dia o site mostra imagem quebrada. Quem
hospeda, nao tem esse problema.

Este script mede com numeros:
  1. Quantas URLs de foto ainda respondem 200 (sem Referer, como o site faz)?
  2. A distribuicao por portal -- algum portal e' mais fragil?
  3. As URLs apontam para um anuncio que ainda existe, ou para o CDN direto?
     (se for o CDN direto, remover o anuncio NAO derruba a foto)

Uso: python testar_links_vivos.py [amostra]
"""

from __future__ import annotations

import collections
import concurrent.futures as cf
import random
import sqlite3
import sys
import time

import requests

try:
    sys.stdout.reconfigure(errors="replace")
except Exception:  # noqa: BLE001
    pass

requests.packages.urllib3.disable_warnings()  # noqa: E402

sys.path.insert(0, "src")
import config  # noqa: E402

AMOSTRA = int(sys.argv[1]) if len(sys.argv) > 1 else 120

conn = sqlite3.connect(config.DB_PATH)
conn.row_factory = sqlite3.Row

# agrupa por portal, para ver se algum e' pior
por_portal: dict[str, list[str]] = collections.defaultdict(list)
for r in conn.execute("SELECT foto_url FROM fotos WHERE foto_url LIKE 'http%'"):
    u = r["foto_url"]
    host = u.split("/")[2].lower()
    if "olx" in host:
        p = "OLX"
    elif "zapimoveis" in host:
        p = "ZAP"
    elif "naventcdn" in host or "vivareal" in host:
        p = "ZAP/VivaReal"
    elif "imovelwebcdn" in host:
        p = "Imovelweb"
    elif "quintoandar" in host:
        p = "QuintoAndar"
    else:
        p = host
    por_portal[p].append(u)
conn.close()

print("=" * 74)
print("LINKS VIVOS: a foto continua disponivel sem Referer?")
print("=" * 74)
print(f"  amostra: ate {AMOSTRA} URLs por portal")
print()

HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                   "AppleWebKit/537.36 (KHTML, like Gecko) "
                   "Chrome/120.0 Safari/537.36"),
    "Accept": "image/avif,image/webp,image/*,*/*;q=0.8",
}


def checar(url: str) -> tuple[str, int, str]:
    try:
        # GET com stream: descobre o status sem baixar a imagem toda
        r = requests.get(url, timeout=25, verify=False, headers=HEADERS,
                         stream=True)
        ct = r.headers.get("Content-Type", "")[:22]
        r.close()
        return url, r.status_code, ct
    except Exception as e:  # noqa: BLE001
        return url, -1, type(e).__name__


random.seed(7)
total_ok = total = 0
resumo = {}
t0 = time.time()
for portal, urls in sorted(por_portal.items(), key=lambda kv: -len(kv[1])):
    if len(urls) < 3:
        continue
    escolhidas = random.sample(urls, min(AMOSTRA, len(urls)))
    stat = collections.Counter()
    tipos = collections.Counter()
    with cf.ThreadPoolExecutor(max_workers=8) as ex:
        for _, code, ct in ex.map(checar, escolhidas):
            stat[code] += 1
            if code == 200:
                tipos[ct] += 1
    ok = stat[200]
    n = len(escolhidas)
    total_ok += ok
    total += n
    resumo[portal] = (ok, n)
    pct = ok / n * 100 if n else 0
    print(f"  {portal:<14} {ok:>4}/{n:<4} {pct:>5.1f}% 200   "
          f"outros: {dict((k, v) for k, v in stat.items() if k != 200)}")
    if tipos:
        print(f"                 content-type: {dict(tipos)}")

print()
print(f"  TOTAL: {total_ok:,} de {total:,} = {total_ok/total*100:.1f}% "
      f"vivos  ({time.time()-t0:.0f}s)")

print()
print("=" * 74)
print("AS URLs APONTAM PARA O CDN DIRETO OU PARA O ANUNCIO?")
print("=" * 74)
# se for CDN direto com nome de arquivo proprio, remover o anuncio costuma
# NAO derrubar a foto (o arquivo continua no CDN)
padroes = {
    "nome do arquivo tem id numerico": r"/\d{6,}[.\-_]",
    "caminho tem /avisos/ (Imovelweb, arquivo proprio)": r"/avisos/",
    "caminho tem /resize/ (processamento de imagem)": r"/resize/",
    "URL tem crop/landscape (QuintoAndar)": r"/crop/",
}
conn = sqlite3.connect(config.DB_PATH)
for nome, pat in padroes.items():
    import re
    n = sum(1 for (u,) in conn.execute("SELECT foto_url FROM fotos")
            if u and re.search(pat, u))
    print(f"  {nome:<52} {n:>7,}")
print()
print("  amostra de URL por portal:")
vistos = set()
for (u,) in conn.execute("SELECT foto_url FROM fotos WHERE foto_url LIKE 'http%'"):
    h = u.split("/")[2]
    if h in vistos:
        continue
    vistos.add(h)
    print(f"     {h:<32} {u[:88]}")
conn.close()
