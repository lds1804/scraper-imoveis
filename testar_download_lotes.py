"""Testa como baixar 1,7 milhao de lotes de forma eficiente.

O cadastro do GeoSampa tem 1.687.969 lotes. O `geosampa.py` atual busca POR
RUA (uma consulta por logradouro), o que nao serve para baixar tudo.

Antes de rodar o download completo, mede:
  1. da para paginar com `startIndex` (necessario para varrer tudo)?
  2. pedir so os campos (sem geometria) deixa mais rapido?
  3. qual o tamanho/tempo real por lote?

Uso: python testar_download_lotes.py
"""

from __future__ import annotations

import sys
import time

import requests

try:
    sys.stdout.reconfigure(errors="replace")
except Exception:  # noqa: BLE001
    pass

WFS = "http://wfs.geosampa.prefeitura.sp.gov.br/geoserver/wfs"
CAMADA = "geoportal:lote_cidadao"
CAMPOS = ("cd_setor_fiscal,cd_quadra_fiscal,cd_lote,cd_digito_sql,"
          "cd_condominio,cd_numero_porta,tx_complemento_endereco,"
          "nm_logradouro_completo,dc_tipo_uso_imovel,cd_tipo_terreno_imovel,"
          "tx_situ_lote,qt_area_terreno,qt_area_construida")


def medir(nome, **extra):
    p = {
        "service": "WFS", "version": "2.0.0", "request": "GetFeature",
        "typeName": CAMADA, "outputFormat": "application/json",
    }
    p.update(extra)
    t0 = time.time()
    try:
        r = requests.get(WFS, params=p, timeout=300)
    except Exception as e:  # noqa: BLE001
        print(f"  {nome:<44} erro {type(e).__name__}: {str(e)[:50]}")
        return None
    dt = time.time() - t0
    n = 0
    try:
        n = len((r.json().get("features") or []))
    except Exception:  # noqa: BLE001
        pass
    print(f"  {nome:<44} HTTP {r.status_code} | {n:>6} lotes | "
          f"{len(r.text):>10,} bytes | {dt:>6.1f}s")
    return r, n, dt, len(r.text)


print("=" * 70)
print("1. PAGINACAO — `startIndex` funciona?")
print("=" * 70)
a = medir("count=1000 sem startIndex", count=1000)
b = medir("count=1000 startIndex=1000", count=1000, startIndex=1000)
if a and b and a[1] and b[1]:
    ids_a = [f["properties"].get("cd_identificador") for f in a[0].json()["features"][:3]]
    ids_b = [f["properties"].get("cd_identificador") for f in b[0].json()["features"][:3]]
    print(f"  primeiros ids pagina 1: {ids_a}")
    print(f"  primeiros ids pagina 2: {ids_b}")
    print(f"  -> paginas DIFERENTES: {ids_a != ids_b}  (precisa ser True)")

print()
print("=" * 70)
print("2. SEM GEOMETRIA — só os campos que interessam")
print("=" * 70)
c = medir("count=1000 só campos (sem geometria)",
          count=1000, propertyName=CAMPOS)
if a and c:
    print(f"  COM geometria : {a[3]:>10,} bytes | {a[2]:.1f}s")
    print(f"  SEM geometria : {c[3]:>10,} bytes | {c[2]:.1f}s")
    if c[3]:
        print(f"  -> {a[3]/c[3]:.1f}x menor" if c[3] else "")

print()
print("=" * 70)
print("3. ESTIMATIVA PARA 1.687.969 LOTES")
print("=" * 70)
if c and c[1]:
    por_lote = c[3] / c[1]
    tempo_por_1000 = c[2]
    blocos = 1_687_969 / 1000
    print(f"  ~{por_lote:.0f} bytes/lote (sem geometria)")
    print(f"  total ....... ~{por_lote*1_687_969/1e6:.0f} MB")
    print(f"  blocos de 1000: {blocos:.0f}")
    print(f"  sequencial ... ~{tempo_por_1000*blocos/60:.0f} min")
    print(f"  com 8 threads ~{tempo_por_1000*blocos/60/8:.0f} min")
