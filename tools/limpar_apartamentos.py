"""Remove os apartamentos já coletados do banco.

Por que existe: a busca é de CASAS, mas a OLX não tem filtro de tipo que
funcione (o Imovelweb filtra pela própria URL). Enquanto o filtro não existia
no código, ~470 apartamentos entraram no banco.

O que é removido: o anúncio E as fotos em disco correspondentes (senão a
pasta `fotos/` acumula lixo que ninguém mais referencia).

Uso:
    .venv\\Scripts\\python.exe tools\\limpar_apartamentos.py            # simulação
    .venv\\Scripts\\python.exe tools\\limpar_apartamentos.py --aplicar  # remove
"""

from _bootstrap import iniciar
iniciar()  # poe src/ no sys.path e fixa a raiz como diretorio de trabalho

import argparse
import os
import shutil
import sys
from collections import Counter

import config
from scraper_browser import Anuncio, tipo_do_anuncio
from storage import DB, _slug


def main() -> int:
    parser = argparse.ArgumentParser(description="Remove apartamentos do banco")
    parser.add_argument("--aplicar", action="store_true",
                        help="remove de verdade (sem isso, só simula)")
    args = parser.parse_args()

    db = DB()
    total_antes = db.total()

    # --- separa o que sai do que fica ---
    sair: list[tuple[str, str]] = []
    contagem = Counter()
    for r in db.conn.execute("SELECT url, titulo FROM anuncios"):
        a = Anuncio(url=r["url"], titulo=r["titulo"] or "")
        tipo = tipo_do_anuncio(a)
        contagem[tipo] += 1
        if tipo == "apartamento":
            sair.append((r["url"], r["titulo"] or ""))

    print(f"Banco: {config.DB_PATH}")
    print(f"Total antes: {total_antes}\n")
    for tipo, n in contagem.most_common():
        print(f"  {tipo:12s} {n}")

    if not sair:
        print("\nNenhum apartamento para remover.")
        db.close()
        return 0

    # --- fotos que serão apagadas do disco ---
    arquivos: list[str] = []
    for url, _ in sair:
        for r in db.conn.execute(
            "SELECT arquivo_local FROM fotos WHERE anuncio_url = ?", (url,)
        ):
            arquivos.append(r["arquivo_local"])

    print(f"\nA remover: {len(sair)} apartamentos e {len(arquivos)} fotos")
    print("\nAmostra:")
    for _, titulo in sair[:6]:
        print(f"  - {titulo[:66]}")

    if not args.aplicar:
        print("\n[simulação] Nada foi alterado. Use --aplicar para remover.")
        db.close()
        return 0

    # --- backup antes de mexer ---
    import datetime

    carimbo = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    backup = config.caminho(f"imoveis_antes_limpeza_{carimbo}.db")
    destino = db.conn.execute("PRAGMA database_list").fetchone()[2]
    shutil.copy2(destino, backup)
    print(f"\nBackup: {os.path.relpath(backup, config.RAIZ)}")

    # --- remove registros ---
    for url, _ in sair:
        db.conn.execute("DELETE FROM fotos WHERE anuncio_url = ?", (url,))
        db.conn.execute("DELETE FROM anuncios WHERE url = ?", (url,))
    db.conn.commit()

    # --- remove as pastas de fotos ---
    pastas = {os.path.join(config.FOTOS_DIR, _slug(url)) for url, _ in sair}
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
