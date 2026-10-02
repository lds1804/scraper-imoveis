"""Inspeciona o HTML da listagem para descobrir seletores do card."""


from _bootstrap import iniciar
iniciar()  # poe src/ no sys.path e fixa a raiz como diretorio de trabalho

import re

html = open("debug_playwright.html", encoding="utf-8", errors="replace").read()

print("Tem POSTING_CARD?", "POSTING_CARD" in html)
print()

print("=== data-qa POSTING_CARD_* distintos ===")
for m in sorted(set(re.findall(r'data-qa="(POSTING_CARD_[A-Z_0-9]+)"', html))):
    print(" ", m)

print()
print("=== 'm²' em contexto no card (primeiras 5) ===")
for i, m in enumerate(re.finditer(r".{50}m².{30}", html)):
    if i >= 5:
        break
    trecho = re.sub(r"<[^>]+>", " ", m.group(0))
    trecho = re.sub(r"\s+", " ", trecho).strip()
    print("  ...", trecho)
