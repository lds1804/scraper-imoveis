"""Mede a decisao mais importante do deploy: SEPARAR o banco de producao
do banco de trabalho.

O `imoveis.db` tem 637 MB, mas o site NAO le a maior parte dele:
  - `lotes`  (1.627.266 linhas) -> so insumo de calculo, o site nunca le
  - `itbi`   (  537.354 linhas) -> so insumo de calculo, o site nunca le
  - `cep_da_rua`, `ref_rua`, `ref_quadra` -> insumo

Se o site so precisa de um subconjunto, o banco de producao fica muito menor
-- e isso decide se cabe em Lambda (512 MB de /tmp) ou se precisa de EC2.

Tambem mede o peso do trafego de fotos: as imagens sao 5 GB e o free tier do
CloudFront da 1 TB/mes, mas 60 fotos por pagina a 1200x900 estoura rapido.
Mede quanto as MINIATURAS economizam.

Uso: python medir_site_db.py
"""

from __future__ import annotations

import io
import os
import sqlite3
import sys

try:
    sys.stdout.reconfigure(errors="replace")
except Exception:  # noqa: BLE001
    pass

sys.path.insert(0, "src")
from cacaimoveis import config  # noqa: E402

RAIZ = config.RAIZ
ORIGEM = config.DB_PATH
DESTINO = os.path.join(RAIZ, "site.db")


def mb(n: float) -> str:
    return f"{n / 1024 / 1024:,.1f} MB"


# --- 1. quais tabelas o site le de verdade ----------------------------------
print("=" * 74)
print("1. O QUE O SITE (webapp.py) LE DE VERDADE")
print("=" * 74)
campos = []
for base in ("src", "."):
    p = os.path.join(base, "webapp.py")
    if os.path.exists(p):
        campos.append(open(p, encoding="utf-8").read())
web = "\n".join(campos)
tabelas = [r[0] for r in sqlite3.connect(ORIGEM).execute(
    "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]
usadas = []
for t in tabelas:
    if t.startswith("sqlite_"):
        continue
    # conta mencoes no webapp e nos templates
    n = web.count(t)
    for raiz, _, arqs in os.walk("web"):
        for a in arqs:
            if a.endswith((".html", ".py", ".js", ".css")):
                try:
                    n += open(os.path.join(raiz, a), encoding="utf-8").read().count(t)
                except Exception:  # noqa: BLE001
                    pass
    if n:
        usadas.append(t)
print(f"  tabelas mencionadas no app: {sorted(usadas)}")

# --- 2. tamanho REAL de cada tabela (por page_count) ------------------------
print()
print("=" * 74)
print("2. TAMANHO REAL POR TABELA (via sqlite3_analyzer simplificado)")
print("=" * 74)
conn = sqlite3.connect(ORIGEM)
conn.row_factory = sqlite3.Row
# pagina do banco
ps = list(conn.execute("PRAGMA page_size"))[0][0]
pc = list(conn.execute("PRAGMA page_count"))[0][0]
print(f"  page_size={ps}  page_count={pc:,}  total={mb(ps * pc)}")

# estimativa por tabela: conta folhas dos indices + tabela
print()
print("  estimativa por tabela (o que domina):")
linhas = sqlite3.connect(ORIGEM).execute(
    "SELECT name FROM sqlite_master WHERE type='table'").fetchall()
for (t,) in sorted(linhas, key=lambda x: x[0]):
    if t.startswith("sqlite_"):
        continue
    try:
        n = list(conn.execute(f"SELECT COUNT(*) FROM [{t}]"))[0][0]
    except Exception:  # noqa: BLE001
        continue
    print(f"     {t:<22} {n:>10,} linhas")

# --- 3. gerar o site.db com so o que a tela precisa -------------------------
print()
print("=" * 74)
print("3. GERANDO site.db (so o que a tela le)")
print("=" * 74)
MANTER = [
    "anuncios", "fotos", "comparacoes", "comparacoes_detalhe",
    "valores_venais", "areas_oficiais",
]
if os.path.exists(DESTINO):
    os.remove(DESTINO)
dest = sqlite3.connect(DESTINO)
# ATTACH e obrigatorio: sem ele o CREATE TABLE AS SELECT procura a origem
# no banco de DESTINO (vazio) e falha com "no such table".
dest.execute(f"ATTACH DATABASE ? AS origem", (ORIGEM,))
for t in MANTER:
    try:
        dest.execute(
            f"CREATE TABLE [{t}] AS SELECT * FROM origem.[{t}]")
        n = list(dest.execute(f"SELECT COUNT(*) FROM [{t}]"))[0][0]
        print(f"  copiada {t:<22} {n:>9,} linhas")
    except Exception as e:  # noqa: BLE001
        print(f"  FALHOU {t}: {type(e).__name__}: {str(e)[:60]}")
# indices que o app usa (medidos: sem eles a home levava 213 s)
print()
print("  criando os indices que o app precisa:")
IDX = [
    "CREATE INDEX idx_fotos_anuncio ON fotos(anuncio_url)",
    "CREATE INDEX idx_comp_anuncio ON comparacoes(anuncio_url)",
    "CREATE INDEX idx_val_anuncio ON valores_venais(anuncio_url)",
    "CREATE INDEX idx_area_anuncio ON areas_oficiais(anuncio_url)",
    "CREATE INDEX idx_det_anuncio ON comparacoes_detalhe(anuncio_url)",
    "CREATE INDEX idx_anuncios_preco ON anuncios(preco)",
    "CREATE INDEX idx_anuncios_bairro ON anuncios(bairro)",
]
for sql in IDX:
    try:
        dest.execute(sql)
        print(f"     ok  {sql[13:50]}...")
    except Exception as e:  # noqa: BLE001
        print(f"     ERR {sql[13:50]}: {type(e).__name__}")
dest.commit()
dest.execute("VACUUM")
dest.commit()
dest.close()

tam_orig = os.path.getsize(ORIGEM)
tam_dest = os.path.getsize(DESTINO)
print()
print(f"  imoveis.db (trabalho)  {mb(tam_orig):>12}")
print(f"  site.db    (producao)  {mb(tam_dest):>12}")
print(f"  reducao: {(1 - tam_dest / tam_orig) * 100:.1f}%  "
      f"({tam_orig / tam_dest:.1f}x menor)")

conn.close()

# --- 4. peso do trafego de fotos --------------------------------------------
print()
print("=" * 74)
print("4. TRAFEGO DAS FOTOS (o que o free tier do CloudFront tem de aguentar)")
print("=" * 74)
pasta = os.path.join(RAIZ, "fotos")
tams = []
n = 0
for raiz, _, arqs in os.walk(pasta):
    for a in arqs:
        try:
            tams.append(os.path.getsize(os.path.join(raiz, a)))
            n += 1
        except OSError:
            pass
        if n > 12000:  # amostra suficiente
            break
    if n > 12000:
        break
if tams:
    tams.sort()
    media = sum(tams) / len(tams)
    print(f"  amostra: {len(tams):,} fotos")
    print(f"  media:   {media / 1024:,.0f} KB")
    print(f"  mediana: {tams[len(tams) // 2] / 1024:,.0f} KB")
    print(f"  p90:     {tams[int(len(tams) * 0.9)] / 1024:,.0f} KB")
    print()
    print("  cenario: 1 pagina com 60 cards x 1 foto de capa")
    por_pagina = media * 60
    print(f"     por carregamento de pagina: {por_pagina / 1024 / 1024:,.1f} MB")
    # 1 TB = 1024^4 bytes (nao 1024^3 -- errinho classico)
    TB = 1024 ** 4
    GB = 1024 ** 3
    print(f"     visitas que cabem em 1 TB/mes: {TB / por_pagina:,.0f}")
    print(f"     visitas que cabem em 100 GB/mes (limite da EC2): "
          f"{100 * GB / por_pagina:,.0f}")
    print()
    print("  com MINIATURA (estimado):")
    for kb in (25, 35, 50):
        por_pagina_m = kb * 1024 * 60
        print(f"     {kb} KB/foto -> {por_pagina_m / 1024 / 1024:,.1f} MB por "
              f"pagina, {TB / por_pagina_m:,.0f} visitas/TB")
    print()
    print("  quanto ocuparia a miniatura no S3 (fator ~15x menor, estimado):")
    print(f"     original: {mb(sum(tams) / len(tams) * 57451)}  (projecao)")
    print(f"     miniatura: {mb(sum(tams) / len(tams) * 57451 / 15)}  (projecao)")
