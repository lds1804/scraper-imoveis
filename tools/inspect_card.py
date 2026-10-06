
"""Analisa varios cards para basear o parser."""


from _bootstrap import iniciar

iniciar()  # poe src/ no sys.path e fixa a raiz como diretorio de trabalho

import re

from bs4 import BeautifulSoup

html = open("debug_playwright.html", encoding="utf-8", errors="replace").read()
soup = BeautifulSoup(html, "html.parser")
cards = soup.select('[data-qa="posting PROPERTY"]')
print("Total de cards:", len(cards))

for i, card in enumerate(cards[:6]):
    print(f"\n===== CARD {i} =====")
    feat = card.select_one('[data-qa="POSTING_CARD_FEATURES"]')
    desc = card.select_one('[data-qa="POSTING_CARD_DESCRIPTION"]')
    loc = card.select_one('[data-qa="POSTING_CARD_LOCATION"]')
    addr = card.select_one(".postingLocations-module__location-address")
    print("  FEATURES:", feat.get_text(" ", strip=True) if feat else None)
    print("  LOCATION:", loc.get_text(" ", strip=True) if loc else None)
    print("  ADDRESS :", addr.get_text(" ", strip=True) if addr else None)
    gal = card.select_one('[data-qa="POSTING_CARD_GALLERY"]')
    if gal:
        img = gal.find("img")
        if img:
            print("  IMG ALT :", repr(img.get("alt")))
    d = desc.get_text(" ", strip=True) if desc else ""
    for pat in [
        r"[\d.,]+\s*m[²2]?\s*de\s*terreno",
        r"terreno[^.]{0,25}?[\d.,]+\s*m",
        r"[\d.,]+\s*m[²2]?\s*de\s*(?:area|área)?\s*constru",
        r"[\d.,]+\s*m[²2]?\s*de\s*constru",
    ]:
        for mm in re.finditer(pat, d, re.I):
            print(f"    match [{pat[:22]}]:", repr(mm.group(0)))
