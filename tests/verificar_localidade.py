"""Verifica a lógica de localidade SEM acessar a rede.

Testa:
  1. `montar_url` no formato canônico (cidade antes do bairro)
  2. `e_de_sao_paulo` com endereços reais do banco
  3. `parse_cards` descartando anúncios de outras cidades

Uso: python verificar_localidade.py
"""

from __future__ import annotations


from _bootstrap import iniciar
iniciar()  # poe src/ no sys.path e fixa a raiz como diretorio de trabalho

import sqlite3

import config
from scraper_browser import (
    bairro_confere,
    bairro_do_endereco,
    cidade_do_endereco,
    e_de_sao_paulo,
    e_fallback_nacional,
    montar_url,
    parse_cards,
)
from scraper_browser import Anuncio

linhas: list[str] = []


def out(t: str = "") -> None:
    linhas.append(t)


# ---------------------------------------------------------------------------
out("=== 1) montar_url (formato: tipo-operacao-BAIRRO-cidade-uf) ===")
casos = [
    ("vila-mangalot", 1),
    ("parque-sao-domingos", 1),
    ("pirituba", 1),
    ("lapa", 3),
]
for slug, pg in casos:
    url = montar_url(slug, pg)
    ok = f"-{slug}-{config.CIDADE}-{config.UF}" in url
    out(f"  [{'OK' if ok else 'ERRO'}] {url}")

# ---------------------------------------------------------------------------
out("\n=== 2) e_fallback_nacional (títulos reais do site) ===")
titulos = [
    ("3.217 Casas à venda no Parque São Domingos, São Paulo", False),
    ("12.815 Casas à venda em Pirituba, São Paulo", False),
    ("381.953 Casas à venda em São Paulo, SP ou Vila Mangalot", False),
    ("1.672.720 Casas à venda no Brasil", True),
]
for t, esperado in titulos:
    r = e_fallback_nacional(t)
    out(f"  [{'OK ' if r == esperado else 'ERRO'}] fallback={r}  {t[:62]}")

# ---------------------------------------------------------------------------
out("\n=== 3) bairro_confere (bate com o bairro buscado?) ===")
casos_bairro = [
    ("Vila Mangalot, São Paulo", "vila-mangalot", True),
    ("Parque São Domingos, São Paulo", "parque-sao-domingos", True),
    ("Parque São Domingos, São Paulo", "parque-sao-domingo", True),
    ("Pirituba, São Paulo", "pirituba", True),
    ("Brooklin Velho, São Paulo", "vila-mangalot", False),
    ("Jardim Gonzaga, São Paulo", "parque-sao-domingos", False),
    ("Ceilândia, Ceilândia", "parque-sao-domingos", False),
]
for end, buscado, esperado in casos_bairro:
    r = bairro_confere(end, buscado)
    out(f"  [{'OK ' if r == esperado else 'ERRO'}] confere={r!s:5s} esperado={esperado!s:5s}  {end:34s} vs {buscado}")

# ---------------------------------------------------------------------------
out("\n=== 4) e_de_sao_paulo com endereços reais ===")
exemplos = [
    "Vila Mangalot, São Paulo",
    "Parque São Domingos, São Paulo",
    "Vila Nova, Campinas",
    "Méier, Rio de Janeiro",
    "Capão da Imbuia, Curitiba",
    "Piratininga, Osasco",
    "",
]
for end in exemplos:
    a = Anuncio(url="http://x", endereco=end)
    sp = e_de_sao_paulo(a)
    out(f"  [{'SP  ' if sp else 'FORA'}] {end!r}")

# ---------------------------------------------------------------------------
out("\n=== 5) Contra o banco real ===")
conn = sqlite3.connect(f"file:{config.DB_PATH}?mode=ro", uri=True)
conn.row_factory = sqlite3.Row
rows = conn.execute("SELECT bairro, endereco FROM anuncios").fetchall()
conn.close()

total = len(rows)
mantidos = sum(1 for r in rows if e_de_sao_paulo(Anuncio(url="x", endereco=r["endereco"] or "")))
out(f"  total no banco : {total}")
out(f"  em São Paulo   : {mantidos}")
out(f"  de fora        : {total - mantidos}")

# ---------------------------------------------------------------------------
out("\n=== 6) parse_cards com HTML sintético ===")
html_teste = """
<html><body>
<div data-qa="posting PROPERTY" data-to-posting="/p/sp-1.html">
  <div data-qa="POSTING_CARD_PRICE">R$ 500.000</div>
  <div data-qa="POSTING_CARD_FEATURES">100 m² tot. 3 quartos 2 ban.</div>
  <div class="postingLocations-module__location-address">Vila Mangalot, São Paulo</div>
</div>
<div data-qa="posting PROPERTY" data-to-posting="/p/ctba-1.html">
  <div data-qa="POSTING_CARD_PRICE">R$ 400.000</div>
  <div data-qa="POSTING_CARD_FEATURES">120 m² tot. 3 quartos 2 ban.</div>
  <div class="postingLocations-module__location-address">Capão da Imbuia, Curitiba</div>
</div>
<div data-qa="posting PROPERTY" data-to-posting="/p/camp-1.html">
  <div data-qa="POSTING_CARD_PRICE">R$ 450.000</div>
  <div data-qa="POSTING_CARD_FEATURES">110 m² tot. 2 quartos 2 ban.</div>
  <div class="postingLocations-module__location-address">Vila Nova, Campinas</div>
</div>
</body></html>
"""
com_filtro = parse_cards(html_teste, "vila-mangalot", filtrar_cidade=True)
sem_filtro = parse_cards(html_teste, "vila-mangalot", filtrar_cidade=False)
out(f"  sem filtro : {len(sem_filtro)} anúncios (esperado 3)")
out(f"  com filtro : {len(com_filtro)} anúncios (esperado 1)")
for a in com_filtro:
    out(f"    mantido: {a.endereco}")

# ---------------------------------------------------------------------------
out("\n=== 5) bairros-alvo configurados ===")
out(f"  total   : {len(config.BAIRROS)}")
for b in config.BAIRROS:
    apel = f"  apelidos={list(b.apelidos)}" if b.apelidos else ""
    out(f"    [{b.grupo:9s}] {b.nome:26s} slug={b.slug}{apel}")

texto = "\n".join(linhas)
with open("verificacao.txt", "w", encoding="utf-8") as f:
    f.write(texto)
print("Relatorio salvo em verificacao.txt")
