"""Orquestrador do crawler da OLX (Playwright).

Uso:
    python olx_principal.py --dry-run                  # testa 1 página por bairro
    python olx_principal.py                            # coleta os bairros principais
    python olx_principal.py --todos                    # inclui os vizinhos
    python olx_principal.py --bairros vila-mangalot    # só estes
    python olx_principal.py --detalhes                 # completa via página do anúncio
    python olx_principal.py --listar                   # mostra os bairros

Sem `--detalhes` o anúncio entra no banco com o que a listagem dá (título,
preço, quartos, banheiros, vagas, área e 1 foto). Com `--detalhes` ele é
completado com a descrição inteira, o CEP e a galeria completa (até ~20 fotos).

Vale a pena rodar sem `--detalhes` primeiro: a listagem já valida o bairro, e
só o que passar no filtro merece o custo de abrir a página individual.
"""

from __future__ import annotations

import argparse
import sys
import time

from playwright.sync_api import sync_playwright

from cacaimoveis import config, olx
from cacaimoveis.progresso import Barra, resumo
from cacaimoveis.scraper_browser import (
    atende_preco,
    calcular_financiamento,
    calcular_match_quintal,
    e_bairro_alvo,
    e_casa,
    e_de_sao_paulo,
)
from cacaimoveis.storage import DB, baixar_fotos


def _fechar_sessao(estado: dict) -> None:
    """Fecha o contexto do navegador, ignorando erros.

    O Chromium pode já ter morrido (foi justamente esse o motivo de estarmos
    fechando), então falhar aqui não é problema.
    """
    ctx = estado.get("ctx")
    if ctx is None:
        return
    try:
        ctx.close()
    except Exception:  # noqa: BLE001
        pass
    estado["ctx"] = estado["page"] = None


def _abrir_contexto(p):
    """Contexto persistente do Chromium (reaproveita cookies entre execuções)."""
    return p.chromium.launch_persistent_context(
        config.USER_DATA_DIR,
        headless=config.HEADLESS,
        user_agent=config.USER_AGENT,
        locale="pt-BR",
        viewport={"width": 1366, "height": 900},
        args=["--disable-blink-features=AutomationControlled"],
    )


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
    # padrão: só os principais (os vizinhos são muitos e a busca é tolerante,
    # então os principais acabam trazendo boa parte deles de qualquer forma)
    return [b for b in config.BAIRROS if b.grupo == "principal"]


def listar_bairros() -> None:
    print(f"Bairros configurados ({len(config.BAIRROS)}):\n")
    for b in config.BAIRROS:
        print(f"  {b.slug:26s} {b.nome:26s} [{b.grupo}]")


def dry_run(alvos: list[str] | None, todos: bool) -> None:
    selecionados = _filtrar_bairros(alvos, todos)
    with sync_playwright() as p:
        ctx = _abrir_contexto(p)
        page = ctx.pages[0] if ctx.pages else ctx.new_page()

        for bairro in selecionados:
            url = olx.montar_url(bairro.nome, 1)
            print(f"\n=== DRY RUN OLX: {bairro.nome} ===\n{url}")

            html = olx._abrir(page, url)
            if html is None:
                print("  [falhou] não consegui abrir.")
                continue

            anuncios = olx.parse_cards(html, bairro.nome, config.NOME_CIDADE)
            do_bairro = [a for a in anuncios if e_bairro_alvo(a)[0]]
            print(f"  {len(anuncios)} anúncios | {len(do_bairro)} de bairros-alvo")
            for a in anuncios[:3]:
                marca = "ALVO" if e_bairro_alvo(a)[0] else "fora"
                print(f"    [{marca}] R$ {a.preco:,.0f} | {a.endereco} | "
                      f"{a.quartos}q {a.area_construida:.0f}m²" if a.preco else "")

        ctx.close()


def coletar(alvos: list[str] | None, todos: bool, com_detalhes: bool,
            max_paginas: int | None = None) -> None:
    selecionados = _filtrar_bairros(alvos, todos)
    if not selecionados:
        print("Nenhum bairro para coletar.")
        return

    db = DB()
    print(f"Banco: {config.DB_PATH}")
    print(f"Já tem {db.total()} anúncios "
          f"({db.por_portal().get('olx', 0)} da OLX)")
    print(f"Bairros a coletar na OLX: {len(selecionados)}")

    novos = 0
    ja_existiam = 0
    com_detalhe = 0
    fora_do_alvo = 0
    apartamentos = 0
    total_vistos = 0
    reinicios = 0
    fotos_baixadas = 0

    playwright = sync_playwright().start()
    estado = {"ctx": None, "page": None}

    def nova_sessao():
        """(Re)abre o navegador. Devolve a página pronta."""
        _fechar_sessao(estado)
        ctx = _abrir_contexto(playwright)
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        estado["ctx"], estado["page"] = ctx, page
        return page

    page = nova_sessao()

    barra = Barra.etapas([b.nome for b in selecionados], rotulo="OLX")

    for bairro in selecionados:
        barra.iniciar_etapa(bairro.nome)
        salvos_no_bairro = 0

        # Um bairro pode ser tentado mais de uma vez: se o navegador cair no
        # meio, a sessão é recriada e a coleta recomeça. Os anúncios já salvos
        # são pulados por URL, então repetir é seguro (só custa requisições).
        for tentativa in range(1, config.OLX_MAX_TENTATIVAS_BAIRRO + 1):
            erro = None
            try:
                # quieto=True: a barra desenha na mesma linha, um print por
                # página quebraria o desenho
                for anuncio in olx.coletar_bairro(page, bairro, max_paginas,
                                                  quieto=True):
                    total_vistos += 1

                    # ---- filtros (iguais aos do Imovelweb) ----
                    if not e_de_sao_paulo(anuncio):
                        barra.contar()
                        continue

                    # Chamada única: `e_bairro_alvo` compara contra os 21
                    # bairros e era chamado duas vezes por anúncio.
                    ok, nome = e_bairro_alvo(anuncio)
                    if not ok:
                        fora_do_alvo += 1
                        barra.contar()
                        continue

                    if not atende_preco(anuncio):
                        barra.contar()
                        continue

                    # A OLX não tem filtro de tipo que funcione (ver
                    # `tipo_do_anuncio`), então descartamos aqui: a busca é de
                    # CASAS, e metade dos resultados eram apartamentos.
                    if not e_casa(anuncio):
                        apartamentos += 1
                        barra.contar()
                        continue

                    # guarda o bairro REAL que veio no anúncio
                    anuncio.bairro = nome or bairro.nome

                    if db.existe(anuncio.url):
                        ja_existiam += 1
                        barra.contar()
                        continue

                    # ---- detalhe: descrição completa, CEP e galeria ----
                    if com_detalhes:
                        olx.enriquecer_detalhe(page, anuncio, pausa=False)
                        com_detalhe += 1

                    calcular_match_quintal(anuncio)
                    calcular_financiamento(anuncio)

                    db.salvar_anuncio(anuncio)
                    novos += 1
                    salvos_no_bairro += 1

                    # Conta as fotos na linha: uma galeria leva vários
                    # segundos e sem isso a barra parece travada.
                    def _foto(_n: int) -> None:
                        barra.contar()

                    fotos_baixadas += baixar_fotos(anuncio, None, db,
                                                   avisar=_foto)

                    # só interrompe a barra para o que é relevante
                    if anuncio.match_quintal:
                        barra.escrever(
                            f"  [quintal] {(anuncio.titulo or '')[:46]}"
                        )
                    barra.contar()
            except Exception as e:  # noqa: BLE001
                erro = e

            if erro is None:
                break

            # O erro clássico aqui é "Target page, context or browser has been
            # closed": o perfil do Playwright é compartilhado, então outro
            # processo abrindo/fechando o navegador derruba este. Recriar a
            # sessão resolve, e o que já foi salvo não se perde.
            barra.escrever(f"  [erro] {str(erro)[:100]}")
            if tentativa < config.OLX_MAX_TENTATIVAS_BAIRRO:
                reinicios += 1
                barra.escrever("  [retomando] recriando a sessão do navegador...")
                time.sleep(5)
                page = nova_sessao()

        barra.escrever(
            f"  {bairro.nome:26s} +{salvos_no_bairro} novos "
            f"(total {db.total()})"
        )
        barra.avancar()

    _fechar_sessao(estado)
    playwright.stop()
    barra.encerrar()

    pares = [
        ("anúncios vistos", total_vistos),
        ("novos salvos", novos),
        ("já existiam", ja_existiam),
        ("apartamentos descartados", apartamentos),
        ("fora do bairro-alvo", fora_do_alvo),
        ("fotos baixadas", fotos_baixadas),
    ]
    if com_detalhes:
        pares.append(("páginas de detalhe", com_detalhe))
    if reinicios:
        pares.append(("sessões recriadas", reinicios))
    pares.append(("total no banco", f"{db.total()}"))
    resumo("OLX · coleta finalizada", pares)
    for portal, n in sorted(db.por_portal().items()):
        print(f"  {portal:24s} {n}")
    db.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Crawler da OLX (Playwright)")
    parser.add_argument("--dry-run", action="store_true",
                        help="só testa a 1ª página de cada bairro, não salva")
    parser.add_argument("--listar", action="store_true",
                        help="lista os bairros e sai")
    parser.add_argument("--bairros", nargs="+", metavar="SLUG",
                        help="coleta só estes bairros (slug ou nome)")
    parser.add_argument("--todos", action="store_true",
                        help="inclui também os bairros vizinhos")
    parser.add_argument("--detalhes", action="store_true",
                        help="abre a página de cada anúncio (descrição, CEP, galeria)")
    parser.add_argument("--max-paginas", type=int, default=None, metavar="N",
                        help=f"limite de páginas por bairro (padrão {config.OLX_MAX_PAGINAS})")
    args = parser.parse_args()

    if args.listar:
        listar_bairros()
    elif args.dry_run:
        dry_run(args.bairros, args.todos)
    else:
        coletar(args.bairros, args.todos, args.detalhes, args.max_paginas)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nInterrompido pelo usuário.")
        sys.exit(0)
