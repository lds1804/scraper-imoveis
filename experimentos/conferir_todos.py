"""Conferencia dos itens 5, 6, 7 e 8 da lista de tarefas.

Nao confia nas caixinhas marcadas: vai no codigo e no banco e mede.

Uso: python conferir_todos.py
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

conn = sqlite3.connect(config.DB_PATH)
conn.row_factory = sqlite3.Row


def linhas(sql):
    try:
        return [tuple(r) for r in conn.execute(sql).fetchall()]
    except Exception as e:  # noqa: BLE001
        return [("ERRO", type(e).__name__, str(e)[:70])]


def n(sql):
    try:
        return list(conn.execute(sql))[0][0]
    except Exception:  # noqa: BLE001
        return -1


def src(arquivo):
    caminho = os.path.join("src", arquivo)
    if not os.path.exists(caminho):
        return None
    return open(caminho, encoding="utf-8").read()


print("=" * 74)
print("ITEM 5 - analise visual das fotos")
print("=" * 74)
tot = n("SELECT COUNT(*) FROM anuncios")
for t in ("analise_visual", "analise_fotos", "fotos_analise"):
    c = n(f"SELECT COUNT(*) FROM {t}")
    if c >= 0:
        print(f"  tabela {t}: {c:,} linhas")
        distintos = n(f"SELECT COUNT(DISTINCT anuncio_id) FROM {t}")
        print(f"  anuncios distintos analisados: {distintos:,} de {tot:,} "
              f"({distintos / tot * 100:.0f}%)")
        print(f"  anos cobertos: {linhas(f'SELECT DISTINCT ano FROM {t} ORDER BY 1')}")
        break
else:
    print("  nao achei a tabela de analise visual")
print(f"  total de anuncios: {tot:,}")
print(f"  bairros: {linhas('SELECT bairro, COUNT(*) n FROM anuncios GROUP BY 1 ORDER BY 2 DESC LIMIT 6')}")

print()
print("=" * 74)
print("ITEM 6 - valor venal por terreno + construcao")
print("=" * 74)
for f in ("valor_venal.py", "modelo_casa.py"):
    t = src(f)
    print(f"  {f}: {len(t):,} bytes")
    for chave in ("terreno", "construc", "razao", "quadra", "cep"):
        usa = [l.strip() for l in t.splitlines()
               if chave in l.lower() and not l.strip().startswith(("#", '"""'))]
        print(f"     '{chave}': {len(usa):>3} linhas de codigo")
    print()

print("  tabelas do modelo:")
for t in ("modelo_param", "preco_terreno", "preco_construcao", "depreciacao",
          "razao_venal", "valores_venais"):
    print(f"     {t:<18} {n(f'SELECT COUNT(*) FROM {t}'):>9,} linhas")

print()
print("  valores_venais - metodo usado:")
for r in linhas("SELECT metodo, COUNT(*) n FROM valores_venais GROUP BY 1 ORDER BY 2 DESC"):
    print(f"     {str(r[0])[:40]:<42} {r[1]:>6,}")

print()
print("  webapp.py usa:")
w = src("webapp.py")
for chave in ("valor_venal", "modelo_casa", "geosampa", "razao_venal",
              "estimar", "preco_terreno", "preco_construcao"):
    print(f"     '{chave}': {w.count(chave)}x")

print()
print("=" * 74)
print("ITEM 7 - refazer a conta do ITBI com terreno + construcao")
print("=" * 74)
cols = [r[1] for r in conn.execute("PRAGMA table_info(comparacoes_detalhe)")]
print(f"  colunas de comparacoes_detalhe: {cols}")
print(f"  area_terreno existe? {'area_terreno' in cols}")
totd = n("SELECT COUNT(*) FROM comparacoes_detalhe")
if "area_terreno" in cols:
    nt = n("SELECT COUNT(*) FROM comparacoes_detalhe WHERE area_terreno IS NOT NULL AND area_terreno>0")
    print(f"  area_terreno preenchida: {nt:,} de {totd:,} ({nt / max(totd, 1) * 100:.0f}%)")

print(f"  metodos gravados em comparacoes ({n('SELECT COUNT(*) FROM comparacoes'):,} linhas):")
for r in linhas("SELECT metodo, COUNT(*) n FROM comparacoes GROUP BY 1 ORDER BY 2 DESC"):
    print(f"     {str(r[0])[:36]:<38} {r[1]:>6,}")

print("  delegacao: a conta do itbi usa terreno para estimar preco?")
ci = src("comparar_itbi.py")
for chave in ("area_terreno", "terreno", "preco_terreno", "preco_construcao",
              "modelo_casa"):
    print(f"     comparar_itbi.py '{chave}': {ci.count(chave)}x")
print("  delegacao: modelo_casa estima preco (nao venal)?")
mc = src("modelo_casa.py")
for chave in ("valor_venal", "preco", "terreno", "construc"):
    print(f"     modelo_casa.py '{chave}': {mc.count(chave)}x")

print()
print("=" * 74)
print("ITEM 8 - correcao pelo IGP-M")
print("=" * 74)
ind = src("indices.py")
print(f"  indices.py ({len(ind):,} bytes) contem:")
for chave in ("igpm", "IGP-M", "189", "sgs", "bcb.gov.br", "IPCA_URL",
              "1737", "2266", "servicodados"):
    print(f"     '{chave}': {ind.count(chave)}x")
print(f"  cache dados/indices.json existe? "
      f"{os.path.exists(config.caminho('dados', 'indices.json'))}")
if os.path.exists(config.caminho("dados", "indices.json")):
    import json
    d = json.load(open(config.caminho("dados", "indices.json"), encoding="utf-8"))
    print(f"  chaves no cache: {list(d)[:6]}")

print()
print("  quem chama indices.py:")
for raiz, _, arquivos in os.walk("src"):
    for a in arquivos:
        if not a.endswith(".py"):
            continue
        t = open(os.path.join(raiz, a), encoding="utf-8").read()
        if "indices" in t and a != "indices.py":
            chamadas = [l.strip()[:72] for l in t.splitlines()
                        if "indices" in l and "import" not in l]
            print(f"     {a}: {chamadas[:3]}")

conn.close()
