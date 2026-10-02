"""Testa o parser corrigido contra o HTML salvo."""

from scraper_browser import parse_cards

html = open("debug_playwright.html", encoding="utf-8", errors="replace").read()
anuncios = parse_cards(html, "vila-mangalot")
print("Cards parseados:", len(anuncios), "\n")

for a in anuncios[:8]:
    print(f"R$ {a.preco}")
    print(f"  titulo        : {a.titulo[:75]}")
    print(f"  endereco      : {a.endereco}")
    print(f"  quartos/banh/vagas: {a.quartos}/{a.banheiros}/{a.vagas}")
    print(f"  area_constr   : {a.area_construida} | area_terreno: {a.area_terreno}")
    print()

# Resumo
com_terreno = sum(1 for a in anuncios if a.area_terreno)
com_banh = sum(1 for a in anuncios if a.banheiros)
com_titulo = sum(1 for a in anuncios if a.titulo)
print(f"Preenchidos -> titulo: {com_titulo}/{len(anuncios)} | "
      f"banheiros: {com_banh}/{len(anuncios)} | area_terreno: {com_terreno}/{len(anuncios)}")
