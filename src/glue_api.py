"""Crawler do ZAP Imóveis e do Viva Real.

Os dois são o MESMO inventário. Comprovado: consultando os mesmos filtros, os
30 primeiros anúncios têm exatamente os mesmos IDs nos dois sites. Eles são do
grupo OLX e compartilham a mesma API:

    GET https://glue-api.vivareal.com/v2/listings

Então não existe "crawler do ZAP" e "crawler do VivaReal" — existe um, e o
`portal` gravado no banco diz por qual site você prefere ver o anúncio.

Por que API e não navegador: a API responde a `requests` simples (o site não
bloqueia), devolve JSON estruturado e é muito mais rápida. Verificado.

Por que a API e não a URL de busca: a página do site NÃO filtra bairro. Testado:
  - `/venda/imoveis/sp+sao-paulo/vila-mangalot/`  -> "não encontramos a página"
  - `/venda/imoveis/sp/?onde=...Vila Mangalot...` -> ignorado (mesmos resultados)
  - `/venda/casas/...`                            -> ignorado
Só o parâmetro `addressNeighborhood` da API funciona.

ARMADILHA: `&unitTypes=HOME` é aceito e IGNORADO pela API (598 resultados com e
sem ele). O tipo vem no campo `listing.unitTypes` e a filtragem é feita aqui.
"""

from __future__ import annotations

import json
import re
import time
from typing import Iterator, Optional

import requests

import config
from scraper_browser import Anuncio

GLUE_URL = "https://glue-api.vivareal.com/v2/listings"

# `unitTypes` da API -> tipo interno. É o dado CONFIÁVEL de tipo de imóvel
# (ao contrário da OLX, que só permite inferir pelo título).
_UNIT_CASA = "HOME"
_UNIT_APARTAMENTO = "APARTMENT"

# um listing pode ter mais de um preço (venda + aluguel); pegamos o de venda
_BUSINESS_VENDA = "SALE"


def _headers(portal: str) -> dict:
    """Headers da requisição. O `x-domain` é OBRIGATÓRIO.

    Sem ele a API responde 400 `MISSING-DOMAIN-HEADER`. O valor é o domínio do
    site que você quer "representar" (os dois funcionam, é a mesma base).
    """
    dominio = (
        "www.zapimoveis.com.br" if portal == "zap" else "www.vivareal.com.br"
    )
    return {
        "User-Agent": config.USER_AGENT,
        "Accept": "application/json",
        "x-domain": dominio,
    }


def url_do_anuncio(portal: str, id_anuncio: str | int) -> str:
    """URL pública do anúncio.

    `/imovel/<id>/` redireciona sozinho para a URL canônica com o slug
    descritivo — verificado nos dois sites (HTTP 200).

    O redirecionamento NÃO é o que torna a coleta lenta: medido em 0,66s, e o
    `requests` faz numa única ida (segue o 308 sozinho). O gargalo real é o
    download das fotos (~91 KB cada, ~8 por segundo no CDN).

    Não há slug canônico disponível: a API não devolve campo de URL (só `id`,
    `externalId`, `advertiserId`), então o redirect é o caminho disponível.
    """
    dominio = "www.zapimoveis.com.br" if portal == "zap" else "www.vivareal.com.br"
    return f"https://{dominio}/imovel/{id_anuncio}/"


def url_da_foto(template: str, largura: int = 1200, altura: int = 900) -> str:
    """Resolve o template de foto que a API devolve.

    A API manda algo como
        https://resizedimgs.vivareal.com/img/vr-listing/<hash>/{description}.jpg
            ?action={action}&dimension={width}x{height}
    Os placeholders são preenchidos pelo CLIENTE. Verificado: qualquer valor em
    `description` devolve 200 (a imagem em si é resolvida pelo hash do caminho),
    então usamos "image".
    """
    if not template:
        return ""
    return (
        template.replace("{description}", "image")
        .replace("{action}", "fit-in")
        .replace("{width}", str(largura))
        .replace("{height}", str(altura))
    )


def _primeiro(lista) -> Optional[float]:
    """Primeiro item numérico de uma lista (a API às vezes manda vários)."""
    if isinstance(lista, (int, float)):
        return float(lista)
    if isinstance(lista, list) and lista:
        try:
            return float(lista[0])
        except (TypeError, ValueError):
            return None
    return None


def _preco_venda(listing: dict) -> Optional[float]:
    """Preço de venda do anúncio (ignora os preços de aluguel)."""
    for info in listing.get("pricingInfos") or []:
        if info.get("businessType") == _BUSINESS_VENDA and info.get("price"):
            try:
                return float(info["price"])
            except (TypeError, ValueError):
                continue
    return None


def _areas(listing: dict) -> tuple[Optional[float], Optional[float]]:
    """(área construída, área do terreno).

    `usableAreas` é a área útil/construída. `totalAreas` pode trazer dois
    valores (ex.: [160, 303] = construída e terreno) — quando traz dois, o
    segundo é o terreno.
    """
    construida = _primeiro(listing.get("usableAreas"))
    totais = listing.get("totalAreas") or []

    terreno = None
    if isinstance(totais, list) and len(totais) >= 2:
        try:
            terreno = float(totais[1])
        except (TypeError, ValueError):
            terreno = None

    if construida is None:
        construida = _primeiro(totais)
    if terreno is not None and construida is not None and terreno <= construida:
        terreno = None  # não é terreno de verdade
    return construida, terreno


def _endereco(listing: dict) -> tuple[str, str]:
    """(endereco "Bairro, Cidade", rua)."""
    addr = listing.get("address") or {}
    bairro = (addr.get("neighborhood") or "").strip()
    cidade = (addr.get("city") or "").strip()
    rua = (addr.get("street") or "").strip()
    numero = (addr.get("streetNumber") or "").strip()
    if rua and numero:
        rua = f"{rua}, {numero}"
    if bairro and cidade:
        endereco = f"{bairro}, {cidade}"
    else:
        endereco = bairro or cidade
    return endereco, rua


def _fotos(item: dict) -> list[str]:
    """URLs das fotos do anúncio."""
    urls = []
    for media in item.get("medias") or []:
        if media.get("type") and media["type"] != "IMAGE":
            continue
        u = url_da_foto(media.get("url", ""))
        if u:
            urls.append(u)
    return list(dict.fromkeys(urls))


def item_para_anuncio(item: dict, portal: str) -> Optional[Anuncio]:
    """Converte um item da API em `Anuncio`. None se não for casa."""
    listing = item.get("listing") or {}
    tipos = listing.get("unitTypes") or []

    # tipo confiável — dá para excluir apartamento sem heurística
    if _UNIT_APARTAMENTO in tipos:
        return None
    if _UNIT_CASA not in tipos:
        # CONDOMINIUM, COMMERCIAL_PROPERTY… não é o que buscamos
        return None

    id_anuncio = listing.get("id")
    if not id_anuncio:
        return None

    endereco, rua = _endereco(listing)
    construida, terreno = _areas(listing)
    addr = listing.get("address") or {}

    a = Anuncio(
        url=url_do_anuncio(portal, id_anuncio),
        titulo=(listing.get("h2Tag") or "").strip() or _titulo_gerado(listing),
        endereco=endereco,
        rua=rua,
        preco=_preco_venda(listing),
        area_construida=construida,
        area_terreno=terreno,
        quartos=_int(listing.get("bedrooms")),
        banheiros=_int(listing.get("bathrooms")),
        vagas=_int(listing.get("parkingSpaces")),
        portal=portal,
        fotos_urls=_fotos(item),
        cep=(addr.get("zipCode") or "").strip(),
    )
    a.bairro = (addr.get("neighborhood") or "").strip()
    # guardamos a contagem de anúncios repetidos que o PRÓPRIO site informa:
    # é sinal direto para o agrupamento de duplicatas.
    a.listing_count = int(listing.get("listingsCount") or 1)
    return a


def _titulo_gerado(listing: dict) -> str:
    """Título quando a API não manda um (nem todo anúncio tem `h2Tag`)."""
    partes = []
    if _int(listing.get("bedrooms")):
        partes.append(f"{_int(listing['bedrooms'])} quartos")
    _, terreno = _areas(listing)
    if terreno:
        partes.append(f"{terreno:.0f} m² de terreno")
    elif _primeiro(listing.get("usableAreas")):
        partes.append(f"{_primeiro(listing['usableAreas']):.0f} m²")
    bairro = (listing.get("address") or {}).get("neighborhood") or ""
    base = "Casa" + (" com " + " e ".join(partes) if partes else "")
    return f"{base} em {bairro}".strip() if bairro else base


def _int(lista) -> Optional[int]:
    v = _primeiro(lista)
    return int(v) if v is not None else None


# ---------------------------------------------------------------------------
# Busca
# ---------------------------------------------------------------------------
def buscar_pagina(bairro: str, pagina: int = 1, tamanho: int | None = None,
                  portal: str = "zap", cidade: str | None = None
                  ) -> tuple[list[Anuncio], int]:
    """Busca uma página de anúncios de um bairro.

    Devolve (lista de Anuncios, total disponível no site).

    `pagina` começa em 1; internamente vira `from = (pagina-1) * tamanho`.
    O `size` da API tem LIMITE (30 funciona, 32 devolve HTTP 400), por isso o
    padrão vem de `config.GLUE_PAGINA_TAMANHO` — não fixar número aqui.
    """
    if tamanho is None:
        tamanho = config.GLUE_PAGINA_TAMANHO
    tamanho = min(tamanho, config.GLUE_PAGINA_TAMANHO)

    params = {
        "business": _BUSINESS_VENDA,
        "listingType": "USED",
        "categoryPage": "RESULT",
        "addressCity": cidade or config.NOME_CIDADE,
        "addressState": config.NOME_ESTADO,
        "addressNeighborhood": bairro,
        "size": tamanho,
        "from": (pagina - 1) * tamanho,
        # `search` sozinho traz tudo. NÃO montar aninhado
        # (`search(result(totalCount))` com espaço devolve 400).
        "includeFields": "search",
    }

    # Filtro de preço NA API. Verificado: `priceMax=1000000` derruba o total
    # de 595 para 466 e o maior preço da página passa de R$ 1.090.000 para
    # R$ 990.000. Sem isso a API devolveria (e nós baixaríamos fotos de)
    # anúncios que seriam descartados logo em seguida.
    # O nome importa: `priceRangeMax` é aceito e IGNORADO.
    if config.PRECO_MAX:
        params["priceMax"] = int(config.PRECO_MAX)
    if config.PRECO_MIN:
        params["priceMin"] = int(config.PRECO_MIN)

    r = requests.get(
        GLUE_URL, params=params, headers=_headers(portal),
        timeout=config.TIMEOUT,
    )
    if r.status_code != 200:
        raise RuntimeError(f"HTTP {r.status_code}: {r.text[:200]}")

    dados = r.json().get("search") or {}
    total = int(dados.get("totalCount") or 0)
    itens = ((dados.get("result") or {}).get("listings")) or []

    anuncios = []
    for item in itens:
        a = item_para_anuncio(item, portal)
        if a is not None:
            anuncios.append(a)
    return anuncios, total


def coletar_bairro(bairro: str, portal: str = "zap",
                   max_paginas: int | None = None,
                   quieto: bool = True) -> Iterator[Anuncio]:
    """Itera pelos anúncios de um bairro, paginando até acabar ou atingir o teto.

    `quieto=True` (padrão) não imprime nada por página: quem chama costuma ter
    uma barra de progresso desenhando na mesma linha, e um `print` no meio
    quebraria a barra. Use `quieto=False` para acompanhar página a página.
    """
    limite = max_paginas if max_paginas is not None else config.GLUE_MAX_PAGINAS
    tamanho = config.GLUE_PAGINA_TAMANHO
    vistos: set[str] = set()

    for pagina in range(1, limite + 1):
        anuncios, total = buscar_pagina(bairro, pagina, tamanho, portal)

        if not quieto:
            if not anuncios and pagina == 1:
                # pode ser só apartamentos na página; avisa e segue
                print(f"  [aviso] nenhuma casa na página {pagina} ({bairro})")
            if total:
                print(f"  pág {pagina}: {len(anuncios)} casas "
                      f"(de {total} imóveis no site, inclui apartamentos)")

        novos = 0
        for a in anuncios:
            if a.url in vistos:
                continue
            vistos.add(a.url)
            novos += 1
            yield a

        if novos == 0:
            break  # página repetida ou vazia: acabou
        if pagina * tamanho >= total:
            break
        if pagina < limite:
            time.sleep(config.GLUE_DELAY_S)


def tem_api(portal: str = "zap") -> bool:
    """Testa rapidamente se a API responde (usado pelo --dry-run)."""
    try:
        buscar_pagina("Vila Mangalot", pagina=1, tamanho=1, portal=portal)
        return True
    except Exception:  # noqa: BLE001
        return False
