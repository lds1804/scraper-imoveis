"""Crawler do QuintoAndar.

O QuintoAndar é independente do grupo OLX (ZAP/VivaReal), então tem inventário
próprio e vale a pena coletar.

Por que API e não navegador: a API responde a `requests` simples, sem cookie e
sem token, devolvendo JSON estruturado. Verificado.

Endpoint (descoberto interceptando a rede do site):
    POST https://apigw.prod.quintoandar.com.br/house-listing-search/v3/search/list

Diferença importante em relação à OLX: aqui o filtro de tipo FUNCIONA
(`filters.houseSpecs.houseTypes: ["HOUSE"]`), então não é preciso inferir casa
x apartamento pelo título.

Sobre a URL de busca do site: `/comprar/imovel/<slug>/casa` filtra de verdade
(11 cards, todos casa, todos no bairro) — diferente do ZAP/VivaReal, cuja URL
de bairro não filtra. Mas usamos a API porque é mais rápida e traz mais campos.
"""

from __future__ import annotations

from collections.abc import Iterator

import requests

from cacaimoveis import config
from cacaimoveis.scraper_browser import Anuncio

# Campos pedidos à API. A lista é explícita de propósito: pedir tudo devolve um
# payload muito maior e mais lento, sem ganho.
_CAMPOS = [
    "id",
    "coverImage",
    "salePrice",
    "area",
    "imageList",
    "address",
    "regionName",
    "neighbourhood",
    "city",
    "type",
    "bedrooms",
    "suites",
    "bathrooms",
    "parkingSpaces",
    "amenities",
    "listingTags",
    "categories",
]

# tipo que queremos (o filtro é aplicado pela própria API)
_TIPO_CASA = "HOUSE"


def montar_slug(bairro: str, cidade: str | None = None, uf: str | None = None) -> str:
    """Monta o slug no formato que a API espera.

    Ex.: "Vila Mangalot" -> "vila-mangalot-sao-paulo-sp-brasil"
    """
    from cacaimoveis.scraper_browser import _sem_acento

    cidade = cidade or config.NOME_CIDADE
    uf = uf or config.UF
    partes = [bairro, cidade, uf, "brasil"]
    texto = "-".join(partes)
    texto = _sem_acento(texto).lower()
    return "-".join(p for p in texto.replace(" ", "-").split("-") if p)


def url_do_anuncio(id_anuncio: str | int) -> str:
    """URL pública do anúncio.

    Precisa do `/comprar/` no fim: `/imovel/<id>/` sozinho cai em "alugar"
    (verificado — redireciona para a versão de aluguel).
    """
    return f"https://www.quintoandar.com.br/imovel/{id_anuncio}/comprar/"


def url_da_foto(nome: str) -> str:
    """URL da foto.

    A API devolve só o NOME do arquivo (ex.: `894937641-544.40048...MG3534.jpg`);
    o caminho é montado com o prefixo do site. Verificado: o `srcset` da página
    usa exatamente `/img/crop/landscape/1200x800/<arquivo>`.
    """
    if not nome:
        return ""
    if nome.startswith("http"):
        return nome
    return config.QUINTO_FOTO_BASE + nome.lstrip("/")


def _corpo(slug: str, pagina: int, tamanho: int) -> dict:
    """Corpo da requisição de busca."""
    return {
        "slug": slug,
        "topics": [],
        "fields": _CAMPOS,
        "sorting": {"criteria": "RELEVANCE"},
        "pagination": {"pageSize": tamanho, "offset": (pagina - 1) * tamanho},
        "context": {
            "listShowing": True,
            "mapShowing": False,
            "numPhotos": 12,
            "isSSR": True,
        },
        "filters": {
            "businessContext": "SALE",
            # a API usa as coordenadas do centro da região para o raio de busca
            "location": {
                "coordinate": {
                    "lat": config.QUINTO_CENTRO[0],
                    "lng": config.QUINTO_CENTRO[1],
                },
                "countryCode": "BR",
            },
            "priceRange": [],
            "availability": "ANY",
            "occupancy": "ANY",
            "houseSpecs": {
                "houseTypes": [_TIPO_CASA],   # <- filtra casa x apartamento
                "area": {"range": {}},
                "amenities": [],
                "installations": [],
                "bathrooms": {"range": {}},
                "bedrooms": {"range": {}},
                "parkingSpace": {"range": {}},
                "suites": {"range": {}},
            },
            "origin": "HYBRID",
        },
        "locationDescriptions": [{"description": slug}],
    }


def item_para_anuncio(src: dict) -> Anuncio | None:
    """Converte um `_source` da API em `Anuncio`."""
    id_anuncio = src.get("id")
    if not id_anuncio:
        return None

    # a API já filtra por HOUSE, mas conferimos: `type` vem em português
    tipo = (src.get("type") or "").strip().lower()
    if tipo and tipo not in ("casa", "sobrado", "casa de vila", "casa de condomínio",
                             "casa de condominio"):
        return None

    bairro = (src.get("neighbourhood") or src.get("regionName") or "").strip()
    cidade = (src.get("city") or config.NOME_CIDADE).strip()
    rua = (src.get("address") or "").strip()

    a = Anuncio(
        url=url_do_anuncio(id_anuncio),
        titulo=_titulo(src),
        endereco=f"{bairro}, {cidade}" if bairro else cidade,
        rua=rua,
        preco=_num(src.get("salePrice")),
        area_construida=_num(src.get("area")),
        quartos=_int(src.get("bedrooms")),
        banheiros=_int(src.get("bathrooms")),
        vagas=_int(src.get("parkingSpaces")),
        portal="quintoandar",
        fotos_urls=[url_da_foto(n) for n in (src.get("imageList") or []) if n],
        cep="",
    )
    # o QuintoAndar não publica terreno nem CEP na busca; `area` é a construída
    a.bairro = bairro
    a.suites = _int(src.get("suites"))
    a.amenities = list(src.get("amenities") or [])
    return a


def _titulo(src: dict) -> str:
    """Monta um título descritivo (a API não manda título pronto)."""
    partes = []
    if _int(src.get("area")):
        partes.append(f"{_int(src['area'])} m²")
    if _int(src.get("bedrooms")):
        partes.append(f"{_int(src['bedrooms'])} quartos")
    if _int(src.get("parkingSpaces")):
        partes.append(f"{_int(src['parkingSpaces'])} vagas")
    bairro = (src.get("neighbourhood") or "").strip()
    base = "Casa" + (" com " + ", ".join(partes) if partes else "")
    return f"{base} - {bairro}".strip(" -") if bairro else base


def _num(v) -> float | None:
    try:
        return float(v) if v is not None else None
    except (TypeError, ValueError):
        return None


def _int(v) -> int | None:
    f = _num(v)
    return int(f) if f is not None else None


# ---------------------------------------------------------------------------
# Busca
# ---------------------------------------------------------------------------
def buscar_pagina(slug: str, pagina: int = 1,
                  tamanho: int | None = None) -> tuple[list[Anuncio], int]:
    """Busca uma página. Devolve (anuncios, total_de_casas)."""
    if tamanho is None:
        tamanho = config.QUINTO_PAGINA_TAMANHO

    r = requests.post(
        config.QUINTO_API,
        headers={
            "User-Agent": config.USER_AGENT,
            "Accept": "application/json",
            "Content-Type": "application/json",
            "Origin": "https://www.quintoandar.com.br",
        },
        json=_corpo(slug, pagina, tamanho),
        timeout=config.TIMEOUT,
    )
    if r.status_code != 200:
        raise RuntimeError(f"HTTP {r.status_code}: {r.text[:200]}")

    dados = r.json()
    hits = (dados.get("hits") or {})
    total_info = hits.get("total") or {}
    # `value` conta apartamentos também; `transactional_value` é o estoque que
    # interessa aqui. Uso `value` como teto conservador para a paginação.
    total = int(total_info.get("value") or 0)

    itens = hits.get("hits") or []
    anuncios = []
    for h in itens:
        a = item_para_anuncio(h.get("_source") or {})
        if a is not None:
            anuncios.append(a)
    return anuncios, total


def coletar_bairro(bairro: str, max_paginas: int | None = None,
                   quieto: bool = True) -> Iterator[Anuncio]:
    """Itera pelos anúncios de um bairro, paginando até acabar.

    `quieto=True` (padrão) não imprime por página: quem chama costuma estar
    desenhando uma barra de progresso na mesma linha.
    """
    slug = montar_slug(bairro)
    limite = max_paginas if max_paginas is not None else config.QUINTO_MAX_PAGINAS
    vistos: set[str] = set()

    for pagina in range(1, limite + 1):
        anuncios, total = buscar_pagina(slug, pagina)
        if not quieto:
            print(f"  pág {pagina}: {len(anuncios)} casas (slug={slug})")

        novos = 0
        for a in anuncios:
            if a.url in vistos:
                continue
            vistos.add(a.url)
            novos += 1
            yield a

        if novos == 0:
            break
        if pagina * config.QUINTO_PAGINA_TAMANHO >= total:
            break


def tem_api() -> bool:
    """Testa rapidamente se a API responde (usado pelo --dry-run)."""
    try:
        buscar_pagina(montar_slug("Vila Mangalot"), 1, 1)
        return True
    except Exception:  # noqa: BLE001
        return False
