"""Inspeciona o HTML salvo para descobrir os seletores reais dos cards."""


from _bootstrap import iniciar

iniciar()  # poe src/ no sys.path e fixa a raiz como diretorio de trabalho

import re
from collections import Counter

html = open("debug_playwright.html", encoding="utf-8").read()

print("=" * 70)
print("LINKS .html (que parecem anúncios individuais)")
print("=" * 70)
hrefs = re.findall(r'href="([^"]+\.html)"', html)
# URLs de anúncio costumam ter um ID numérico no final
anon = [h for h in set(hrefs) if re.search(r"\d{6,}", h)]
for h in sorted(anon)[:30]:
    print(" ", h)

print()
print("=" * 70)
print("ATRIBUTOS data-qa / data-test / class contendo 'posting' ou 'card'")
print("=" * 70)
for pat in [
    r'data-qa="([^"]+)"',
    r'data-test="([^"]+)"',
    r'class="([^"]*(?:posting|card|listing)[^"]*)"',
]:
    found = Counter(re.findall(pat, html))
    for k, v in found.most_common(20):
        print(f"  {k!r}  -> {v}x")
    print("  ---")

print()
print("=" * 70)
print("PRIMEIRO CARD encontrado (trecho)")
print("=" * 70)
m = re.search(r'<[^>]+data-qa="[^"]*posting[^"]*"[^>]*>', html)
if m:
    start = m.start()
    print(html[start:start + 3000])
else:
    print("Não achei data-qa com 'posting'.")
