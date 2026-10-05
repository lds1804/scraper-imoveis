"""Testa onde as copias do mesmo imovel aparecem.

DECISAO DE PRODUTO: as copias aparecem na PAGINA DO ANUNCIO (tabela com os
precos de cada imobiliaria), e a LISTAGEM nao se expande.

Por que: a mesma casa pode ter 25 anuncios. Expandir na listagem viraria 25
cards identicos -- o oposto de ajudar a comparar. Numa tabela, os precos
ficam lado a lado e a diferenca aparece de uma vez.

Uso: python testar_grupo_copias.py
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

OK = FALHA = 0
falhas: list[str] = []


def checar(nome: str, cond: bool, extra: str = "") -> None:
    global OK, FALHA
    if cond:
        OK += 1
        print(f"  PASSA  {nome}" + (f"   {extra}" if extra else ""))
    else:
        FALHA += 1
        falhas.append(nome)
        print(f"  FALHA  {nome}   {extra}")


def get(q: str = "") -> tuple[int, str]:
    with webapp.app.test_client() as c:
        r = c.get("/" + q)
        return r.status_code, r.get_data(as_text=True)


def detalhe(url: str) -> tuple[int, str]:
    with webapp.app.test_client() as c:
        r = c.get("/anuncio/" + urllib.parse.quote(url, safe=""))
        return r.status_code, r.get_data(as_text=True)


def total(html: str) -> int:
    m = re.search(r"<b>([\d.]+)</b>\s*imóve", html)
    return int(m.group(1).replace(".", "")) if m else -1


conn = sqlite3.connect(config.DB_PATH)
conn.row_factory = sqlite3.Row

g = conn.execute("""SELECT dup_grupo g, COUNT(*) n FROM anuncios
                    WHERE dup_grupo IS NOT NULL GROUP BY 1
                    ORDER BY n DESC LIMIT 1""").fetchone()
GRUPO, N_GRUPO = g["g"], g["n"]
principal = conn.execute("""SELECT url, preco FROM anuncios
                            WHERE dup_grupo=? AND dup_melhor=1 LIMIT 1""",
                         (GRUPO,)).fetchone()
print(f"grupo de teste: {GRUPO} com {N_GRUPO} anuncios")
print(f"principal: {principal['url'][:62]}  R$ {principal['preco']:,.0f}")
conn.close()

print()
print("=" * 72)
print("1. A LISTAGEM nao se expande (1 linha por imovel)")
print("=" * 72)
_, home = get()
t_home = total(home)
_, todas = get("?todas=1")
t_todas = total(todas)
checar("a home mostra 1 linha por imovel", t_home == 2938, f"total={t_home}")
checar("`?todas=1` continua existindo (opcao da barra)",
       t_todas == 4793, f"total={t_todas}")
checar("a home e' menor que a lista com repetidos", t_home < t_todas)

_, html_g = get(f"?grupo={GRUPO}")
checar("`?grupo=N` nao filtra mais a listagem (ignorado)",
       total(html_g) == t_home, f"total={total(html_g)}")

print()
print("=" * 72)
print("2. O aviso no card leva a PAGINA DO ANUNCIO")
print("=" * 72)
m = re.search(r'<a class="card-copias"\s+href="([^"]+)"', home)
checar("existe o aviso de copias", m is not None)
if m:
    link = m.group(1).replace("&amp;", "&")
    checar("o link vai para /anuncio/...", link.startswith("/anuncio/"),
           link[:70])
    checar("o link NAO usa 'todas=1'", "todas=1" not in link)
    checar("o link NAO usa 'grupo='", "grupo=" not in link)
    checar("o link tem a ancora #ofertas", link.endswith("#ofertas"),
           link[-20:])

print()
print("=" * 72)
print("3. A PAGINA DO ANUNCIO mostra as outras ofertas")
print("=" * 72)
code, det = detalhe(principal["url"])
checar("a pagina abre", code == 200, f"HTTP {code}")
checar("tem o bloco 'Outras ofertas deste imovel'",
       "Outras ofertas deste imóvel" in det)
checar("o bloco tem a ancora #ofertas", 'id="ofertas"' in det)
checar(f"o cabecalho diz o total ({N_GRUPO})", f"{N_GRUPO} no total" in det)
checar("tem a tabela de ofertas", 'class="ofertas-tabela"' in det)
checar("o proprio anuncio aparece marcado", "este anúncio" in det)

n_linhas = len(re.findall(r'class="ofertas-link"', det))
checar(f"lista as outras {N_GRUPO - 1} ofertas com link",
       n_linhas == N_GRUPO - 1, f"{n_linhas} links (esperado {N_GRUPO - 1})")

print()
print("=" * 72)
print("4. A tabela diz ONDE o anuncio se posiciona no preco")
print("=" * 72)
checar("explica a faixa de preco (ou o empate)",
       "de diferença" in det or "mesmo preço" in det)
checar("marca a mais barata na tabela", "mais barata" in det)
checar("a nota explica que sao copias de imobiliarias",
       "imobiliárias diferentes" in det)
# 24 dos 25 tem o MESMO preco: marcar todos como "mais barata" seria 24 selos
# identicos e zero informacao. O selo tem de aparecer UMA vez.
n_selos = len(re.findall(r'class="ofertas-menor"', det))
checar("o selo 'mais barata' aparece UMA vez so", n_selos == 1,
       f"{n_selos} selos")

print()
print("=" * 72)
print("5. Anuncio SEM copia nao mostra o bloco")
print("=" * 72)
conn = sqlite3.connect(config.DB_PATH)
conn.row_factory = sqlite3.Row
sozinho = conn.execute("""SELECT url FROM anuncios
                          WHERE dup_grupo IS NULL LIMIT 1""").fetchone()
if sozinho:
    code3, det3 = detalhe(sozinho["url"])
    checar("anuncio sem copia abre", code3 == 200, f"HTTP {code3}")
    checar("nao mostra o bloco de ofertas",
           "Outras ofertas deste imóvel" not in det3)

g2 = conn.execute("""SELECT dup_grupo g FROM anuncios
                     WHERE dup_grupo IS NOT NULL GROUP BY 1
                     HAVING COUNT(*)=2 LIMIT 1""").fetchone()
if g2:
    u2 = conn.execute("""SELECT url FROM anuncios WHERE dup_grupo=?
                         AND dup_melhor=1 LIMIT 1""", (g2["g"],)).fetchone()
    code4, det4 = detalhe(u2["url"])
    checar("grupo de 2 abre", code4 == 200, f"HTTP {code4}")
    checar("grupo de 2 mostra 1 outra oferta",
           len(re.findall(r'class="ofertas-link"', det4)) == 1)
    checar("grupo de 2 diz '2 no total'", "2 no total" in det4)

# grupo com variacao real de preco
gv = conn.execute("""SELECT dup_grupo g FROM anuncios
                     WHERE dup_grupo IS NOT NULL
                     GROUP BY 1 HAVING MAX(preco) > MIN(preco) * 1.05
                     LIMIT 1""").fetchone()
if gv:
    uv = conn.execute("""SELECT url FROM anuncios WHERE dup_grupo=?
                         AND dup_melhor=1 LIMIT 1""", (gv["g"],)).fetchone()
    _, det5 = detalhe(uv["url"])
    checar("grupo com variacao mostra a faixa de precos",
           "de diferença" in det5, "mostrou faixa")
conn.close()

print()
print("=" * 72)
print(f"{OK} passaram, {FALHA} falharam")
if falhas:
    for f in falhas:
        print(f"  - {f}")
    sys.exit(1)
print("TODAS AS VERIFICACOES PASSARAM")
