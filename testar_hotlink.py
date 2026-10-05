"""Testa se os CDNs dos portais ACEITAM HOTLINK (linkar a imagem de fora).

Isto decide se o approach "linkar em vez de hospedar" funciona ou nao.
Se o CDN checar o Referer e devolver 403, o site mostraria imagem quebrada
e a ideia morre. Se devolver 200, funciona e economiza os 5 GB do S3.

Testo tres cenarios por URL:
  1. SEM Referer        (o navegador manda isso quando a pagina e https->https
                         com <meta name="referrer" content="no-referrer">)
  2. Referer do PROPRIO portal  (uso normal)
  3. Referer de OUTRO site      (hotlink classico -- o caso que costuma ser
                                 bloqueado)

Uso: python testar_hotlink.py
"""

from __future__ import annotations

import collections
import sqlite3
import sys

import requests

try:
    sys.stdout.reconfigure(errors="replace")
except Exception:  # noqa: BLE001
    pass

requests.packages.urllib3.disable_warnings()  # noqa: E402

sys.path.insert(0, "src")
import config  # noqa: E402

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0 Safari/537.36")

conn = sqlite3.connect(config.DB_PATH)
conn.row_factory = sqlite3.Row

# agrupa as fotos por dominio, para testar 1 de cada CDN
por_host = collections.defaultdict(list)
for r in conn.execute("SELECT anuncio_url, foto_url FROM fotos"):
    u = r["foto_url"]
    if not u.startswith("http"):
        continue
    host = u.split("/")[2]
    if len(por_host[host]) < 3:
        por_host[host].append((r["anuncio_url"], u))
conn.close()

HOSTS = {
    "imgbr.imovelwebcdn.com": "Imovelweb",
    "resizedimgs.vivareal.com": "VivaReal",
    "resizedimgs.zapimoveis.com.br": "ZAP",
    "img.olx.com.br": "OLX",
    "images.olx.com.br": "OLX",
    "www.quintoandar.com.br": "QuintoAndar",
}

print("=" * 76)
print("HOTLINK: o CDN aceita servir a imagem para OUTRO site?")
print("=" * 76)
print(f"  hosts de foto no banco: {len(por_host)}")
for h in sorted(por_host, key=lambda x: -len(por_host[x]))[:12]:
    print(f"     {h:<42} {len(por_host[h])} (amostra)")

print()
resultado = {}
for host, (urls) in por_host.items():
    nome = HOSTS.get(host, host)
    if nome not in HOSTS.values():
        continue
    for _, url in urls[:2]:
        linha = []
        for rotulo, ref in (
            ("sem Referer", None),
            ("Referer do portal", f"https://{host}/"),
            ("Referer de outro site", "https://meusite.com.br/pagina"),
        ):
            cab = {"User-Agent": UA, "Accept": "image/avif,image/webp,*/*"}
            if ref:
                cab["Referer"] = ref
            try:
                r = requests.get(url, timeout=30, headers=cab, verify=False,
                                 stream=True)
                # le so o comeco: quero o status e o content-type
                ct = r.headers.get("Content-Type", "")
                tam = r.headers.get("Content-Length", "?")
                ok = r.status_code == 200 and ct.startswith("image")
                linha.append(f"{rotulo}: {r.status_code} {ct[:18]} "
                             f"{'OK' if ok else 'BLOQUEADO'}")
                r.close()
            except Exception as e:  # noqa: BLE001
                linha.append(f"{rotulo}: ERRO {type(e).__name__}")
        chave = f"{nome} | {host}"
        if chave not in resultado:
            resultado[chave] = linha
            print(f"  {nome:<14} {host}")
            for l in linha:
                print(f"       {l}")
            print()

print("=" * 76)
print("VEREDITO")
print("=" * 76)
sem_ref_ok = sum(1 for l in resultado.values()
                 if any("sem Referer: 200" in x for x in l))
outro_ok = sum(1 for l in resultado.values()
               if any("Referer de outro site: 200" in x for x in l))
tot = len(resultado)
print(f"  CDNs testados: {tot}")
print(f"  funcionam SEM Referer:          {sem_ref_ok}/{tot}")
print(f"  funcionam com Referer de fora:  {outro_ok}/{tot}")
print()
if outro_ok == tot:
    print("  -> Hotlink LIBERADO em todos. Dá para linkar sem hospedar.")
elif sem_ref_ok >= outro_ok:
    print("  -> Hotlink bloqueado, mas SEM Referer passa.")
    print("     Solucao: <meta name=\"referrer\" content=\"no-referrer\"> na")
    print("     pagina. O navegador entao nao manda Referer e a imagem carrega.")
else:
    print("  -> Hotlink BLOQUEADO. Nao da para linkar direto.")
    print("     Alternativas: (a) manter as fotos locais e NAO publicar o site,")
    print("     (b) servir so miniatura propria (uso informativo),")
    print("     (c) usar apenas o dado derivado da visao, sem imagem.")
