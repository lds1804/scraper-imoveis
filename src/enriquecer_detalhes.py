"""Enriquece os anúncios visitando a PÁGINA INDIVIDUAL de cada um.

Por quê: a listagem do Imovelweb só traz um resumo truncado da descrição e
raramente informa a área do terreno. A página individual tem a descrição
completa (de onde saem os m² de terreno) e o bloco `mainFeatures`
(quartos/banheiros/vagas/áreas confiáveis).

Uso:
    python enriquecer_detalhes.py                 # só os sem terreno
    python enriquecer_detalhes.py --todos         # todos os ainda não visitados
    python enriquecer_detalhes.py --limite 10     # processa no máximo 10
    python enriquecer_detalhes.py --refazer       # ignora a marca de já visitado

O progresso fica salvo na coluna `detalhe_ok`, então pode interromper com
Ctrl+C e rodar de novo depois — ele retoma de onde parou.
"""

from __future__ import annotations

import argparse
import random
import sqlite3
import sys
import time

from playwright.sync_api import sync_playwright

import config
from scraper_browser import Anuncio, _e_challenge, calcular_match_quintal, parse_detalhe
from storage import DB, baixar_fotos


def _linha_para_anuncio(row: sqlite3.Row) -> Anuncio:
    return Anuncio(
        url=row["url"],
        titulo=row["titulo"] or "",
        bairro=row["bairro"] or "",
        endereco=row["endereco"] or "",
        preco=row["preco"],
        area_construida=row["area_construida"],
        area_terreno=row["area_terreno"],
        quartos=row["quartos"],
        banheiros=row["banheiros"],
        vagas=row["vagas"],
        descricao=row["descricao"] or "",
        match_quintal=bool(row["match_quintal"]),
        score_quintal=row["score_quintal"] or 0,
    )


def _n_fotos_db(db: DB, url: str) -> int:
    return db.conn.execute(
        "SELECT COUNT(*) FROM fotos WHERE anuncio_url = ?", (url,)
    ).fetchone()[0]


def _abrir_contexto(p, headless: bool):
    """Mesmo contexto persistente do main.py (cookies do Cloudflare)."""
    return p.chromium.launch_persistent_context(
        config.USER_DATA_DIR,
        headless=headless,
        user_agent=config.USER_AGENT,
        locale="pt-BR",
        viewport={"width": 1366, "height": 900},
        args=["--disable-blink-features=AutomationControlled"],
    )


def _carregar_pagina(page, url: str) -> str | None:
    """Abre a URL tratando o desafio do Cloudflare. Devolve o HTML ou None.

    O bloqueio costuma aparecer depois de ~60 páginas seguidas. Aqui usamos
    recuo progressivo entre tentativas; o controle de "esfriamento" global
    fica a cargo do laço principal.
    """
    for tentativa in range(1, config.MAX_RETRIES + 1):
        try:
            resp = page.goto(
                url, wait_until="domcontentloaded", timeout=config.NAV_TIMEOUT_MS
            )
        except Exception as e:  # noqa: BLE001
            print(f"    [erro] {e}")
            time.sleep(5 * tentativa)
            continue

        status = resp.status if resp else "?"

        if _e_challenge(page):
            # espera crescente: 25s, 50s, 75s
            espera = config.CHALLENGE_TIMEOUT_MS * tentativa
            print(f"    [cloudflare] desafio (tent. {tentativa}); esperando {espera // 1000}s...")
            page.wait_for_timeout(espera)
            continue

        if status >= 400:
            print(f"    [http {status}] tentativa {tentativa}")
            page.wait_for_timeout(3000)
            continue

        # rola para disparar o lazy-load da descrição
        page.wait_for_timeout(1200)
        page.mouse.wheel(0, 3000)
        page.wait_for_timeout(900)
        return page.content()

    return None


def main() -> None:
    parser = argparse.ArgumentParser(description="Enriquece anúncios via página de detalhe")
    parser.add_argument("--todos", action="store_true", help="inclui os que já têm terreno")
    parser.add_argument("--refazer", action="store_true", help="ignora a marca detalhe_ok")
    parser.add_argument("--limite", type=int, default=0, help="máximo de anúncios (0 = sem limite)")
    parser.add_argument(
        "--headless",
        action="store_true",
        help="roda sem janela do navegador (mais rápido; usa o perfil já validado)",
    )
    parser.add_argument("--delay-min", type=float, default=None, help="intervalo mínimo entre páginas")
    parser.add_argument("--delay-max", type=float, default=None, help="intervalo máximo entre páginas")
    parser.add_argument(
        "--sem-fotos",
        action="store_true",
        help="não baixar as galerias completas de fotos",
    )
    args = parser.parse_args()

    delay_min = args.delay_min if args.delay_min is not None else config.DELAY_MIN
    delay_max = args.delay_max if args.delay_max is not None else config.DELAY_MAX

    db = DB()
    if args.refazer:
        db.conn.execute("UPDATE anuncios SET detalhe_ok = 0")
        db.conn.commit()

    pendentes = db.pendentes_detalhe(somente_sem_terreno=not args.todos)
    if args.limite:
        pendentes = pendentes[: args.limite]

    total_db = db.total()
    print(f"Banco: {config.DB_PATH} | {total_db} anúncios")
    print(f"A processar: {len(pendentes)} anúncios\n")
    if not pendentes:
        print("Nada a fazer. Use --refazer para reprocessar tudo.")
        db.close()
        return

    n_terreno = n_banh = n_ok = n_falha = n_fotos = 0
    falhas_seguidas = 0

    with sync_playwright() as p:
        ctx = _abrir_contexto(p, headless=args.headless)
        page = ctx.pages[0] if ctx.pages else ctx.new_page()

        try:
            for i, row in enumerate(pendentes, 1):
                a = _linha_para_anuncio(row)
                antes_terr = a.area_terreno
                antes_banh = a.banheiros
                antes_n_fotos = _n_fotos_db(db, a.url)

                print(f"[{i}/{len(pendentes)}] {a.titulo[:55]!r}")

                html = _carregar_pagina(page, a.url)
                if html is None:
                    n_falha += 1
                    falhas_seguidas += 1
                    print("    [falhou] mantido pendente para a próxima execução")

                    # Bloqueio persistente: esfria bastante antes de continuar
                    if falhas_seguidas >= 3:
                        esfria = 180 * min(falhas_seguidas, 5)
                        msg = f"    [pausa] {falhas_seguidas} falhas seguidas; esfriando {esfria}s"
                        print(msg)
                        page.wait_for_timeout(esfria * 1000)
                    else:
                        print()
                    continue

                falhas_seguidas = 0
                parse_detalhe(html, a)
                calcular_match_quintal(a)

                ganhos = []
                if antes_terr is None and a.area_terreno is not None:
                    ganhos.append(f"terreno={a.area_terreno:.0f}m²")
                    n_terreno += 1
                if antes_banh is None and a.banheiros is not None:
                    ganhos.append(f"banheiros={a.banheiros}")
                    n_banh += 1

                # A galeria da página é completa; a da listagem tinha 1 foto.
                if not args.sem_fotos and len(a.fotos_urls) > antes_n_fotos:
                    # se a galeria cresceu, os arquivos antigos não correspondem
                    # mais aos mesmos índices — recomeça do zero
                    if antes_n_fotos:
                        apagadas = db.limpar_fotos(a.url)
                        if apagadas:
                            print(f"    [fotos] {apagadas} antigas removidas")

                    baixadas = baixar_fotos(a, None, db)
                    if baixadas:
                        ganhos.append(f"+{baixadas} fotos")
                        n_fotos += baixadas

                db.atualizar_detalhe(a)
                n_ok += 1

                print(f"    [ok] {' '.join(ganhos) if ganhos else 'sem dados novos'}\n")
                time.sleep(random.uniform(delay_min, delay_max))
        except KeyboardInterrupt:
            print("\nInterrompido pelo usuário. Progresso salvo.")
        finally:
            ctx.close()

    print(f"{'=' * 50}")
    print(f"Processados com sucesso : {n_ok}")
    print(f"Falhas (Cloudflare/HTTP): {n_falha}")
    print(f"Terreno preenchido      : {n_terreno}")
    print(f"Banheiros preenchidos   : {n_banh}")
    print(f"Fotos baixadas          : {n_fotos}")
    print(f"Restantes pendentes     : {len(db.pendentes_detalhe(somente_sem_terreno=not args.todos))}")
    db.close()


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nInterrompido.")
        sys.exit(0)
