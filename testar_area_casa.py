"""Testa a correcao de QUAL area o anuncio publica (terreno x construcao).

O defeito: o anuncio publica UM numero de metragem e o cadastro tem DOIS.
O codigo comparava sempre com a CONSTRUCAO, entao acusava divergencia quando
o anuncio tinha publicado o TERRENO. Medido: 15,2% das conferencias eram
falso positivo nosso.

Uso: python testar_area_casa.py
"""

from __future__ import annotations

import os
import sqlite3
import sys

try:
    sys.stdout.reconfigure(errors="replace")
except Exception:  # noqa: BLE001
    pass

_RAIZ = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _RAIZ)
sys.path.insert(0, os.path.join(_RAIZ, "src"))
os.chdir(_RAIZ)

import config  # noqa: E402
import referencia_geosampa as rg  # noqa: E402
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


print("=" * 72)
print("1. _casa_com(): com QUAL area o numero do anuncio casa")
print("=" * 72)
F = rg.FAIXA_OK / 100
casos = [
    # (anuncio, construida, terreno, campo esperado)
    (200, 60, 200, "terreno"),      # o caso real: Rua Alvares Otero 37
    (434, 145, 434, "terreno"),     # Rua Itapejara 86
    (200, 200, 200, "ambos"),       # terreno = construcao
    (100, 100, 300, "construcao"),  # o caso normal
    (900, 100, 130, "nenhum"),      # nao casa com nada
    (None, 100, 200, "sem dado"),   # anuncio nao informou
]
for ad, co, te, esperado in casos:
    _, campo = rg._casa_com(ad, co, te)
    checar(f"anuncio={ad} constr={co} terr={te} -> {esperado}",
           campo == esperado, f"obtido: {campo}")

print()
print("  a zona morta de +-15% vale para os dois lados:")
_, campo = rg._casa_com(100 * (1 + F * 0.9), 100, 300)
checar("dentro da faixa casa com a construcao", campo == "construcao", campo)
_, campo = rg._casa_com(100 * (1 + F * 2), 100, 300)
checar("fora da faixa nao casa", campo == "nenhum", campo)

print()
print("=" * 72)
print("2. _grau_da_area(): o que a interface mostra")
print("=" * 72)
# anuncio que publicou o TERRENO: era "divergente", agora compativel
area_terreno = {"dif_pct": 233.0, "area_casa": "terreno"}
checar("anuncio = terreno NAO aparece como divergente",
       webapp._grau_da_area(area_terreno) == "compativel",
       webapp._grau_da_area(area_terreno))
area_ambos = {"dif_pct": 0.0, "area_casa": "ambos"}
checar("anuncio = os dois -> compativel",
       webapp._grau_da_area(area_ambos) == "compativel")
# divergencia REAL continua aparecendo
area_real = {"dif_pct": 250.0, "area_casa": "nenhum"}
checar("divergencia REAL continua divergente",
       webapp._grau_da_area(area_real) == "divergente",
       webapp._grau_da_area(area_real))
area_const = {"dif_pct": 8.0, "area_casa": "construcao"}
checar("anuncio = construcao dentro da faixa -> compativel",
       webapp._grau_da_area(area_const) == "compativel")
checar("sem area -> sem dado", webapp._grau_da_area(None) == "sem dado")
checar("compatibilidade com registro antigo (sem area_casa)",
       webapp._grau_da_area({"dif_pct": 5.0}) == "compativel")

print()
print("=" * 72)
print("3. O BANCO foi recalculado? (coluna area_casa preenchida)")
print("=" * 72)
conn = sqlite3.connect(config.DB_PATH)
conn.row_factory = sqlite3.Row
cols = {r[1] for r in conn.execute("PRAGMA table_info(areas_oficiais)")}
checar("a coluna area_casa existe", "area_casa" in cols)

tot = list(conn.execute("SELECT COUNT(*) FROM areas_oficiais"))[0][0]
n_campo = list(conn.execute(
    "SELECT COUNT(*) FROM areas_oficiais WHERE area_casa IS NOT NULL"))[0][0]
checar("area_casa preenchida em (quase) todas as linhas",
       n_campo >= tot - 5, f"{n_campo:,} de {tot:,}")

vals = {r[0] for r in conn.execute(
    "SELECT DISTINCT area_casa FROM areas_oficiais")}
checar("os valores sao os esperados",
       vals <= {"construcao", "terreno", "ambos", "nenhum", "sem dado", None},
       str(vals))

n_terr = list(conn.execute(
    "SELECT COUNT(*) FROM areas_oficiais WHERE area_casa='terreno'"))[0][0]
checar("ha casos de anuncio publicando o terreno (o achado)",
       n_terr > 0, f"{n_terr:,} anúncios")

print()
print("=" * 72)
print("4. Um anuncio real abre e mostra a explicacao")
print("=" * 72)
linha = conn.execute(
    """SELECT o.anuncio_url, o.area_casa, o.area_anuncio, o.area_oficial,
              o.area_terreno_oficial
       FROM areas_oficiais o WHERE o.area_casa='terreno' LIMIT 1""").fetchone()
conn.close()
if linha:
    import urllib.parse
    with webapp.app.test_client() as c:
        r = c.get("/anuncio/" + urllib.parse.quote(linha["anuncio_url"],
                                                   safe=""))
        html = r.get_data(as_text=True)
    checar("a pagina do anuncio abre", r.status_code == 200,
           f"HTTP {r.status_code}")
    # o HTML vem com acento: procurar pelo trecho sem tags e SEM acento daria
    # falso negativo (foi o que aconteceu na 1a versao deste teste)
    texto = html.replace("<b>", "").replace("</b>", "")
    checar("o HTML diz que o anuncio publica o TERRENO",
           "publica a área do TERRENO" in texto)
    checar("o HTML explica que nao e erro do anuncio",
           "não é erro do anúncio" in texto)
    checar("o HTML mostra a construcao do cadastro",
           "prefeitura registra como construído" in texto)

print()
print("=" * 72)
print(f"{OK} passaram, {FALHA} falharam")
if falhas:
    for f in falhas:
        print(f"  - {f}")
    sys.exit(1)
print("TODAS AS VERIFICACOES PASSARAM")
