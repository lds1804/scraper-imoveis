"""Detecta anúncios duplicados comparando as FOTOS (hash perceptual).

Por que não comparar as URLs: cada imobiliária sobe a **própria cópia** da
foto no CDN, então o nome do arquivo muda mesmo quando a imagem é idêntica.
Comparar `/avisos/2/30/00/24/68/16/1200x900/4347200987.jpg` com
`/avisos/2/30/25/89/45/12/1200x900/7200885116.jpg` não acusa nada — mas as
imagens podem ser a mesmíssima.

Solução: **hash perceptual** (pHash). Ele reduz a imagem a uma "impressão
digital" de 64 bits que ignora tamanho, compressão e leve mudança de brilho.
Duas fotos do mesmo imóvel reupadas dão hashes quase iguais (distância de
Hamming pequena).

Uso:
    python achar_duplicatas.py                 # relatório dos grupos
    python achar_duplicatas.py --limiar 6      # mais tolerante (default 10)
    python achar_duplicatas.py --saida dup.txt # grava em arquivo
    python achar_duplicatas.py --csv dup.csv   # também exporta CSV
"""

from __future__ import annotations

import argparse
import collections
import csv
import os
import sqlite3
import sys

import config
from storage import DB, _slug

try:
    import imagehash
    from PIL import Image
except ImportError:  # pragma: no cover
    print("Faltam dependências. Rode:")
    print("  pip install pillow imagehash")
    sys.exit(1)


# ---------------------------------------------------------------------------
# Coleta de hashes
# ---------------------------------------------------------------------------
def _fotos_por_anuncio(conn) -> dict[str, list[str]]:
    """{url do anúncio: [caminhos locais das fotos]}"""
    mapa: dict[str, list[str]] = collections.defaultdict(list)
    for r in conn.execute(
        "SELECT anuncio_url, arquivo_local FROM fotos ORDER BY id"
    ):
        caminho = r["arquivo_local"]
        if os.path.exists(caminho):
            mapa[r["anuncio_url"]].append(caminho)
    return mapa


def _hash_arquivo(caminho: str, tamanho: int):
    """pHash de um arquivo de imagem (None se não der para ler)."""
    try:
        with Image.open(caminho) as im:
            return imagehash.phash(im.convert("RGB"), hash_size=tamanho)
    except Exception:  # noqa: BLE001
        return None


def _hash_anuncios(
    fotos: dict[str, list[str]], tamanho: int, verbose: bool = True
) -> dict[str, list]:
    """Calcula o pHash de todas as fotos de cada anúncio.

    Guarda no máximo `config.DUP_MAX_HASHES_FOTO` hashes por anúncio — a foto
    de capa e as primeiras já bastam para identificar o imóvel.
    """
    total = sum(len(v) for v in fotos.values())
    feito = 0
    resultado: dict[str, list] = {}

    for i, (url, caminhos) in enumerate(fotos.items(), 1):
        hashes = []
        for caminho in caminhos[: config.DUP_MAX_HASHES_FOTO]:
            h = _hash_arquivo(caminho, tamanho)
            if h is not None:
                hashes.append(h)
            feito += 1
        if hashes:
            resultado[url] = hashes
        if verbose and i % 25 == 0:
            print(f"    {i}/{len(fotos)} anúncios ({feito}/{total} fotos)...", flush=True)

    return resultado


# ---------------------------------------------------------------------------
# Agrupamento
# ---------------------------------------------------------------------------
def _limiar_para_hash(limiar: int) -> float:
    """Converte a distância de Hamming (bits) em limiar relativo do imagehash.

    O imagehash compara `hash - hash` e devolve a fração de bits diferentes
    sobre o total. Normalizamos para manter o parâmetro em "bits", que é mais
    intuitivo: limiar 10 de 64 bits = 0.156.
    """
    return limiar / 64.0


def _agrupar(hashes: dict[str, list], limiar: int) -> list[list[str]]:
    """Agrupa anúncios que compartilham alguma foto (união-busca).

    Se A e B têm uma foto igual, e B e C têm outra, os três ficam no mesmo
    grupo — mesmo que A e C não compartilhem nada diretamente.
    """
    urls = list(hashes)
    pai = {u: u for u in urls}

    def raiz(x: str) -> str:
        while pai[x] != x:
            pai[x] = pai[pai[x]]
            x = pai[x]
        return x

    def unir(a: str, b: str) -> None:
        ra, rb = raiz(a), raiz(b)
        if ra != rb:
            pai[ra] = rb

    tol = _limiar_para_hash(limiar)

    # índice grosseiro: primeiro hash de cada anúncio -> candidatos.
    # Comparamos os anúncios dois a dois, mas só entre os que têm hashes.
    for i, ua in enumerate(urls):
        for ub in urls[i + 1 :]:
            if raiz(ua) == raiz(ub):
                continue
            if _tem_foto_parecida(hashes[ua], hashes[ub], tol):
                unir(ua, ub)

    grupos: dict[str, list[str]] = collections.defaultdict(list)
    for u in urls:
        grupos[raiz(u)].append(u)
    return [g for g in grupos.values() if len(g) > 1]


def _tem_foto_parecida(ha: list, hb: list, tol: float) -> bool:
    """True se alguma foto de A é parecida com alguma de B."""
    for a in ha:
        for b in hb:
            if (a - b) <= tol * len(a.hash.flatten()):
                return True
    return False


# ---------------------------------------------------------------------------
# Relatório
# ---------------------------------------------------------------------------
def main() -> None:
    p = argparse.ArgumentParser(description="Detecta anúncios duplicados pelas fotos")
    p.add_argument("--limiar", type=int, default=config.DUP_LIMIAR_BITS,
                   help=f"bits diferentes tolerados (default {config.DUP_LIMIAR_BITS})")
    p.add_argument("--saida", default="", help="arquivo de texto (UTF-8)")
    p.add_argument("--csv", default="", help="exporta também em CSV")
    p.add_argument("--quieto", action="store_true")
    p.add_argument("--marcar", action="store_true",
                   help="grava os grupos no banco (marca, NÃO remove nada)")
    args = p.parse_args()

    conn = sqlite3.connect(config.DB_PATH)
    conn.row_factory = sqlite3.Row

    print(f"Banco : {config.DB_PATH}")
    fotos = _fotos_por_anuncio(conn)
    total_fotos = sum(len(v) for v in fotos.values())
    print(f"Anúncios com fotos em disco: {len(fotos)} | fotos: {total_fotos}")
    print(f"Calculando hashes (limiar={args.limiar} bits)...")

    hashes = _hash_anuncios(fotos, config.DUP_HASH_SIZE, verbose=not args.quieto)
    print(f"Hashes calculados para {len(hashes)} anúncios.\n")

    grupos = _agrupar(hashes, args.limiar)

    # dados dos anúncios para o relatório
    info = {
        r["url"]: dict(r)
        for r in conn.execute(
            "SELECT url, titulo, bairro, rua, preco, endereco FROM anuncios"
        )
    }
    n_fotos = {
        r["anuncio_url"]: r["n"]
        for r in conn.execute(
            "SELECT anuncio_url, COUNT(*) n FROM fotos GROUP BY anuncio_url"
        )
    }
    conn.close()

    L: list[str] = []
    L.append(f"GRUPOS DE ANÚNCIOS DUPLICADOS (limiar {args.limiar} bits)")
    L.append(f"anúncios com foto: {len(hashes)} | grupos: {len(grupos)}")
    L.append("")

    grupos.sort(key=lambda g: (-len(g), info.get(g[0], {}).get("bairro") or ""))

    for n_grupo, g in enumerate(grupos, 1):
        a0 = info.get(g[0], {})
        L.append("=" * 78)
        L.append(f"GRUPO {n_grupo} — {len(g)} anúncios | {a0.get('bairro') or '?'}"
                 f" | {a0.get('rua') or a0.get('endereco') or '?'}")
        precos = [info[u].get("preco") for u in g if info.get(u, {}).get("preco")]
        if precos:
            L.append(f"  preços: R$ {min(precos):,.0f} a R$ {max(precos):,.0f}")
        L.append("")
        for u in sorted(g, key=lambda x: info.get(x, {}).get("preco") or 0):
            d = info.get(u, {})
            preco = f"R$ {d['preco']:>11,.0f}" if d.get("preco") else "R$       ?    "
            L.append(f"  {preco}  {n_fotos.get(u, 0):>2d} fotos  {(d.get('titulo') or '')[:44]}")
            L.append(f"               {u}")
        L.append("")

    texto = "\n".join(L)

    if args.saida:
        with open(args.saida, "w", encoding="utf-8") as f:
            f.write(texto)
        print(f"Relatório salvo em {args.saida}")
    else:
        print(texto)

    if args.csv:
        with open(args.csv, "w", encoding="utf-8", newline="") as f:
            w = csv.writer(f)
            w.writerow(["grupo", "qtd", "preco", "titulo", "bairro", "rua", "url"])
            for n_grupo, g in enumerate(grupos, 1):
                for u in g:
                    d = info.get(u, {})
                    w.writerow([
                        n_grupo, len(g), d.get("preco"), d.get("titulo"),
                        d.get("bairro"), d.get("rua"), u,
                    ])
        print(f"CSV salvo em {args.csv}")

    if args.marcar:
        # escolhe o "principal" de cada grupo: o que tem MAIS FOTOS
        # (é o mais completo) e, em empate, o de MENOR PREÇO.
        pacote = []
        for g in grupos:
            precos = [info[u]["preco"] for u in g if info.get(u, {}).get("preco")]
            def chave(u: str):
                return (-n_fotos.get(u, 0), info.get(u, {}).get("preco") or 1e12)
            principal = min(g, key=chave)
            pacote.append({
                "membros": g,
                "principal": principal,
                "menor_preco": min(precos) if precos else None,
            })

        db = DB()
        marcados = db.marcar_duplicatas(pacote)
        db.close()
        print(f"\nMarcados {marcados} anúncios em {len(pacote)} grupos (nada foi removido).")
        print("Use `python listar_duplicatas.py` para ver o resultado.")


if __name__ == "__main__":
    main()
