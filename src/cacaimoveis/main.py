"""Orquestrador do scraper do Imovelweb (via Playwright).

Uso:
    python main.py --dry-run                     # testa a 1a página de cada bairro
    python main.py                               # coleta todos os bairros
    python main.py --bairros city-america        # coleta só esses (aceita vários)
    python main.py --listar                      # mostra os bairros disponíveis
"""

from __future__ import annotations

import argparse
import sys

from playwright.sync_api import sync_playwright

from cacaimoveis import config
from cacaimoveis.scraper_browser import (
    CloudflareBloqueou,
    _e_challenge,
    atende_preco,
    calcular_match_quintal,
    coletar_bairro,
    e_bairro_alvo,
    e_casa,
    e_de_sao_paulo,
    montar_url,
    parse_cards,
)
from cacaimoveis.storage import DB, baixar_fotos


def _abrir_contexto(p):
    """Abre um contexto PERSISTENTE do Chromium.

    O perfil em config.USER_DATA_DIR guarda os cookies do Cloudflare, então
    o desafio anti-bot só precisa ser resolvido uma vez (na 1a execução).
    """
    return p.chromium.launch_persistent_context(
        config.USER_DATA_DIR,
        headless=config.HEADLESS,
        user_agent=config.USER_AGENT,
        locale="pt-BR",
        viewport={"width": 1366, "height": 900},
        args=["--disable-blink-features=AutomationControlled"],
    )


def _filtrar_bairros(alvos: list[str] | None) -> list:
    """Seleciona os bairros pedidos (por slug ou nome, tolerante a caixa).

    Sem `alvos`, devolve todos. Avisa se algum slug pedido não existir.
    """
    if not alvos:
        return list(config.BAIRROS)

    pedidos = {a.strip().lower().replace(" ", "-") for a in alvos if a.strip()}
    escolhidos = []
    for b in config.BAIRROS:
        if b.slug.lower() in pedidos or b.nome.lower() in pedidos:
            escolhidos.append(b)
            pedidos.discard(b.slug.lower())
            pedidos.discard(b.nome.lower())

    if pedidos:
        print(f"[aviso] bairro(s) não encontrado(s) em config.BAIRROS: {sorted(pedidos)}")
        print("        use --listar para ver os disponíveis.")
    return escolhidos


def listar_bairros() -> None:
    print(f"Bairros configurados ({len(config.BAIRROS)}):\n")
    for b in config.BAIRROS:
        ja = ""
        print(f"  {b.slug:26s} {b.nome:26s} [{b.grupo}]{ja}")


def dry_run(alvos: list[str] | None = None) -> None:
    selecionados = _filtrar_bairros(alvos)
    with sync_playwright() as p:
        ctx = _abrir_contexto(p)
        page = ctx.pages[0] if ctx.pages else ctx.new_page()

        for bairro in selecionados:
            url = montar_url(bairro.slug, 1)
            print(f"\n=== DRY RUN: {bairro.nome} ===\n{url}")
            resp = page.goto(url, wait_until="domcontentloaded", timeout=config.NAV_TIMEOUT_MS)
            print("  Status:", resp.status if resp else "?")

            if _e_challenge(page):
                print("  [cloudflare] desafio detectado. Aguardando...")
                page.wait_for_timeout(config.CHALLENGE_TIMEOUT_MS)

            page.wait_for_timeout(3000)
            anuncios = parse_cards(page.content(), bairro.slug)
            print(f"  Cards em São Paulo: {len(anuncios)}")
            for a in anuncios[:3]:
                print(f"    - R$ {a.preco} | {a.titulo[:40]!r} | {a.endereco}")

        ctx.close()


def rodar(alvos: list[str] | None = None) -> int:
    selecionados = _filtrar_bairros(alvos)
    if not selecionados:
        print("Nenhum bairro para coletar.")
        return 0

    db = DB()
    print(f"Banco: {config.DB_PATH} (já tem {db.total()} anúncios)")
    print(f"Bairros a coletar: {len(selecionados)}")

    bloqueado = False
    with sync_playwright() as p:
        ctx = _abrir_contexto(p)
        page = ctx.pages[0] if ctx.pages else ctx.new_page()

        try:
            _coletar(selecionados, page, ctx, db)
        except CloudflareBloqueou as e:
            bloqueado = True
            print(f"\n[cloudflare] {e}. Coleta do Imovelweb interrompida.")
            print("  O que já foi salvo fica no banco. Abra `caca-imovelweb --dry-run` "
                  "para resolver o desafio na janela e rode de novo.")
        ctx.close()

    print(f"\n{'=' * 60}")
    print(f"Total no banco: {db.total()} anúncios")
    db.close()
    return 3 if bloqueado else 0


def _coletar(selecionados, page, ctx, db) -> None:
    novos = 0
    descartados_tipo = 0
    for bairro in selecionados:
        print(f"\n{'=' * 60}\nBAIRRO: {bairro.nome}\n{'=' * 60}")
        for anuncio in coletar_bairro(page, bairro):
            if db.existe(anuncio.url):
                print(f"  [pulado] já salvo: {anuncio.url}")
                continue

            # rede de segurança: nunca salvar anúncio de outra cidade
            if not e_de_sao_paulo(anuncio):
                print(f"  [fora de SP] descartado: {anuncio.endereco}")
                continue

            # e nunca de um bairro que não está na lista-alvo
            ok, nome = e_bairro_alvo(anuncio)
            if not ok:
                print(f"  [bairro fora da lista] descartado: {nome}")
                continue

            # rede de segurança: a URL já pede /casas-venda-, mas se o
            # site devolver apartamento, não entra
            if not e_casa(anuncio):
                print(f"  [não é casa] descartado: {anuncio.titulo[:40]}")
                descartados_tipo += 1
                continue

            calcular_match_quintal(anuncio)

            if not atende_preco(anuncio):
                print(f"  [acima do preço] R$ {anuncio.preco} | {anuncio.url}")
                continue

            db.salvar_anuncio(anuncio)
            novos += 1
            # `None` = requests direto no CDN. Passar `ctx.request` (Playwright) para
            # as threads de download falhava com "Cannot switch to a different
            # thread": 36 fotos perdidas em uma rodada de um bairro
            n = baixar_fotos(anuncio, None, db)
            print(
                f"  [salvo] {anuncio.titulo[:40]!r} | R$ {anuncio.preco} | "
                f"quintal={anuncio.match_quintal}({anuncio.score_quintal}) | {n} fotos"
            )

    print(f"\n{'=' * 60}")
    print(f"Novos anúncios salvos: {novos}")
    if descartados_tipo:
        print(f"Descartados (não-casa): {descartados_tipo}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Scraper Imovelweb (Playwright)")
    parser.add_argument("--dry-run", action="store_true", help="Só testa, não salva")
    parser.add_argument("--listar", action="store_true", help="Lista os bairros e sai")
    parser.add_argument(
        "--bairros",
        nargs="+",
        metavar="SLUG",
        help="Coleta só estes bairros (slug ou nome, separados por espaço)",
    )
    args = parser.parse_args()

    if args.listar:
        listar_bairros()
    elif args.dry_run:
        dry_run(args.bairros)
    else:
        return rodar(args.bairros)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\nInterrompido pelo usuário.")
        sys.exit(130)

