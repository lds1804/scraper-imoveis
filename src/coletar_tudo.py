"""Roda todas as coletas em sequência, com uma barra de progresso por portal.

Por que existe: cada crawler tem sua própria barra, e rodá-los um a um na mão
exige acompanhar quatro terminais. Este script encadeia tudo, mostra em que
portal está, e no fim imprime um quadro único com o resultado de todos.

Ordem: ZAP/VivaReal e QuintoAndar usam API (rápidos, sem navegador), então vêm
primeiro. A OLX usa Playwright (lenta e sensível ao perfil compartilhado), fica
por último para não atrapalhar as outras.

Uso:
    python coletar_tudo.py                     # bairros principais
    python coletar_tudo.py --todos             # inclui os vizinhos
    python coletar_tudo.py --so zap quintoandar
    python coletar_tudo.py --max-paginas 5     # teto de páginas por bairro

Os crawlers rodam como PROCESSO SEPARADO e a saída vai direto para este
terminal — nada de capturar em buffer, senão a barra de progresso some.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import time
from pathlib import Path

import config
from progresso import _duracao

RAIZ = Path(config.RAIZ)
PYTHON = RAIZ / ".venv" / "Scripts" / "python.exe"

# (chave, script, como o nome aparece, quais argumentos aceita)
COLETAS = [
    ("zap", "zap.py", "ZAP Imóveis", "portal_api"),
    ("quintoandar", "quintoandar_principal.py", "QuintoAndar", "portal_api"),
    ("olx", "olx_principal.py", "OLX", "olx"),
    # o VivaReal é o MESMO inventário do ZAP (grupo OLX), então não é uma
    # coleta separada: use `--so zap --portal vivareal` para gravar os links
    # do Viva Real em vez dos do ZAP.
]


def _contar_por_portal() -> dict[str, int]:
    """Quantos anúncios de cada portal existem agora no banco."""
    from storage import DB

    db = DB()
    try:
        return db.por_portal()
    finally:
        db.close()


def _montar_cmd(script: str, tipo: str, args) -> list[str]:
    """Monta a linha de comando do crawler."""
    cmd = [str(PYTHON), str(RAIZ / script)]

    if args.bairros:
        cmd += ["--bairros", *args.bairros]
    elif args.todos:
        cmd.append("--todos")

    if args.max_paginas:
        cmd += ["--max-paginas", str(args.max_paginas)]

    # a OLX tem flag própria para enriquecer os detalhes
    if tipo == "olx" and args.detalhes:
        cmd.append("--detalhes")

    return cmd


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Roda todas as coletas em sequência, com progresso")
    parser.add_argument("--so", nargs="+", metavar="PORTAL",
                        choices=[c[0] for c in COLETAS],
                        help="roda só estes portais")
    parser.add_argument("--bairros", nargs="+", metavar="SLUG",
                        help="coleta só estes bairros")
    parser.add_argument("--todos", action="store_true",
                        help="inclui também os bairros vizinhos")
    parser.add_argument("--max-paginas", type=int, default=None, metavar="N",
                        help="limite de páginas por bairro")
    parser.add_argument("--detalhes", action="store_true",
                        help="OLX: abre a página de cada anúncio (mais lento)")
    args = parser.parse_args()

    selecionadas = [c for c in COLETAS if not args.so or c[0] in args.so]
    if not selecionadas:
        print("Nenhum portal selecionado.")
        return 1

    antes = _contar_por_portal()
    total_antes = sum(antes.values())

    # ------------------------------------------------------------------
    # Cabeçalho: o que vai rodar, para onde, e como está o banco agora
    # ------------------------------------------------------------------
    print("=" * 62)
    print("COLETA COMPLETA — o progresso de cada portal aparece abaixo")
    print("=" * 62)
    print(f"Banco       : {config.DB_PATH}")
    print(f"Já tem      : {total_antes} anúncios")
    for portal, n in sorted(antes.items(), key=lambda x: -x[1]):
        print(f"    {portal:24s} {n}")
    print(f"Portais     : {', '.join(c[2] for c in selecionadas)}")
    if args.bairros:
        print(f"Bairros     : {' '.join(args.bairros)}")
    elif args.todos:
        print(f"Bairros     : todos os {len(config.BAIRROS)} configurados")
    else:
        principais = [b.nome for b in config.BAIRROS if b.grupo == "principal"]
        print(f"Bairros     : {' e '.join(principais)} (principais)")
    print(f"Max páginas : {args.max_paginas or 'padrão de cada portal'}")
    print()

    # ------------------------------------------------------------------
    # Roda cada crawler como processo separado (saída direta no terminal)
    # ------------------------------------------------------------------
    resultados: list[tuple[str, str, float]] = []
    t_inicio = time.time()

    for i, (_chave, script, nome, tipo) in enumerate(selecionadas, 1):
        print("\n" + "#" * 62)
        print(f"# [{i}/{len(selecionadas)}] {nome}")
        print("#" * 62 + "\n", flush=True)

        cmd = _montar_cmd(script, tipo, args)
        t0 = time.time()
        try:
            # Sem captura de saída: o stdout do filho é o MESMO deste terminal,
            # então a barra de progresso dele aparece em tempo real.
            proc = subprocess.run(cmd, cwd=str(RAIZ))
            situacao = "ok" if proc.returncode == 0 else f"erro (código {proc.returncode})"
        except KeyboardInterrupt:
            print("\n[interrompido pelo usuário]")
            situacao = "interrompido"
        except OSError as e:
            print(f"[falhou] {e}")
            situacao = "falhou"

        decorrido = time.time() - t0
        resultados.append((nome, situacao, decorrido))
        print(f"\n>> {nome}: {situacao} em {_duracao(decorrido)}", flush=True)

    # ------------------------------------------------------------------
    # Quadro final: quanto entrou em cada portal
    # ------------------------------------------------------------------
    depois = _contar_por_portal()
    total_depois = sum(depois.values())

    print("\n" + "=" * 62)
    print("COLETA COMPLETA — resumo")
    print("=" * 62)

    print(f"{'portal':26s} {'antes':>7s} {'depois':>7s} {'novos':>7s}")
    print("-" * 62)
    for portal in sorted(set(antes) | set(depois)):
        a, d = antes.get(portal, 0), depois.get(portal, 0)
        marca = "  <- novo" if a == 0 and d else ""
        print(f"{portal:26s} {a:>7d} {d:>7d} {d - a:>+7d}{marca}")
    print("-" * 62)
    print(f"{'TOTAL':26s} {total_antes:>7d} {total_depois:>7d} "
          f"{total_depois - total_antes:>+7d}")

    print("\nPor portal:")
    for nome, situacao, decorrido in resultados:
        print(f"  {nome:22s} {situacao:16s} {_duracao(decorrido)}")

    print(f"\nTempo total: {_duracao(time.time() - t_inicio)}")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\nInterrompido pelo usuário.")
        sys.exit(130)
