"""Processa HTMLs capturados pela ponte do navegador (quando o Playwright
está bloqueado pelo Cloudflare).

Lê os arquivos de `html_ponte/` e decide o que fazer pelo CONTEÚDO:

  - Página de LISTAGEM  -> parse_cards -> filtros -> salva anúncios novos
  - Página de DETALHE   -> parse_detalhe -> completa descrição, quartos,
                           banheiros, financiamento e a GALERIA COMPLETA
                           (a listagem só publica 1 foto; o detalhe tem 28-42)

Como os arquivos são identificados:
  - listagem: o nome é o slug do bairro        (ex: `city-america.html`)
  - detalhe : `det_<ID>.html`                  (ex: `det_3027110058.html`)
              e a URL do anúncio é achada no banco pelo ID.

Uso:
    python processar_ponte.py                 # processa tudo que está na pasta
    python processar_ponte.py --bairro city-america
    python processar_ponte.py --sem-fotos     # não baixa galeria
"""

from __future__ import annotations

import argparse
import os
import re

import config
from scraper_browser import (
    Anuncio,
    atende_preco,
    bairro_confere,
    bairro_do_endereco,
    calcular_financiamento,
    calcular_match_quintal,
    parse_cards,
    parse_detalhe,
)
from storage import DB, baixar_fotos

PONTE_DIR = config.PONTE_DIR

# Marcadores que denunciam uma página de DETALHE (e não de listagem)
_MARCA_DETALHE = ("const mainFeatures", "'pictures'", '"pictures"')


def _e_pagina_detalhe(html: str) -> bool:
    return any(m in html for m in _MARCA_DETALHE)


def _bairro_do_arquivo(caminho: str) -> str:
    """Extrai o slug do bairro a partir do nome do arquivo gravado pela ponte."""
    return os.path.splitext(os.path.basename(caminho))[0]


def _id_do_arquivo(arquivo: str) -> str | None:
    """Extrai o ID do anúncio de `det_<ID>.html`."""
    m = re.search(r"det[_-]?(\d{6,})", arquivo, re.I)
    return m.group(1) if m else None


def _linha_para_anuncio(row) -> Anuncio:
    """Reconstrói o Anuncio a partir da linha do banco."""
    financiamento = row["aceita_financiamento"]
    return Anuncio(
        url=row["url"],
        titulo=row["titulo"] or "",
        bairro=row["bairro"] or "",
        endereco=row["endereco"] or "",
        rua=row["rua"] or "",
        preco=row["preco"],
        area_construida=row["area_construida"],
        area_terreno=row["area_terreno"],
        quartos=row["quartos"],
        banheiros=row["banheiros"],
        vagas=row["vagas"],
        descricao=row["descricao"] or "",
        match_quintal=bool(row["match_quintal"]),
        score_quintal=row["score_quintal"] or 0,
        fotos_urls=[u for u in (row["fotos_urls"] or "").split(",") if u],
        aceita_financiamento=None if financiamento is None else bool(financiamento),
    )


def _processar_detalhe(db: DB, arquivo: str, html: str, gravar_fotos: bool) -> str:
    """Completa um anúncio com os dados da página individual. Devolve um resumo."""
    id_anuncio = _id_do_arquivo(arquivo)
    if not id_anuncio:
        return "nome do arquivo sem ID (esperado det_<ID>.html)"

    row = db.conn.execute(
        "SELECT * FROM anuncios WHERE url LIKE ?", (f"%{id_anuncio}%",)
    ).fetchone()
    if row is None:
        return f"anúncio {id_anuncio} não está no banco"

    a = _linha_para_anuncio(row)
    antes_desc = len(a.descricao or "")
    antes_fotos = db.conn.execute(
        "SELECT COUNT(*) FROM fotos WHERE anuncio_url = ?", (a.url,)
    ).fetchone()[0]
    antes_banh = a.banheiros

    parse_detalhe(html, a)
    calcular_match_quintal(a)

    ganhos = []
    if len(a.descricao or "") > antes_desc:
        ganhos.append(f"descrição {antes_desc}->{len(a.descricao)}")
    if antes_banh is None and a.banheiros is not None:
        ganhos.append(f"banheiros={a.banheiros}")
    if a.area_terreno:
        ganhos.append(f"terreno={a.area_terreno:.0f}m²")

    # galeria completa: substitui a capa única da listagem
    if gravar_fotos and len(a.fotos_urls) > antes_fotos:
        if antes_fotos:
            db.limpar_fotos(a.url)
        baixadas = baixar_fotos(a, None, db)
        if baixadas:
            ganhos.append(f"+{baixadas} fotos")

    db.atualizar_detalhe(a)
    return " | ".join(ganhos) if ganhos else "sem novidade"


def main() -> None:
    parser = argparse.ArgumentParser(description="Processa HTMLs da ponte")
    parser.add_argument("--bairro", default="", help="processa só este slug")
    parser.add_argument("--sem-fotos", action="store_true", help="não baixar fotos")
    parser.add_argument("--limite", type=int, default=0, help="máx. de arquivos (0=todos)")
    parser.add_argument("--so-detalhes", action="store_true", help="pula listagens")
    args = parser.parse_args()

    if not os.path.isdir(PONTE_DIR):
        print(f"Nada para processar: pasta {PONTE_DIR}/ não existe.")
        print("Capture as páginas pelo navegador primeiro.")
        return

    arquivos = sorted(
        f for f in os.listdir(PONTE_DIR) if f.lower().endswith((".html", ".htm"))
    )
    if args.bairro:
        arquivos = [f for f in arquivos if args.bairro in f]
    if args.so_detalhes:
        arquivos = [f for f in arquivos if f.startswith("det_")]
    if args.limite:
        arquivos = arquivos[: args.limite]

    if not arquivos:
        print(f"Nenhum HTML em {PONTE_DIR}/ para processar.")
        return

    db = DB()
    print(f"Banco: {config.DB_PATH} (já tem {db.total()} anúncios)")
    print(f"Arquivos: {len(arquivos)}\n")

    total_novos = 0
    total_detalhes = 0
    for arquivo in arquivos:
        slug = _bairro_do_arquivo(arquivo)
        caminho = os.path.join(PONTE_DIR, arquivo)

        with open(caminho, encoding="utf-8", errors="replace") as f:
            html = f.read()

        print(f"{'=' * 60}")
        print(f"ARQUIVO: {arquivo}  ({len(html):,} bytes)")

        # ------------------------------------------------------------------
        # Página de DETALHE: completa um anúncio existente (galeria completa)
        # ------------------------------------------------------------------
        if _e_pagina_detalhe(html):
            print("TIPO   : detalhe")
            print(f"{'=' * 60}")
            resumo = _processar_detalhe(db, arquivo, html, not args.sem_fotos)
            print(f"  {resumo}\n")
            total_detalhes += 1
            continue

        # ------------------------------------------------------------------
        # Página de LISTAGEM: descobre anúncios novos
        # ------------------------------------------------------------------
        slug_base = re.sub(r"_?pag\d+$", "", slug)
        print(f"TIPO   : listagem do bairro {slug_base}")
        print(f"{'=' * 60}")

        anuncios = parse_cards(
            html, slug_base, filtrar_cidade=True, exigir_bairro_alvo=True
        )
        if not anuncios:
            print("  nenhum anúncio de São Paulo encontrado.\n")
            continue

        do_bairro = sum(1 for a in anuncios if bairro_confere(a.endereco, slug_base))
        print(f"  {len(anuncios)} anúncios em SP | {do_bairro} do bairro\n")

        novos = 0
        for a in anuncios:
            if not bairro_confere(a.endereco, slug_base):
                continue
            if db.existe(a.url):
                continue
            if not atende_preco(a):
                continue

            # guarda o bairro REAL que veio no anúncio
            a.bairro = bairro_do_endereco(a.endereco) or slug_base
            calcular_match_quintal(a)
            calcular_financiamento(a)

            db.salvar_anuncio(a)
            novos += 1
            n = 0
            if not args.sem_fotos:
                n = baixar_fotos(a, None, db)

            print(
                f"  [salvo] {a.titulo[:44]!r} | R$ {a.preco:,.0f} | "
                f"quintal={int(a.match_quintal)}({a.score_quintal}) | {n} fotos"
            )

        total_novos += novos
        print(f"  -> {novos} novos neste arquivo\n")

    print(f"{'=' * 60}")
    print(f"Novos anúncios salvos : {total_novos}")
    print(f"Páginas de detalhe    : {total_detalhes}")
    print(f"Total no banco        : {db.total()}")
    db.close()


if __name__ == "__main__":
    main()
