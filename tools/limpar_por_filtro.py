"""Remove do banco os anúncios que não passariam nos filtros atuais.

Por que existe: os filtros de coleta mudam, e os anúncios que já estavam
gravados continuam lá. Já aconteceu duas vezes:

  1. apartamentos entraram porque a OLX não tem filtro de tipo que funcione;
  2. anúncios acima do teto de preço entraram enquanto o teto era maior.

Esta ferramenta reaplica os filtros de HOJE sobre o que está no banco, para o
que você vê na interface corresponder ao que o coletor aceita.

O que sai junto: as fotos em disco (senão `fotos/` vira lixo que ninguém
mais referencia — hoje a pasta passa de 1,6 GB).

Uso:
    .venv\\Scripts\\python.exe tools\\limpar_por_filtro.py            # simulação
    .venv\\Scripts\\python.exe tools\\limpar_por_filtro.py --aplicar  # remove

    --preco            remove acima de config.PRECO_MAX
    --tipo             remove apartamentos
    --só-preco / --só-tipo   limita a um critério
"""

from _bootstrap import iniciar
iniciar()  # poe src/ no sys.path e fixa a raiz como diretorio de trabalho

import argparse
import datetime
import os
import shutil
import sys
from collections import Counter

import config
from scraper_browser import Anuncio, tipo_do_anuncio
from storage import DB, _slug


def _selecionar(db: DB, por_preco: bool, por_tipo: bool
                ) -> tuple[list[tuple[str, str, str]], Counter]:
    """Devolve [(url, titulo, motivo)] e um resumo do que existe no banco."""
    sair: list[tuple[str, str, str]] = []
    resumo = Counter()

    for r in db.conn.execute("SELECT url, titulo, preco FROM anuncios"):
        url, titulo = r["url"], r["titulo"] or ""
        preco = r["preco"]

        # tipo
        tipo = tipo_do_anuncio(Anuncio(url=url, titulo=titulo))
        resumo[f"tipo:{tipo}"] += 1

        # preço
        if preco is None:
            faixa = "sem preço"
        elif config.PRECO_MAX and preco > config.PRECO_MAX:
            faixa = f"acima de R$ {config.PRECO_MAX:,.0f}"
        elif config.PRECO_MIN and preco < config.PRECO_MIN:
            faixa = f"abaixo de R$ {config.PRECO_MIN:,.0f}"
        else:
            faixa = "dentro da faixa"
        resumo[f"preço: {faixa}"] += 1

        if por_tipo and tipo == "apartamento":
            sair.append((url, titulo, "apartamento"))
        elif por_preco and preco is not None and (
            (config.PRECO_MAX and preco > config.PRECO_MAX)
            or (config.PRECO_MIN and preco < config.PRECO_MIN)
        ):
            sair.append((url, titulo, f"R$ {preco:,.0f}"))

    return sair, resumo


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Remove anúncios que não passam nos filtros atuais")
    parser.add_argument("--aplicar", action="store_true",
                        help="remove de verdade (sem isso, só simula)")
    parser.add_argument("--preco", action="store_true",
                        help="remove acima de PRECO_MAX (config)")
    parser.add_argument("--tipo", action="store_true",
                        help="remove apartamentos")
    args = parser.parse_args()

    # sem critério explícito, aplica os dois
    por_preco = args.preco or not (args.preco or args.tipo)
    por_tipo = args.tipo or not (args.preco or args.tipo)

    db = DB()
    total_antes = db.total()
    sair, resumo = _selecionar(db, por_preco, por_tipo)

    print(f"Banco: {config.DB_PATH}")
    print(f"Total: {total_antes}")
    print(f"Teto de preço: R$ {config.PRECO_MAX:,.0f}"
          f"{'  (sem teto)' if not config.PRECO_MAX else ''}\n")

    print("Filtros que serão aplicados:")
    print(f"  preço : {'sim' if por_preco else 'não'}")
    print(f"  tipo  : {'sim' if por_tipo else 'não'}\n")

    print("Conteúdo atual do banco:")
    for chave, n in sorted(resumo.items()):
        print(f"  {chave:34s} {n}")

    if not sair:
        print("\nNada a remover: o banco já corresponde aos filtros.")
        db.close()
        return 0

    motivos = Counter(m for _, _, m in sair)
    print(f"\nA remover: {len(sair)} anúncios")
    for motivo, n in motivos.most_common():
        print(f"  {motivo:34s} {n}")

    # fotos que serão apagadas do disco
    arquivos = 0
    for url, _, _ in sair:
        arquivos += db.conn.execute(
            "SELECT COUNT(*) FROM fotos WHERE anuncio_url = ?", (url,)
        ).fetchone()[0]

    pastas = {os.path.join(config.FOTOS_DIR, _slug(url)) for url, _, _ in sair}
    print(f"\n  fotos em disco: ~{arquivos}")
    print(f"  pastas        : ~{len(pastas)}")

    print("\nAmostra:")
    for _, titulo, motivo in sair[:6]:
        print(f"  [{motivo}] {titulo[:58]}")

    if not args.aplicar:
        print("\n[simulação] Nada foi alterado. Use --aplicar para remover.")
        db.close()
        return 0

    # --- backup antes de mexer (o banco é a única fonte dos dados coletados)
    carimbo = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    backup = config.caminho(f"imoveis_antes_limpeza_{carimbo}.db")
    destino = db.conn.execute("PRAGMA database_list").fetchone()[2]
    shutil.copy2(destino, backup)
    print(f"\nBackup: {os.path.relpath(backup, config.RAIZ)}")

    for url, _, _ in sair:
        db.conn.execute("DELETE FROM fotos WHERE anuncio_url = ?", (url,))
        db.conn.execute("DELETE FROM anuncios WHERE url = ?", (url,))
    db.conn.commit()

    apagadas = 0
    for pasta in pastas:
        if os.path.isdir(pasta):
            shutil.rmtree(pasta, ignore_errors=True)
            apagadas += 1

    print(f"Removidos: {len(sair)} anúncios e {apagadas} pastas de fotos")
    print(f"Total agora: {db.total()} anúncios")
    db.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
