"""Baixa o CADASTRO IMOBILIARIO COMPLETO de Sao Paulo (1,7 milhao de lotes).

POR QUE
-------
O projeto tinha 26.475 lotes (1,6% da cidade) porque buscava POR RUA, so para
as ruas dos anuncios. O GeoSampa expoe 1.687.969. Baixar tudo da a AREA
OFICIAL de qualquer imovel da cidade, nao so dos que ja coletamos.

IMPORTANTE — o que isto NAO resolve
-----------------------------------
Valor venal NAO esta no cadastro. As 484 camadas do GeoSampa foram
inspecionadas: `edificacao` traz altura e area de projecao, `imovel_notificado`
traz so um flag de IPTU progressivo. Nenhuma tem valor. Para o valor venal real
so existe a consulta do TVM, que tem CAPTCHA.

Medicoes que definiram o desenho
--------------------------------
- Sem geometria: 431 bytes/lote (2,7x menor que com geometria).
- `startIndex` pagina corretamente (paginas com ids diferentes).
- Total estimado: ~728 MB, ~13 min sequencial, ~2 min com 8 threads.

Uso:
    python baixar_cadastro.py              # mostra o plano, nao baixa
    python baixar_cadastro.py --baixar     # baixa de verdade
    python baixar_cadastro.py --baixar --limite 20000
"""

from __future__ import annotations

import argparse
import re
import sqlite3
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests

from cacaimoveis import config, endereco, geosampa

try:
    sys.stdout.reconfigure(errors="replace")
except Exception:  # noqa: BLE001
    pass

WFS = "http://wfs.geosampa.prefeitura.sp.gov.br/geoserver/wfs"
CAMADA = "geoportal:lote_cidadao"

# Só os campos que usamos. Pedir isto deixa a resposta 2,7x menor.
CAMPOS = ("cd_setor_fiscal,cd_quadra_fiscal,cd_lote,cd_digito_sql,"
          "cd_condominio,cd_numero_porta,tx_complemento_endereco,"
          "nm_logradouro_completo,dc_tipo_uso_imovel,cd_tipo_terreno_imovel,"
          "tx_situ_lote,qt_area_terreno,qt_area_construida")

POR_PAGINA = 5000
# 6 threads: o serviço é público e não tem rate limit declarado. Medido que
# 1000 lotes levam ~0,5s, então o gargalo é o servidor.
THREADS = 6
TENTATIVAS = 4

# o download é retomável: guarda até onde foi
_contador = {"blocos": 0, "lotes": 0}
_trava = threading.Lock()


def _texto(v) -> str:
    return ("" if v is None else str(v)).strip()


def _num(v) -> float | None:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if f > 0 else None


def _limpar(c: dict) -> dict | None:
    """Converte um lote cru em registro da tabela `lotes`."""
    logradouro = _texto(c.get("nm_logradouro_completo"))
    if not logradouro:
        return None
    numero = re.sub(r"\D", "", _texto(c.get("cd_numero_porta")))
    return {
        "setor": _texto(c.get("cd_setor_fiscal")),
        "quadra": _texto(c.get("cd_quadra_fiscal")),
        "lote": _texto(c.get("cd_lote")),
        "complemento": _texto(c.get("tx_complemento_endereco")),
        "logradouro": logradouro,
        "chave": endereco.chave_tolerante(logradouro),
        "numero": numero,
        "uso": _texto(c.get("dc_tipo_uso_imovel")),
        "tipo_terreno": _texto(c.get("cd_tipo_terreno_imovel")),
        "situacao": _texto(c.get("tx_situ_lote")),
        "area_terreno": _num(c.get("qt_area_terreno")),
        "area_construida": _num(c.get("qt_area_construida")),
    }


def _buscar(pagina: int) -> list[dict]:
    """Baixa um bloco de lotes, com recuo progressivo em caso de falha."""
    params = {
        "service": "WFS", "version": "2.0.0", "request": "GetFeature",
        "typeName": CAMADA, "outputFormat": "application/json",
        "propertyName": CAMPOS,
        "count": POR_PAGINA,
        "startIndex": pagina * POR_PAGINA,
    }
    ultimo = ""
    for i in range(TENTATIVAS):
        try:
            r = requests.get(WFS, params=params, timeout=300,
                             headers={"User-Agent": "Mozilla/5.0"})
            if r.status_code != 200:
                ultimo = f"HTTP {r.status_code}"
                time.sleep(2 * (i + 1))
                continue
            if "ExceptionReport" in r.text[:2000]:
                raise RuntimeError("WFS recusou: " + r.text[:200])
            d = r.json()
            feats = d.get("features") or []
            return [_limpar(f.get("properties") or {}) for f in feats]
        except Exception as e:  # noqa: BLE001
            ultimo = f"{type(e).__name__}: {str(e)[:60]}"
            time.sleep(2 * (i + 1))
    raise RuntimeError(f"bloco {pagina} falhou ({ultimo})")


def _gravar(conn: sqlite3.Connection, lotes: list[dict]) -> int:
    limpos = [l for l in lotes if l]
    if not limpos:
        return 0
    conn.executemany(
        """INSERT OR REPLACE INTO lotes
           (setor, quadra, lote, complemento, logradouro, chave, numero,
            bairro, uso, tipo_terreno, situacao, area_terreno,
            area_construida, atualizado_em)
           VALUES (?,?,?,?,?,?,?,NULL,?,?,?,?,?,datetime('now'))""",
        [(l["setor"], l["quadra"], l["lote"], l["complemento"],
          l["logradouro"], l["chave"], l["numero"], l["uso"],
          l["tipo_terreno"], l["situacao"], l["area_terreno"],
          l["area_construida"]) for l in limpos],
    )
    conn.commit()
    return len(limpos)


def main() -> int:
    p = argparse.ArgumentParser(
        description="Baixa o cadastro de lotes de Sao Paulo (GeoSampa)")
    p.add_argument("--baixar", action="store_true",
                   help="baixa de verdade (sem isso, só mostra o plano)")
    p.add_argument("--completar", action="store_true",
                   help="refaz SÓ os blocos que faltam (retoma de onde parou)")
    p.add_argument("--limite", type=int, default=0,
                   help="máximo de lotes (0 = todos)")
    p.add_argument("--threads", type=int, default=THREADS)
    args = p.parse_args()

    conn = sqlite3.connect(config.DB_PATH)
    conn.execute("PRAGMA busy_timeout=300000")
    geosampa.criar_tabela(conn)
    ja = conn.execute("SELECT COUNT(*) FROM lotes").fetchone()[0]

    # quantos lotes existem hoje no serviço
    r = requests.get(
        WFS, params={"service": "WFS", "version": "2.0.0",
                     "request": "GetFeature", "typeName": CAMADA,
                     "resultType": "hits"},
        timeout=120, headers={"User-Agent": "Mozilla/5.0"})
    m = re.search(r'numberMatched="(\d+)"', r.text)
    total = int(m.group(1)) if m else 0

    alvo = args.limite or total
    blocos_total = (alvo + POR_PAGINA - 1) // POR_PAGINA

    # RETOMADA. O download é sequencial por `startIndex`, então um bloco que
    # falha no meio deixa um buraco — não adianta rodar tudo de novo (gastaria
    # 13 min para preencher o que já existe). Aqui eu descubro QUAIS blocos
    # faltam comparando o que o serviço tem com o que o banco tem, e refaço só
    # eles.
    #
    # Como sei quais faltam? Cada bloco é um intervalo de `startIndex`. O
    # serviço não muda de ordem entre chamadas (ordenado por gid), então dá
    # para comparar o total esperado com o que foi gravado. O caminho mais
    # seguro é: baixar de novo os blocos e conferir se o lote já está lá
    # (INSERT OR REPLACE torna a repetição inofensiva).
    faltando = total - ja
    blocos = list(range(blocos_total))
    if args.completar and faltando > 0:
        # quantos blocos, no fim da lista, ainda não foram cobertos.
        # Como o download foi sequencial e parou nos últimos, o buraco está
        # perto do fim; refaço os últimos `n` blocos com folga.
        estimados = (faltando + POR_PAGINA - 1) // POR_PAGINA
        inicio = max(0, blocos_total - estimados - 8)   # +8 de folga
        blocos = list(range(inicio, blocos_total))
        print(f"  RETOMANDO: {faltando:,} lotes faltando "
              f"(~{estimados} blocos); vou refazer os {len(blocos)} últimos")

    print("=" * 66)
    print("CADASTRO DE LOTES DO GEOSAMPA")
    print("=" * 66)
    print(f"  lotes no serviço .........: {total:,}")
    print(f"  lotes no banco hoje ......: {ja:,}")
    print(f"  faltando .................: {faltando:,}")
    print(f"  a baixar agora ...........: {len(blocos):,} blocos "
          f"({POR_PAGINA} lotes cada)")
    print()
    print("  LEMBRETE: o cadastro NÃO tem valor venal. Ele dá ÁREA OFICIAL")
    print("  para qualquer imóvel de SP — útil para conferir metragem.")

    if not (args.baixar or args.completar):
        print()
        print("  (nada foi baixado; rode com --baixar ou --completar)")
        conn.close()
        return 0

    print()
    t0 = time.time()
    feitos = 0
    falhas = []
    with ThreadPoolExecutor(max_workers=args.threads) as pool:
        futuros = {pool.submit(_buscar, i): i for i in blocos}
        for n, fut in enumerate(as_completed(futuros), 1):
            try:
                lotes = fut.result()
                feitos += _gravar(conn, lotes)
            except Exception as e:  # noqa: BLE001
                falhas.append((futuros[fut], str(e)[:80]))
            if n % 10 == 0 or n == len(blocos):
                dt = time.time() - t0
                falta = dt / n * (len(blocos) - n)
                print(f"    bloco {n}/{len(blocos)} | {feitos:>9,} lotes gravados | "
                      f"{dt/60:>5.1f} min (faltam ~{falta/60:.0f})", flush=True)

    print()
    print(f"  pronto em {(time.time()-t0)/60:.1f} min | gravados: {feitos:,}")
    if falhas:
        print(f"  blocos com falha: {len(falhas)}")
        for i, e in falhas[:5]:
            print(f"    bloco {i}: {e}")
        print("  rode de novo com --completar para tentar só esses")
    print(f"  total na tabela lotes: "
          f"{conn.execute('SELECT COUNT(*) FROM lotes').fetchone()[0]:,}")
    conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
