"""Mede o que decide a arquitetura do deploy e confere as fontes de dados.

Sem esses numeros, qualquer plano de deploy e chute:
  - tamanho do banco (SQLite nao cabe em qualquer lugar)
  - tamanho das fotos (o item mais caro e o mais problematico legalmente)
  - quantos MB trafega por mes
  - se a pagina do ITBI ainda esta no mesmo lugar (deu 404 na sonda)

Uso: python medir_deploy.py
"""

from __future__ import annotations

import os
import sqlite3
import sys

try:
    sys.stdout.reconfigure(errors="replace")
except Exception:  # noqa: BLE001
    pass

sys.path.insert(0, "src")
from cacaimoveis import config  # noqa: E402


def mb(n: float) -> str:
    return f"{n / 1024 / 1024:,.1f} MB"


def tamanho_pasta(caminho: str) -> tuple[int, int]:
    total = 0
    n = 0
    for raiz, _, arquivos in os.walk(caminho):
        for a in arquivos:
            try:
                total += os.path.getsize(os.path.join(raiz, a))
                n += 1
            except OSError:
                pass
    return total, n


print("=" * 70)
print("1. O QUE O DEPLOY PRECISA CARREGAR")
print("=" * 70)
db = config.DB_PATH
print(f"  banco        {mb(os.path.getsize(db)):>12}   {db}")

for pasta in ("fotos", "playwright-profile", "html_ponte", "debug_html"):
    p = os.path.join(config.RAIZ, pasta)
    if os.path.isdir(p):
        t, n = tamanho_pasta(p)
        print(f"  {pasta:<12} {mb(t):>12}   {n:,} arquivos")
    else:
        print(f"  {pasta:<12} {'(nao existe)':>12}")

# o que ha dentro do banco
print()
print("  conteudo do banco (o que pesa):")
conn = sqlite3.connect(db)
for r in conn.execute("""SELECT name FROM sqlite_master
                         WHERE type='table' ORDER BY name"""):
    t = r[0]
    try:
        c = list(conn.execute(f"SELECT COUNT(*) FROM [{t}]"))[0][0]
    except Exception:  # noqa: BLE001
        continue
    if c > 1000:
        print(f"     {t:<22} {c:>10,} linhas")

print()
print("  espaco por tabela (dbstat, se disponivel):")
try:
    tot = 0
    for r in conn.execute("""SELECT name, SUM(pgsize) b FROM dbstat
                             GROUP BY name ORDER BY b DESC LIMIT 8"""):
        tot += r[1]
        print(f"     {r[0]:<22} {mb(r[1]):>12}")
    print(f"     {'(8 maiores)':<22} {mb(tot):>12}")
except Exception as e:  # noqa: BLE001
    print(f"     dbstat indisponivel: {type(e).__name__}")

print()
print("  transacoes agregadas do ITBI (podem virar arquivo estatico):")
for r in conn.execute("""SELECT COUNT(*) FROM itbi"""):
    print(f"     itbi: {r[0]:,} linhas")

print()
print("  quanto o site realmente LE (tabelas usadas pelas rotas):")
for t in ("anuncios", "fotos", "comparacoes", "valores_venais",
          "areas_oficiais", "comparacoes_detalhe"):
    try:
        c = list(conn.execute(f"SELECT COUNT(*) FROM [{t}]"))[0][0]
        print(f"     {t:<22} {c:>10,}")
    except Exception:  # noqa: BLE001
        pass
conn.close()

print()
print("=" * 70)
print("2. A PAGINA DO ITBI AINDA ESTA NO MESMO LUGAR?")
print("=" * 70)
import requests  # noqa: E402
requests.packages.urllib3.disable_warnings()
H = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
try:
    from cacaimoveis import itbi as mod_itbi  # noqa: E402
    print(f"  itbi.py tem funcao de descobrir links? "
          f"{[f for f in dir(mod_itbi) if 'descobrir' in f or 'link' in f]}")
    if hasattr(mod_itbi, "URL_PAGINA"):
        print(f"  URL_PAGINA no codigo: {mod_itbi.URL_PAGINA}")
    if hasattr(mod_itbi, "descobrir_links"):
        try:
            links = mod_itbi.descobrir_links()
            print(f"  descobrir_links() devolveu {len(links)} links:")
            for l in list(links)[:6]:
                print(f"     {str(l)[:104]}")
        except Exception as e:  # noqa: BLE001
            print(f"  descobrir_links() falhou: {type(e).__name__}: {str(e)[:80]}")
except Exception as e:  # noqa: BLE001
    print(f"  nao consegui importar itbi.py: {type(e).__name__}: {str(e)[:70]}")

print()
print("  sources declaradas em itbi.py:")
t = open(os.path.join("src", "itbi.py"), encoding="utf-8").read()
for linha in t.splitlines():
    low = linha.lower()
    if "http" in low and ("prefeitura" in low or "fazenda" in low or "itbi" in low
                          or "download" in low):
        print(f"     {linha.strip()[:104]}")
