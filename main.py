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

import config
from scraper_browser import (
    _e_challenge,
    atende_preco,
    calcular_match_quintal,
    coletar_bairro,
    e_bairro_alvo,
    e_de_sao_paulo,
    montar_url,
    parse_cards,
)
from storage import DB, baixar_fotos


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


def rodar(alvos: list[str] | None = None) -> None:
    selecionados = _filtrar_bairros(alvos)
    if not selecionados:
        print("Nenhum bairro para coletar.")
        return

    db = DB()
    print(f"Banco: {config.DB_PATH} (já tem {db.total()} anúncios)")
    print(f"Bairros a coletar: {len(selecionados)}")

    novos = 0
    with sync_playwright() as p:
        ctx = _abrir_contexto(p)
        page = ctx.pages[0] if ctx.pages else ctx.new_page()

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

                calcular_match_quintal(anuncio)

                if not atende_preco(anuncio):
                    print(f"  [acima do preço] R$ {anuncio.preco} | {anuncio.url}")
                    continue

                db.salvar_anuncio(anuncio)
                novos += 1
                n = baixar_fotos(anuncio, ctx.request, db)
                print(
                    f"  [salvo] {anuncio.titulo[:40]!r} | R$ {anuncio.preco} | "
                    f"quintal={anuncio.match_quintal}({anuncio.score_quintal}) | {n} fotos"
                )

        ctx.close()

    print(f"\n{'=' * 60}")
    print(f"Novos anúncios salvos: {novos}")
    print(f"Total no banco: {db.total()} anúncios")
    db.close()


def main() -> None:
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
        rodar(args.bairros)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nInterrompido pelo usuário.")
        sys.exit(0)

