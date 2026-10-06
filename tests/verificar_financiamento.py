"""Verifica a detecção de financiamento e os bairros novos.

Uso: python verificar_financiamento.py
"""

from __future__ import annotations

from _bootstrap import iniciar

iniciar()  # poe src/ no sys.path e fixa a raiz como diretorio de trabalho

import sqlite3

import config
from scraper_browser import Anuncio, calcular_financiamento, parse_cards

linhas: list[str] = []


def out(t: str = "") -> None:
    linhas.append(t)


# ---------------------------------------------------------------------------
out("=== 1) Bairros na lista ===")
out(f"  total: {len(config.BAIRROS)}")
for b in config.BAIRROS:
    out(f"    [{b.grupo:9s}] {b.slug}")
out("")
out("  city-america na lista?        " + str(any(b.slug == "city-america" for b in config.BAIRROS)))
out("  parque-maria-domitila na lista? " + str(any(b.slug == "parque-maria-domitila" for b in config.BAIRROS)))

# ---------------------------------------------------------------------------
out("\n=== 2) calcular_financiamento (casos reais) ===")
casos = [
    # (descricao, pills, esperado)
    ("", "Melhor financiamento", True),
    ("O imóvel aceita financiamento e está aberto a propostas", "", True),
    ("Casa financiável pelos principais bancos", "", True),
    ("Não aceita financiamento. Somente à vista.", "", False),
    ("Excelente casa com área gourmet e ótima localização", "", None),
    ("Simule seu financiamento com as melhores taxas", "", None),
    ("Aceita financiamento bancário", "", True),
    ("Imóvel sujeito a financiamento", "", True),
    ("Aceita permuta. Financiamento a combinar com o proprietário.", "", True),
]
ok = 0
for desc, pills, esperado in casos:
    a = Anuncio(url="x", descricao=desc)
    calcular_financiamento(a, pills)
    bate = a.aceita_financiamento == esperado
    ok += bate
    out(f"  [{'OK ' if bate else 'ERRO'}] {str(a.aceita_financiamento):5s} (esperado {str(esperado):5s})  {desc[:52]!r}{' +pill' if pills else ''}")
out(f"  -> {ok}/{len(casos)} casos corretos")

# ---------------------------------------------------------------------------
out("\n=== 3) parse_cards captura a pílula da listagem ===")
html = """
<html><body>
<div data-qa="posting PROPERTY" data-to-posting="/p/a.html">
  <div data-qa="POSTING_CARD_PRICE">R$ 500.000</div>
  <div data-qa="POSTING_CARD_FEATURES">100 m² tot. 3 quartos 2 ban.</div>
  <div class="postingLocations-module__location-address">Vila Mangalot, São Paulo</div>
  <div class="pills-module"> <span class="pills-module__pill-item-span">Melhor financiamento</span></div>
</div>
<div data-qa="posting PROPERTY" data-to-posting="/p/b.html">
  <div data-qa="POSTING_CARD_PRICE">R$ 400.000</div>
  <div data-qa="POSTING_CARD_FEATURES">120 m² tot. 3 quartos 2 ban.</div>
  <div class="postingLocations-module__location-address">Vila Mangalot, São Paulo</div>
  <div class="pills-module"> <span class="pills-module__pill-item-span">Churrasqueira</span></div>
</div>
</body></html>
"""
cards = parse_cards(html, "vila-mangalot")
for a in cards:
    out(f"  {a.titulo or a.url}: aceita_financiamento={a.aceita_financiamento}")
out(f"  1º deve ser True, 2º None -> {'OK' if cards[0].aceita_financiamento is True and cards[1].aceita_financiamento is None else 'ERRO'}")

# ---------------------------------------------------------------------------
out("\n=== 4) Coluna no banco ===")
conn = sqlite3.connect(config.DB_PATH)
cols = {r[1] for r in conn.execute("PRAGMA table_info(anuncios)")}
conn.close()
tem = "aceita_financiamento" in cols
out(f"  coluna aceita_financiamento existe? {tem}")
out(f"  colunas: {sorted(c for c in cols if 'financ' in c or 'detalhe' in c)}")

texto = "\n".join(linhas)
with open("verificacao_financ.txt", "w", encoding="utf-8") as f:
    f.write(texto)
print("Relatorio salvo em verificacao_financ.txt")
