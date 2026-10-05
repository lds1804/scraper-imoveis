"""Reproduz o defeito: clicar em "+N anuncios deste imovel" nao mostra o grupo.

O QUE O USUARIO ESPERA
Ao clicar no aviso "+2 anuncios deste imovel", ver AS OUTRAS OFERTAS DAQUELE
imovel (o grupo de duplicatas).

O QUE O CODIGO FAZ
O link leva para `?todas=1`, que mostra a listagem INTEIRA com todos os
duplicados (4.793 anuncios) em vez do grupo. Ou seja: clicar no aviso nao
aproxima o usuario das outras ofertas -- ele cai na mesma lista de antes,
so que maior.

Uso: python reproduzir_copias.py
"""

from __future__ import annotations

import os
import re
import sqlite3
import sys
import urllib.parse

try:
    sys.stdout.reconfigure(errors="replace")
except Exception:  # noqa: BLE001
    pass

_RAIZ = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _RAIZ)
sys.path.insert(0, os.path.join(_RAIZ, "src"))
os.chdir(_RAIZ)

import config  # noqa: E402
import webapp  # noqa: E402

conn = sqlite3.connect(config.DB_PATH)
conn.row_factory = sqlite3.Row

# um grupo com varias copias, e o principal dele (o que aparece na lista)
grupo = conn.execute("""
    SELECT dup_grupo g, COUNT(*) n FROM anuncios
    WHERE dup_grupo IS NOT NULL GROUP BY 1
    HAVING n >= 3 ORDER BY n DESC LIMIT 1""").fetchone()
print(f"grupo escolhido: {grupo['g']}  com {grupo['n']} anuncios")

membro = conn.execute("""SELECT url, preco, dup_grupo FROM anuncios
                         WHERE dup_grupo=? LIMIT 1""", (grupo["g"],)).fetchone()
print(f"anuncio do card: {membro['url'][:66]}")

print()
print("=" * 72)
print("1. O card mostra o aviso de copias?")
print("=" * 72)
with webapp.app.test_client() as c:
    home = c.get("/").get_data(as_text=True)
n_avisos = len(re.findall(r'class="card-copias"', home))
print(f"  avisos 'card-copias' na home: {n_avisos}")

# extrai o href do primeiro aviso
m = re.search(r'<a class="card-copias"\s+href="([^"]+)"', home)
if not m:
    print("  *** nao achei o link do aviso")
    link = None
else:
    link = m.group(1).replace("&amp;", "&")
    print(f"  href do aviso: {link[:100]}")

print()
print("=" * 72)
print("2. Para ONDE o link leva? (o defeito)")
print("=" * 72)
if link:
    so_todas = "todas=1" in link
    print(f"  o link tem 'todas=1': {so_todas}")
    print()
    print("  'todas=1' significa: mostre a listagem INTEIRA com os duplicados.")
    print("  Isso NAO filtra o grupo do imovel clicado.")

    # confirma medindo
    with webapp.app.test_client() as c:
        sem = c.get("/").get_data(as_text=True)
        com = c.get(link).get_data(as_text=True)
    def total(html):
        m = re.search(r'<b>([\d.]+)</b>\s*(?:imóve|imove)', html)
        return m.group(1) if m else "?"
    print()
    print(f"  total SEM todas=1 : {total(sem)}")
    print(f"  total COM todas=1 : {total(com)}")
    print(f"  -> o clique trazia a lista inteira, nao o grupo de {grupo['n']}.")

print()
print("=" * 72)
print("3. O que DEVERIA acontecer")
print("=" * 72)
print(f"  mostrar os {grupo['n']} anuncios do grupo {grupo['g']}")
print()
print("  os membros do grupo (precos):")
for r in conn.execute("""SELECT titulo, preco, portal, url FROM anuncios
                         WHERE dup_grupo=? ORDER BY preco""", (grupo["g"],)):
    print(f"     R$ {r['preco'] or 0:>10,.0f}  {str(r['portal']):<12} "
          f"{(r['titulo'] or '')[:40]}")
conn.close()
