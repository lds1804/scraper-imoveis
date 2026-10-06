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
import time

from cacaimoveis import config, endereco
from cacaimoveis.storage import DB

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
    """Converte a distância de Hamming (bits) no limiar usado na comparação.

    ATENÇÃO à semântica de `hash - hash` nesta versão do imagehash (4.3.2):
    ela devolve a **quantidade de bits diferentes** (um inteiro de 0 a 64),
    NÃO uma fração. Conferido na mão:

        (a - b) = 28      e     bin(int(a) ^ int(b)).count('1') = 28

    O comentário aqui dizia o contrário ("devolve a fração"), e a conta
    abaixo — dividir por 64 e multiplicar por 64 na hora de comparar — acaba
    se anulando, então o resultado sempre esteve certo por acidente.

    O risco fica registrado porque é silencioso: se alguém ajustar a
    comparação para casar com o comentário antigo (`(a-b) <= tol`, supondo
    fração), então `(a-b)=28` passaria a ser comparado com `0.156` e o teste
    ficaria sempre verdadeiro. Toda foto casaria com toda foto, os 4.536
    anúncios virariam um único grupo e **nenhum erro apareceria**.
    """
    return limiar / 64.0


def _agrupar(hashes: dict[str, list], limiar: int,
             verbose: bool = True) -> list[list[str]]:
    """Agrupa anúncios que compartilham alguma foto (união-busca).

    Se A e B têm uma foto igual, e B e C têm outra, os três ficam no mesmo
    grupo — mesmo que A e C não compartilhem nada diretamente.

    A comparação é par a par, então o custo cresce com o QUADRADO do número
    de anúncios: 4.536 anúncios são 10,3 milhões de pares. Antes esta etapa
    ficava ~11 minutos sem imprimir nada, o que parecia travamento. Agora
    informa o progresso a cada 5%.
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

    # Comparação dois a dois, só entre quem tem hash. O laço é O(n²) — é o
    # gargalo do script —, por isso o progresso por tempo decorrido.
    total = len(urls)
    t0 = time.time()
    passo = max(1, total // 20)      # informa 20 vezes ao longo do trabalho
    for i, ua in enumerate(urls):
        if verbose and i % passo == 0 and i:
            feito = i / total
            resta = (time.time() - t0) / feito * (1 - feito) if feito else 0
            print(f"    comparando {i}/{total} anúncios "
                  f"({100*feito:.0f}%) — faltam ~{resta/60:.1f} min",
                  flush=True)
        for ub in urls[i + 1:]:
            if raiz(ua) == raiz(ub):
                continue
            if _tem_foto_parecida(hashes[ua], hashes[ub], tol):
                unir(ua, ub)

    grupos: dict[str, list[str]] = collections.defaultdict(list)
    for u in urls:
        grupos[raiz(u)].append(u)
    resultado = [g for g in grupos.values() if len(g) > 1]
    if verbose:
        print(f"    agrupamento concluído em {(time.time()-t0)/60:.1f} min "
              f"| {len(resultado):,} grupos", flush=True)
    return resultado


def _tem_foto_parecida(ha: list, hb: list, tol: float) -> bool:
    """True se alguma foto de A é parecida com alguma de B."""
    for a in ha:
        for b in hb:
            if (a - b) <= tol * len(a.hash.flatten()):
                return True
    return False


def _grupos_do_csv(caminho: str) -> list[list[str]]:
    """Lê os grupos de um CSV gerado antes por `--csv`.

    Existe para não repetir o cálculo quando só falta marcar: a comparação
    par a par leva ~11 minutos nesta base, e o CSV já guarda o resultado
    completo (grupo, url). Assim `--reaproveitar` gasta segundos.
    """
    por_grupo: dict[str, list[str]] = collections.defaultdict(list)
    with open(caminho, encoding="utf-8") as fh:
        for linha in csv.DictReader(fh):
            if linha.get("url"):
                por_grupo[linha.get("grupo") or "?"].append(linha["url"])
    return [g for g in por_grupo.values() if len(g) > 1]


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
    p.add_argument("--reaproveitar", default="", metavar="CSV",
                   help="usa um CSV de duplicatas já gerado, sem recalcular")
    p.add_argument("--marcar", action="store_true",
                   help="grava os grupos no banco (marca, NÃO remove nada)")
    args = p.parse_args()

    conn = sqlite3.connect(config.DB_PATH)
    conn.row_factory = sqlite3.Row

    print(f"Banco : {config.DB_PATH}")
    if args.reaproveitar:
        grupos = _grupos_do_csv(args.reaproveitar)
        n_urls = sum(len(g) for g in grupos)
        print(f"Reaproveitando {args.reaproveitar}: {len(grupos):,} grupos, "
              f"{n_urls:,} anúncios (sem recalcular)")
        hashes = {u: [] for g in grupos for u in g}
        fotos = {}
    else:
        fotos = _fotos_por_anuncio(conn)
        total_fotos = sum(len(v) for v in fotos.values())
        print(f"Anúncios com fotos em disco: {len(fotos)} | fotos: {total_fotos}")
        print(f"Calculando hashes (limiar={args.limiar} bits)...")

        hashes = _hash_anuncios(fotos, config.DUP_HASH_SIZE, verbose=not args.quieto)
        print(f"Hashes calculados para {len(hashes)} anúncios.")
        if len(hashes) > 1:
            pares = len(hashes) * (len(hashes) - 1) // 2
            print(f"Agrupando: {pares:,} pares a comparar "
                  f"(cresce com o quadrado do número de anúncios)...", flush=True)

        grupos = _agrupar(hashes, args.limiar, verbose=not args.quieto)

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
        # ------------------------------------------------------------------
        # TRAVA DE SEGURANÇA antes de gravar.
        #
        # Foto parecida NÃO prova que o imóvel é o mesmo, e a união-busca
        # encadeia: se A casa com B e B casa com C, os três entram no mesmo
        # grupo mesmo que A e C não se pareçam. Um anúncio com foto genérica
        # (ou comum a um lançamento) costura ruas diferentes e contamina o
        # grupo inteiro.
        #
        # Medido nesta base: de 1.054 grupos, 33 juntavam 2+ RUAS diferentes
        # (141 anúncios). Como imóvel em ruas diferentes não pode ser o mesmo
        # imóvel, esses grupos são descartados — não marcados.
        #
        # Vale notar que 949 dos 1.054 grupos misturam PORTAIS, que é
        # exatamente o que se quer detectar (a mesma casa anunciada no ZAP e
        # na OLX). Cidade e bairro também entram na checagem.
        # ------------------------------------------------------------------
        limpos, recusados = [], []
        for g in grupos:
            ruas = {endereco.chave_tolerante(info.get(u, {}).get("rua") or "")
                    for u in g}
            ruas.discard("")
            bairros = {info.get(u, {}).get("bairro") for u in g}
            bairros.discard(None)
            bairros.discard("")
            if len(ruas) >= 2:
                recusados.append((g, "ruas diferentes", sorted(ruas)))
            elif len(bairros) >= 2:
                recusados.append((g, "bairros diferentes", sorted(bairros)))
            else:
                limpos.append(g)

        # escolhe o "principal" de cada grupo: o de MENOR PREÇO.
        #
        # A mesma casa aparece em portais diferentes, às vezes com preços
        # diferentes. O que serve de referência para o usuário é a oferta mais
        # barata — é ela que ele quer ver na lista, e é ela que faz sentido
        # comparar com o preço de mercado. Em empate de preço (ou quando o
        # preço é desconhecido), desempata por mais fotos, que é o anúncio
        # mais completo.
        pacote = []
        for g in limpos:
            precos = [info[u]["preco"] for u in g if info.get(u, {}).get("preco")]
            def chave(u: str):
                preco = info.get(u, {}).get("preco")
                # preço desconhecido vai para o fim, nunca lidera o grupo
                return (preco is None, preco or 0, -n_fotos.get(u, 0))
            principal = min(g, key=chave)
            pacote.append({
                "membros": g,
                "principal": principal,
                "menor_preco": min(precos) if precos else None,
            })

        if recusados:
            print(f"\nGrupos RECUSADOS pela trava de segurança: {len(recusados)}"
                  f" ({sum(len(g) for g, _, _ in recusados)} anúncios)")
            for g, motivo, vals in sorted(recusados, key=lambda x: -len(x[0]))[:10]:
                print(f"  {len(g):>3} anúncios · {motivo}: "
                      f"{', '.join(v[:26] for v in vals[:3])}")
            if len(recusados) > 10:
                print(f"  ... e mais {len(recusados)-10} grupos")
            print("  (rode `verificar_duplicatas.py` para ver todos)")

        db = DB()
        marcados = db.marcar_duplicatas(pacote)
        db.close()
        print(f"\nMarcados {marcados} anúncios em {len(pacote)} grupos "
              f"(nada foi removido).")
        print("Use `python listar_duplicatas.py` para ver o resultado.")


if __name__ == "__main__":
    main()
