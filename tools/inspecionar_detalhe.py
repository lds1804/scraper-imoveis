"""Abre a página individual de um anúncio e inspeciona a estrutura.

Usa o MESMO contexto persistente do main.py (config.USER_DATA_DIR), pois é
o perfil que já passou pelo desafio do Cloudflare. Sem ele, o site devolve
a página "Um momento..." e a inspeção não acha nada.

Uso:
    python inspecionar_detalhe.py            # usa o 1º anúncio do banco
    python inspecionar_detalhe.py <indice>   # usa o N-ésimo (0-based)
"""


from _bootstrap import iniciar

iniciar()  # poe src/ no sys.path e fixa a raiz como diretorio de trabalho

import re
import sqlite3
import sys

from playwright.sync_api import sync_playwright

import config
from scraper_browser import _e_challenge

indice = int(sys.argv[1]) if len(sys.argv) > 1 else 0

c = sqlite3.connect(config.DB_PATH)
row = c.execute(
    "SELECT url, titulo, area_terreno FROM anuncios LIMIT 1 OFFSET ?", (indice,)
).fetchone()
c.close()

if row is None:
    print(f"Não existe anúncio no índice {indice}.")
    raise SystemExit(1)

url, titulo, terreno = row
print(f"URL   : {url}")
print(f"Título: {titulo}")
print(f"Terreno atual no banco: {terreno}\n")

html = None
with sync_playwright() as p:
    ctx = p.chromium.launch_persistent_context(
        config.USER_DATA_DIR,
        headless=config.HEADLESS,
        user_agent=config.USER_AGENT,
        locale="pt-BR",
        viewport={"width": 1366, "height": 900},
        args=["--disable-blink-features=AutomationControlled"],
    )
    page = ctx.pages[0] if ctx.pages else ctx.new_page()

    for tentativa in range(1, config.MAX_RETRIES + 1):
        resp = page.goto(url, wait_until="domcontentloaded", timeout=config.NAV_TIMEOUT_MS)
        status = resp.status if resp else "?"
        bloqueado = _e_challenge(page)
        print(f"tentativa {tentativa}: status {status} | challenge={bloqueado}")

        if status == 200 and not bloqueado:
            page.wait_for_timeout(4000)
            # rola a página para carregar seções preguiçosas (lazy)
            page.mouse.wheel(0, 4000)
            page.wait_for_timeout(1500)
            page.mouse.wheel(0, 4000)
            page.wait_for_timeout(1500)
            html = page.content()
            break

        if bloqueado:
            page.wait_for_timeout(config.CHALLENGE_TIMEOUT_MS)

    ctx.close()

if not html:
    print("\nNão consegui acessar (bloqueio do Cloudflare).")
    print("Rode `python main.py --dry-run` com HEADLESS=False para renovar o perfil.")
    raise SystemExit(1)

open("debug_detalhe.html", "w", encoding="utf-8").write(html)
print(f"\nHTML salvo em debug_detalhe.html ({len(html)} bytes)\n")

# Relatório em arquivo (o console do Windows quebra o encoding de acentos/m²)
linhas: list[str] = []


def R(txt: str = "") -> None:
    linhas.append(txt)


R("=== data-qa relevantes ===")
for m in sorted(set(re.findall(r'data-qa="([^"]*)"', html))):
    if re.search(r"(feature|area|surface|m2|terrain|characteristic|price|title|address)", m, re.I):
        R("  " + m)

R("\n=== Bloco mainFeatures (JSON) ===")
mf = re.search(r"const mainFeatures = (\{.*?\});", html, re.DOTALL)
R("  " + mf.group(1) if mf else "  (não encontrado)")

R("\n=== Onde aparece 'm²' (contexto, sem tags) ===")
vistos = set()
for m in re.finditer(r".{90}m².{50}", html):
    trecho = re.sub(r"<[^>]+>", " ", m.group(0))
    trecho = re.sub(r"\s+", " ", trecho).strip()
    if trecho not in vistos:
        vistos.add(trecho)
        R("  ... " + trecho)

R("\n=== Title / H1 ===")
mt = re.search(r"<title>(.*?)</title>", html, re.DOTALL)
R("TITLE: " + (mt.group(1).strip()[:200] if mt else "?"))
mh = re.search(r"<h1[^>]*>(.*?)</h1>", html, re.DOTALL)
R("H1: " + (re.sub(r"<[^>]+>", " ", mh.group(1)).strip()[:200] if mh else "?"))

# Meta keywords costuma listar todas as características
mk = re.search(r'<meta name="keywords" content="(.*?)"', html, re.DOTALL)
R("\n=== meta keywords ===")
R("  " + (mk.group(1)[:800] if mk else "?"))

texto = "\n".join(linhas)
open("insp_detalhe.txt", "w", encoding="utf-8").write(texto)
print("Relatório salvo em insp_detalhe.txt")


print("\n=== Title / H1 ===")
mt = re.search(r"<title>(.*?)</title>", html, re.DOTALL)
print("TITLE:", mt.group(1).strip()[:150] if mt else "?")
mh = re.search(r"<h1[^>]*>(.*?)</h1>", html, re.DOTALL)
print("H1:", re.sub(r"<[^>]+>", " ", mh.group(1)).strip()[:150] if mh else "?")
