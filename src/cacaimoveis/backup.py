"""Backup do banco de trabalho, com rotação.

Por que existe: os backups eram cópias manuais na raiz
(`imoveis_backup_20261002_083040.db`, `imoveis_antes_limpeza_*.db`), cada
uma com 600+ MB, sem regra de quantas guardar. E copiar o arquivo com o banco
aberto por outro processo pode gerar uma cópia corrompida.

Aqui a cópia usa a API de backup do próprio SQLite (consistente mesmo com o
banco em uso) e só os N mais recentes ficam em `backups/`.

Uso:
    caca-backup                      # copia e mantém os 3 mais recentes
    caca-backup --rotulo antes_limpeza
    caca-backup --manter 5
    caca-backup --listar
"""

from __future__ import annotations

import argparse
import datetime
import glob
import os
import sqlite3
import sys

from cacaimoveis import config

PASTA = config.caminho("backups")
PREFIXO = "imoveis_"


def _mb(caminho: str) -> str:
    return f"{os.path.getsize(caminho) / 1e6:,.1f} MB"


def listar() -> list[str]:
    """Backups existentes, do mais recente para o mais antigo."""
    return sorted(glob.glob(os.path.join(PASTA, f"{PREFIXO}*.db")),
                  key=os.path.getmtime, reverse=True)


def copiar(rotulo: str = "", origem: str | None = None) -> str:
    """Gera um backup consistente de `origem` (padrão: o banco configurado)."""
    origem = origem or config.DB_PATH
    if not os.path.exists(origem):
        raise FileNotFoundError(origem)
    os.makedirs(PASTA, exist_ok=True)
    carimbo = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    nome = f"{PREFIXO}{rotulo + '_' if rotulo else ''}{carimbo}.db"
    destino = os.path.join(PASTA, nome)

    fonte = sqlite3.connect(f"file:{origem}?mode=ro", uri=True)
    alvo = sqlite3.connect(destino)
    try:
        fonte.backup(alvo)
    finally:
        alvo.close()
        fonte.close()
    return destino


def rodar(manter: int) -> list[str]:
    """Apaga os backups além dos `manter` mais recentes. Devolve os apagados."""
    apagados = listar()[max(manter, 1):]
    for caminho in apagados:
        os.remove(caminho)
    return apagados


def main() -> int:
    ap = argparse.ArgumentParser(description="Backup do imoveis.db com rotação")
    ap.add_argument("--rotulo", default="", help="texto no nome (ex.: antes_limpeza)")
    ap.add_argument("--manter", type=int, default=3,
                    help="quantos backups guardar (padrão 3)")
    ap.add_argument("--listar", action="store_true", help="só lista os backups")
    args = ap.parse_args()

    if not args.listar:
        destino = copiar(args.rotulo)
        print(f"backup: {destino} ({_mb(destino)})")
        for caminho in rodar(args.manter):
            print(f"removido (rotação): {os.path.basename(caminho)}")

    for caminho in listar():
        print(f"  {os.path.basename(caminho):48s} {_mb(caminho):>12}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
