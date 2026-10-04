"""Confere os grupos de duplicatas ANTES de marcar no banco.

Por que existe: `achar_duplicatas.py` agrupa por semelhança de FOTO, e foto
semelhante não prova que o imóvel é o mesmo. Medido nesta base: 33 dos 1.054
grupos juntam anúncios de RUAS DIFERENTES — o que é impossível para o mesmo
imóvel. Marcar isso como duplicata envenenaria a interface em silêncio.

Duas causas foram identificadas e medidas:

  1. ENCADEAMENTO. A união-busca liga A-B e B-C, então A e C acabam no mesmo
     grupo mesmo sem se parecer. Se o "hub" (B) tiver uma foto comum, ele
     costura ruas diferentes. Medido num grupo: de 12 pares que casaram, 6
     ligavam ruas diferentes, e o hub ligava 2 ruas quase sozinho.

  2. FOTO GENÉRICA. Existem fotos com o MESMO pHash (0 bits de diferença) em
     anúncios de ruas diferentes. Comparar md5 descarta cópia de arquivo
     (todos os md5 são distintos), mas o pHash é igual: são imagens
     visualmente idênticas publicadas por imobiliárias diferentes.

Uso:
    python verificar_duplicatas.py             # relatório, nao grava nada
    python verificar_duplicatas.py --detalhar 5
"""

from __future__ import annotations

import argparse
import os
import re
import sqlite3
import sys

import config
import endereco

try:
    sys.stdout.reconfigure(errors="replace")
    sys.stderr.reconfigure(errors="replace")
except Exception:  # noqa: BLE001
    pass


def ler_grupos(caminho: str) -> list[dict]:
    """Le o relatorio gerado por `achar_duplicatas.py --saida`."""
    with open(caminho, encoding="utf-8", errors="replace") as fh:
        txt = fh.read()
    partes = re.split(r"\nGRUPO (\d+) ", txt)
    grupos = []
    for i in range(1, len(partes), 2):
        corpo = partes[i + 1]
        m = re.match(r"[\W]*(\d+) an.ncios \| (.*?) \| (.*)$",
                     corpo.splitlines()[0])
        grupos.append({
            "n": int(partes[i]),
            "qtd": int(m.group(1)) if m else 0,
            "urls": re.findall(r"(https?://\S+)", corpo),
        })
    return grupos


def main() -> int:
    p = argparse.ArgumentParser(
        description="Confere os grupos de duplicatas antes de marcar")
    p.add_argument("--arquivo", default="duplicatas.txt")
    p.add_argument("--detalhar", type=int, default=0,
                   help="mostra os N maiores grupos em conflito")
    args = p.parse_args()

    caminho = os.path.join(config.RAIZ, args.arquivo)
    if not os.path.exists(caminho):
        print(f"nao achei {caminho}")
        print("rode antes: achar_duplicatas.py --saida duplicatas.txt")
        return 1

    grupos = ler_grupos(caminho)
    conn = sqlite3.connect(config.DB_PATH)
    conn.row_factory = sqlite3.Row

    # rua normalizada de cada anuncio (uma consulta so, em memoria)
    rua_de = {r["url"]: endereco.chave_tolerante(r["rua"] or "")
              for r in conn.execute("SELECT url, rua FROM anuncios")}

    total_ads = conn.execute("SELECT COUNT(*) FROM anuncios").fetchone()[0]

    limpos, conflitos, sem_dado = [], [], []
    for g in grupos:
        chaves = {rua_de[u] for u in g["urls"] if rua_de.get(u)}
        if not chaves:
            sem_dado.append((g, chaves))
        elif len(chaves) == 1:
            limpos.append((g, chaves))
        else:
            conflitos.append((g, chaves))

    print("=" * 68)
    print("VERIFICACAO DOS GRUPOS DE DUPLICATAS (nada foi gravado)")
    print("=" * 68)
    print(f"  grupos lidos .................: {len(grupos):,}")
    print(f"  anuncios na base .............: {total_ads:,}")
    print()
    print(f"  CONSISTENTES (1 endereco) ....: {len(limpos):>5,} grupos"
          f"  ({sum(g['qtd'] for g, _ in limpos):,} anuncios)")
    print(f"  EM CONFLITO (>1 endereco) ....: {len(conflitos):>5,} grupos"
          f"  ({sum(g['qtd'] for g, _ in conflitos):,} anuncios)")
    print(f"  SEM endereco no banco ........: {len(sem_dado):>5,} grupos")

    if conflitos:
        ads = sum(g["qtd"] for g, _ in conflitos)
        print()
        print(f"  -> marcar tudo colocaria {ads:,} anuncios em grupo errado"
              f" ({100*ads/total_ads:.1f}% da base)")
        print("     o desenho correto e marcar os CONSISTENTES e deixar os")
        print("     conflitantes de fora, listados para revisao manual.")

    if args.detalhar and conflitos:
        print()
        print("=" * 68)
        print(f"OS {args.detalhar} MAIORES GRUPOS EM CONFLITO")
        print("=" * 68)
        for g, chaves in sorted(conflitos, key=lambda x: -x[0]["qtd"])[:args.detalhar]:
            print(f"\n  grupo {g['n']} — {g['qtd']} anuncios, "
                  f"{len(chaves)} enderecos:")
            por_rua = {}
            for u in g["urls"]:
                por_rua.setdefault(rua_de.get(u) or "(sem rua)", []).append(u)
            for chave, urls in por_rua.items():
                print(f"    {chave or '(vazio)'}")
                for u in urls[:3]:
                    print(f"      {u[:76]}")
                if len(urls) > 3:
                    print(f"      ... e mais {len(urls)-3}")

    conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
