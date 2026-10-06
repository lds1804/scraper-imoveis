"""Orquestrador do ZAP Imóveis e do Viva Real.

Os dois são o MESMO inventário (grupo OLX), então este script cobre ambos —
só muda a flag `--portal`. Comprovado: os 30 primeiros anúncios têm IDs
idênticos nos dois sites.

A diferença prática é só qual link você prefere receber no anúncio:
    --portal zap       -> https://www.zapimoveis.com.br/imovel/<id>/
    --portal vivareal  -> https://www.vivareal.com.br/imovel/<id>/

Uso:
    python zap.py --dry-run                    # testa a API
    python zap.py                              # coleta os principais
    python zap.py --portal vivareal            # links do Viva Real
    python zap.py --todos --max-paginas 20     # inclui vizinhos
    python zap.py --bairros city-america parque-maria-domitila
"""

from __future__ import annotations

import argparse
import sys
import time

from cacaimoveis import config, glue_api
from cacaimoveis.progresso import Barra, _duracao, resumo
from cacaimoveis.scraper_browser import (
    atende_preco,
    calcular_financiamento,
    calcular_match_quintal,
    e_bairro_alvo,
    e_de_sao_paulo,
)
from cacaimoveis.storage import DB, baixar_fotos


def _filtrar_bairros(alvos: list[str] | None, todos: bool) -> list:
    """Seleciona os bairros pedidos (por slug ou nome, tolerante a caixa)."""
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
            print("        use --listar para ver os disponíveis.")
        return escolhidos

    if todos:
        return list(config.BAIRROS)
    return [b for b in config.BAIRROS if b.grupo == "principal"]


def dry_run(alvos: list[str] | None, todos: bool, portal: str) -> None:
    selecionados = _filtrar_bairros(alvos, todos)
    print(f"Portal: {portal}  (API do grupo OLX — ZAP e Viva Real "
          f"compartilham o inventário)\n")

    for bairro in selecionados:
        try:
            anuncios, total = glue_api.buscar_pagina(bairro.nome, 1, portal=portal)
        except Exception as e:  # noqa: BLE001
            print(f"  {bairro.nome:24s} ERRO: {str(e)[:100]}")
            continue

        alvo = [a for a in anuncios if e_bairro_alvo(a)[0]]
        print(f"  {bairro.nome:24s} casas={len(anuncios):3d} "
              f"(site={total:5d} inclui apartamentos) | no alvo={len(alvo):3d}")
        for a in anuncios[:2]:
            marca = "ALVO" if e_bairro_alvo(a)[0] else "fora"
            print(f"      [{marca}] R$ {a.preco or 0:,.0f} | {a.endereco} | "
                  f"{a.quartos}q | {len(a.fotos_urls)} fotos")


def coletar(alvos: list[str] | None, todos: bool, portal: str,
            max_paginas: int | None) -> None:
    selecionados = _filtrar_bairros(alvos, todos)
    if not selecionados:
        print("Nenhum bairro para coletar.")
        return

    db = DB()
    print(f"Portal : {portal}  (API do grupo OLX — mesmo inventário do Viva Real)")
    print(f"Banco  : {config.DB_PATH}")
    print(f"Já tem : {db.total()} anúncios {db.por_portal()}")
    print(f"Bairros: {len(selecionados)}\n")

    novos = ja_existiam = fora_do_alvo = fora_do_preco = 0
    vistos_total = 0
    fotos_baixadas = 0
    t0 = time.time()

    # Uma etapa por bairro. O total de anúncios de cada bairro só é conhecido
    # depois de paginar, então a barra mede o progresso em bairros concluídos
    # e mostra a contagem de itens do bairro atual subindo.
    barra = Barra.etapas([b.nome for b in selecionados], rotulo=portal.upper())

    for bairro in selecionados:
        barra.iniciar_etapa(bairro.nome)
        salvos_no_bairro = 0

        try:
            for anuncio in glue_api.coletar_bairro(bairro.nome, portal, max_paginas):
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

                # Descartar pelo preço ANTES de gravar e baixar fotos: um
                # anúncio caro baixava a galeria inteira (~30 fotos) para ser
                # descartado logo depois.
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

                # Conta as fotos na própria linha: baixar uma galeria de 50
                # fotos leva vários segundos, e sem isso a barra fica parada
                # como se tivesse travado.
                def _foto(_n: int) -> None:
                    barra.contar()

                fotos_baixadas += baixar_fotos(anuncio, None, db, avisar=_foto)

                # só interrompe a barra para o que é relevante
                if anuncio.listing_count and anuncio.listing_count > 1:
                    barra.escrever(
                        f"  [repetido x{anuncio.listing_count}] "
                        f"{(anuncio.titulo or '')[:44]}"
                    )
                barra.contar()
        except Exception as e:  # noqa: BLE001
            barra.escrever(f"  [erro] {bairro.nome}: {str(e)[:90]}")

        barra.escrever(
            f"  {bairro.nome:26s} +{salvos_no_bairro} novos "
            f"(total {db.total()})"
        )
        barra.avancar()
        time.sleep(config.GLUE_DELAY_S)

    barra.encerrar()

    resumo(f"{portal.upper()} · coleta finalizada", [
        ("anúncios vistos", vistos_total),
        ("novos salvos", novos),
        ("já existiam", ja_existiam),
        ("fora do bairro-alvo", fora_do_alvo),
        ("acima do preço", fora_do_preco),
        ("tempo", _duracao(time.time() - t0)),
        ("total no banco", f"{db.total()}"),
    ])
    for p, n in sorted(db.por_portal().items()):
        print(f"  {p:24s} {n}")
    db.close()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Crawler do ZAP Imóveis / Viva Real (API do grupo OLX)")
    parser.add_argument("--portal", choices=["zap", "vivareal"], default="zap",
                        help="de qual site vêm os links (mesmo inventário)")
    parser.add_argument("--dry-run", action="store_true",
                        help="só testa a API, não salva")
    parser.add_argument("--bairros", nargs="+", metavar="SLUG",
                        help="coleta só estes bairros")
    parser.add_argument("--todos", action="store_true",
                        help="inclui também os bairros vizinhos")
    parser.add_argument("--max-paginas", type=int, default=None, metavar="N",
                        help=f"limite de páginas por bairro (padrão {config.GLUE_MAX_PAGINAS})")
    args = parser.parse_args()

    if args.dry_run:
        dry_run(args.bairros, args.todos, args.portal)
    else:
        coletar(args.bairros, args.todos, args.portal, args.max_paginas)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nInterrompido pelo usuário.")
        sys.exit(0)
