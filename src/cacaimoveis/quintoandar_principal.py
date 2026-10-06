"""Orquestrador do QuintoAndar.

Diferente do ZAP/Viva Real, o QuintoAndar tem inventário próprio (não é do
grupo OLX), então vale coletar separadamente.

Vantagem sobre a OLX: o filtro de tipo FUNCIONA na API, então só vêm casas.

Uso:
    python quintoandar_principal.py --dry-run
    python quintoandar_principal.py
    python quintoandar_principal.py --todos
    python quintoandar_principal.py --bairros city-america parque-maria-domitila
"""

from __future__ import annotations

import argparse
import sys
import time

from cacaimoveis import config, logs
from cacaimoveis import quintoandar as qa
from cacaimoveis.incremental import Incremental
from cacaimoveis.progresso import Barra, _duracao, resumo
from cacaimoveis.scraper_browser import (
    atende_preco,
    calcular_financiamento,
    calcular_match_quintal,
    e_bairro_alvo,
    e_de_sao_paulo,
)
from cacaimoveis.storage import DB, baixar_fotos

log = logs.obter(__name__)

PORTAL = "quintoandar"


def _filtrar_bairros(alvos: list[str] | None, todos: bool) -> list:
    if alvos:
        pedidos = {a.strip().lower().replace(" ", "-") for a in alvos if a.strip()}
        escolhidos = []
        for b in config.BAIRROS:
            if b.slug.lower() in pedidos or b.nome.lower() in pedidos:
                escolhidos.append(b)
                pedidos.discard(b.slug.lower())
                pedidos.discard(b.nome.lower())
        if pedidos:
            print(f"[aviso] bairro(s) não encontrado(s): {sorted(pedidos)}")
        return escolhidos
    if todos:
        return list(config.BAIRROS)
    return [b for b in config.BAIRROS if b.grupo == "principal"]


def dry_run(alvos: list[str] | None, todos: bool) -> None:
    selecionados = _filtrar_bairros(alvos, todos)
    print("Portal: QuintoAndar (inventário próprio)\n")
    for bairro in selecionados:
        slug = qa.montar_slug(bairro.nome)
        try:
            anuncios, total = qa.buscar_pagina(slug, 1, 20)
        except Exception as e:  # noqa: BLE001
            log.warning("erro tratado, a execução segue: %s", e, exc_info=True)
            print(f"  {bairro.nome:24s} ERRO: {str(e)[:90]}")
            continue
        alvo = [a for a in anuncios if e_bairro_alvo(a)[0]]
        print(f"  {bairro.nome:24s} casas={len(anuncios):3d} (site={total:5d}) "
              f"| no alvo={len(alvo):3d}")
        for a in anuncios[:2]:
            print(f"      R$ {a.preco or 0:,.0f} | {a.endereco} | {a.quartos}q | "
                  f"{len(a.fotos_urls)} fotos")


def coletar(alvos: list[str] | None, todos: bool,
            max_paginas: int | None, completa: bool = False) -> None:
    selecionados = _filtrar_bairros(alvos, todos)
    if not selecionados:
        print("Nenhum bairro para coletar.")
        return

    db = DB()
    incr = Incremental(db.conn, "quintoandar", completa=completa)
    print("Portal : QuintoAndar  (inventário próprio)")
    print(f"Banco  : {config.DB_PATH}")
    print(f"Já tem : {db.total()} anúncios {db.por_portal()}")
    print(f"Bairros: {len(selecionados)}\n")

    novos = ja_existiam = fora_do_alvo = fora_do_preco = 0
    vistos_total = 0
    fotos_baixadas = 0
    t0 = time.time()

    barra = Barra.etapas([b.nome for b in selecionados], rotulo="QUINTOANDAR")

    for bairro in selecionados:
        barra.iniciar_etapa(bairro.nome)
        salvos_no_bairro = 0

        try:
            for anuncio in qa.coletar_bairro(bairro.nome, max_paginas, incremental=incr):
                vistos_total += 1

                if not e_de_sao_paulo(anuncio):
                    barra.contar()
                    continue

                ok, nome = e_bairro_alvo(anuncio)
                if not ok:
                    fora_do_alvo += 1
                    barra.contar()
                    continue

                anuncio.bairro = nome or bairro.nome

                # O filtro de preço vai ANTES de gravar e baixar fotos.
                # Importa mais aqui que nos outros portais: a API do
                # QuintoAndar IGNORA o filtro de preço (verificado — o
                # `priceRange` do body não muda o total), então ela devolve
                # muito anúncio caro que seria descartado logo depois.
                if not atende_preco(anuncio):
                    fora_do_preco += 1
                    barra.contar()
                    continue

                if db.existe(anuncio.url):
                    ja_existiam += 1
                    barra.contar()
                    continue

                calcular_match_quintal(anuncio)
                calcular_financiamento(anuncio)
                db.salvar_anuncio(anuncio)
                novos += 1
                salvos_no_bairro += 1

                # Conta as fotos na linha: uma galeria leva vários segundos e
                # sem isso a barra parece travada.
                def _foto(_n: int) -> None:
                    barra.contar()

                fotos_baixadas += baixar_fotos(anuncio, None, db, avisar=_foto)
                barra.contar()
        except Exception as e:  # noqa: BLE001
            log.warning("erro tratado, a execução segue: %s", e, exc_info=True)
            barra.escrever(f"  [erro] {bairro.nome}: {str(e)[:90]}")

        barra.escrever(
            f"  {bairro.nome:26s} +{salvos_no_bairro} novos "
            f"(total {db.total()})"
        )
        barra.avancar()
        time.sleep(config.QUINTO_DELAY_S)

    barra.encerrar()

    resumo("QUINTOANDAR · coleta finalizada", [
        ("casas vistas", vistos_total),
        ("novos salvos", novos),
        ("já existiam", ja_existiam),
        ("fora do bairro-alvo", fora_do_alvo),
        ("acima do preço", fora_do_preco),
        ("coleta", incr.resumo()),
        ("fotos baixadas", fotos_baixadas),
        ("tempo", _duracao(time.time() - t0)),
        ("total no banco", f"{db.total()}"),
    ])
    for p, n in sorted(db.por_portal().items()):
        print(f"  {p:24s} {n}")
    db.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Crawler do QuintoAndar")
    parser.add_argument("--dry-run", action="store_true", help="só testa, não salva")
    parser.add_argument("--bairros", nargs="+", metavar="SLUG")
    parser.add_argument("--todos", action="store_true",
                        help="inclui também os bairros vizinhos")
    parser.add_argument("--max-paginas", type=int, default=None, metavar="N")
    parser.add_argument("--completa", action="store_true",
                        help="pagina tudo (padrão: só até o que já foi visto)")
    args = parser.parse_args()

    if args.dry_run:
        dry_run(args.bairros, args.todos)
    else:
        coletar(args.bairros, args.todos, args.max_paginas, args.completa)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nInterrompido pelo usuário.")
        sys.exit(0)
