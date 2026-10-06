"""Crawler da OLX (Playwright).

Por que Playwright e não `requests`: a OLX bloqueia requisições simples
(HTTP 451 em alguns recursos) e monta a listagem via JavaScript. O Chromium
real renderiza tudo e ainda reaproveita os cookies do perfil persistente.

Por que o caminho é diferente do Imovelweb: a OLX **não tem URL por bairro**.
Anexar o bairro no caminho (`/sao-paulo-e-regiao/vila-mangalot`) é aceito mas
não filtra nada — devolve a região inteira sem avisar. Verificado: veio
São Caetano do Sul, Santana de Parnaíba, Santo Amaro, Vila Mariana.

Quem filtra é a busca textual (`?q=`), e mesmo ela é tolerante: "pirituba"
traz Jardim Íris e Vila Barreto. Então o fluxo é o MESMO do Imovelweb —
buscar amplo e **validar o bairro pelo endereço de cada anúncio**
(`e_bairro_alvo`). Isso acaba ajudando: uma busca por bairro captura também
anúncios de outros bairros-alvo da mesma região.

Sobre os dados: a página de detalhe publica um **JSON-LD** (schema.org) com
título, descrição COMPLETA, preço, cidade e CEP. É muito mais confiável que
raspar o DOM, então o parser prefere o JSON-LD e só cai no DOM como reserva.

Seletores da listagem (verificados em 2026-10):
  card        -> section.olx-adcard
  link        -> a[data-testid="adcard-link"]
  título      -> .olx-adcard__title
  preço       -> .olx-adcard__price
  local       -> .olx-adcard__location   ("Cidade, Bairro" — ATENÇÃO à ordem)
  detalhes    -> .olx-adcard__detail     (aria-label: "110 metros quadrados")
  foto        -> img[src*="img.olx.com.br"]
"""

from __future__ import annotations

import json
import re
import time

from bs4 import BeautifulSoup

from cacaimoveis import config, logs
from cacaimoveis.scraper_browser import Anuncio, bairro_confere, calcular_match_quintal

log = logs.obter(__name__)

PORTAL = "olx"

CARD_SELECTOR = "section.olx-adcard"
_LINK_SELECTOR = 'a[data-testid="adcard-link"]'

# A URL do anúncio termina em "-<id numérico longo>". Serve para descartar
# links de navegação que também contêm "/imoveis/".
_RE_ID_URL = re.compile(r"-(\d{8,})(?:\?|$)")
_RE_ID_QUALQUER = re.compile(r"(\d{8,})")


# ---------------------------------------------------------------------------
# Utilitários
# ---------------------------------------------------------------------------
def _texto(no) -> str:
    return re.sub(r"\s+", " ", no.get_text(" ", strip=True)).strip() if no else ""


def id_do_anuncio(url: str) -> str:
    """Extrai o ID numérico do anúncio a partir da URL."""
    m = _RE_ID_QUALQUER.search(url or "")
    return m.group(1) if m else ""


def montar_url(bairro: str, pagina: int = 1) -> str:
    """URL de busca da OLX para um bairro, em uma página.

    Sempre pela busca textual (`?q=`) — o caminho por bairro NÃO filtra.
    A página 1 não leva `?o=1` (o parâmetro é omitido quando é a primeira).
    """
    q = bairro.strip()
    url = f"{config.OLX_BASE}{config.OLX_REGIAO}?q={q.replace(' ', '+')}"
    if pagina > 1:
        url += f"&o={pagina}"
    return url


def _para_float(valor) -> float | None:
    """Converte 'R$ 1.356.300' ou 456000 em float."""
    if valor is None:
        return None
    if isinstance(valor, (int, float)):
        return float(valor)
    m = re.search(r"([\d\.]+)(?:,(\d{2}))?", str(valor))
    if not m:
        return None
    inteiro = m.group(1).replace(".", "")
    centavos = m.group(2) or "00"
    try:
        return float(f"{inteiro}.{centavos}")
    except ValueError:
        return None


def _primeiro_int(texto: str) -> int | None:
    m = re.search(r"(\d+)", texto or "")
    return int(m.group(1)) if m else None


# ---------------------------------------------------------------------------
# Endereço: a OLX escreve "Cidade, Bairro" — o INVERSO do Imovelweb
# ---------------------------------------------------------------------------
def endereco_para_padrao(local: str, cidade_uf: str = "") -> str:
    """Converte o local da OLX para o padrão interno "Bairro, Cidade".

    A OLX mostra "São Paulo, Vila Mangalot"; o resto do projeto (validação de
    cidade, `bairro_do_endereco`, `e_bairro_alvo`) espera "Bairro, Cidade".

    Converto apenas quando a primeira parte é reconhecidamente a cidade
    (bate com `config.NOME_CIDADE` ou alguma de `config.CIDADES_ACEITAS`);
    caso contrário mantenho a ordem original, para não trocar as partes por
    engano e estragar a validação de bairro.
    """
    local = _texto_ou(local)
    if not local:
        return cidade_uf

    partes = [p.strip() for p in local.split(",") if p.strip()]
    if len(partes) >= 2:
        primeira = _sem_acento(partes[0]).lower().strip()
        nomes_cidade = {_sem_acento(config.NOME_CIDADE).lower()}
        nomes_cidade |= {_sem_acento(c).lower() for c in config.CIDADES_ACEITAS}

        if any(primeira == n or primeira.startswith(n) for n in nomes_cidade):
            # "Cidade, Bairro" -> "Bairro, Cidade"
            return f"{', '.join(partes[1:])}, {partes[0]}"

    # já está no padrão, ou é uma referência de região ("Zona Norte")
    if cidade_uf and ", " not in local:
        return f"{local}, {cidade_uf}"
    return local


def _texto_ou(valor) -> str:
    return re.sub(r"\s+", " ", str(valor or "")).strip()


def _sem_acento(texto: str) -> str:
    import unicodedata

    nfkd = unicodedata.normalize("NFKD", texto or "")
    return "".join(c for c in nfkd if not unicodedata.combining(c))


# ---------------------------------------------------------------------------
# Parsing da listagem
# ---------------------------------------------------------------------------
def _detalhes_do_card(card) -> dict:
    """Extrai área/quartos/banheiros/vagas dos divs com aria-label.

    Cada detalhe é `<div class="olx-adcard__detail" aria-label="110 metros
    quadrados">`. O aria-label é bem mais estável que o texto interno (que
    contém o SVG do ícone).
    """
    dados: dict = {}
    for el in card.select(".olx-adcard__detail"):
        rotulo = (el.get("aria-label") or "").lower()
        if not rotulo:
            continue
        if "metro" in rotulo or "m²" in rotulo:
            dados.setdefault("area", _primeiro_int(rotulo))
        elif "quarto" in rotulo:
            dados.setdefault("quartos", _primeiro_int(rotulo))
        elif "banheiro" in rotulo:
            dados.setdefault("banheiros", _primeiro_int(rotulo))
        elif "vaga" in rotulo or "garagem" in rotulo:
            dados.setdefault("vagas", _primeiro_int(rotulo))
        elif "su" in rotulo and "te" in rotulo:  # suíte
            dados.setdefault("suites", _primeiro_int(rotulo))
    return dados


def _fotos_do_card(card) -> list[str]:
    """URLs das fotos visíveis no carrossel do card."""
    urls: list[str] = []
    for img in card.select("img"):
        src = img.get("src") or ""
        if "img.olx.com.br" in src:
            # a listagem serve miniaturas; peço a versão grande do CDN
            urls.append(_foto_grande(src))
    return list(dict.fromkeys(urls))  # dedup preservando a ordem


def _foto_grande(url: str) -> str:
    """Transforma a miniatura do card na imagem grande usada pela galeria.

    O card serve `thumbs700x500/<p>/<arquivo>.webp`; a versão grande fica em
    `/images/<p>/<arquivo>.jpg`. Verificado no CDN: o `.jpg` de `/images/` é a
    maior (143 KB contra 97 KB do webp), e é o mesmo formato que o JSON-LD da
    página de detalhe publica — então listagem e galeria produzem URLs
    idênticas para a MESMA foto, o que ajuda na deduplicação por pHash.

    O `.png` não existe (404), por isso o `.jpg` é fixo.
    """
    base = url.split("?")[0]
    base = re.sub(r"/thumbs\d+x\d+/", "/images/", base)
    raiz, _, _ext = base.rpartition(".")
    return f"{raiz}.jpg" if raiz else base


def parse_cards(html: str, bairro: str, cidade_uf: str = "") -> list[Anuncio]:
    """Extrai os anúncios de uma página de listagem da OLX.

    Não filtra bairro aqui — a busca por bairro da OLX é tolerante e traz
    vizinhos, então a filtragem fica em `e_bairro_alvo` (igual ao Imovelweb).
    """
    sopa = BeautifulSoup(html, "html.parser")
    anuncios: list[Anuncio] = []
    vistos: set[str] = set()

    for card in sopa.select(CARD_SELECTOR):
        link = card.select_one(_LINK_SELECTOR)
        if link is None:
            continue
        url = (link.get("href") or "").split("?")[0]
        if not url or not _RE_ID_URL.search(url) or url in vistos:
            continue
        vistos.add(url)

        titulo = _texto(card.select_one(".olx-adcard__title")) or _texto(
            link.get("title")
        )
        preco_txt = _texto(card.select_one(".olx-adcard__price"))
        local = _texto(card.select_one(".olx-adcard__location"))
        detalhes = _detalhes_do_card(card)

        a = Anuncio(
            url=url,
            titulo=titulo,
            endereco=endereco_para_padrao(local, cidade_uf),
            preco=_para_float(preco_txt),
            quartos=detalhes.get("quartos"),
            banheiros=detalhes.get("banheiros"),
            vagas=detalhes.get("vagas"),
            portal=PORTAL,
            fotos_urls=_fotos_do_card(card),
        )
        # A OLX não separa rua no card; o `endereco` já é "Bairro, Cidade"
        a.bairro = bairro
        # A OLX chama de "metros quadrados" a área ÚTIL/construída do anúncio.
        if detalhes.get("area"):
            a.area_construida = float(detalhes["area"])
        anuncios.append(a)

    return anuncios


# ---------------------------------------------------------------------------
# Parsing da página de detalhe (JSON-LD primeiro, DOM como reserva)
# ---------------------------------------------------------------------------
def _json_ld(html: str) -> dict:
    """Devolve o primeiro bloco JSON-LD do tipo BuyAction/Product."""
    sopa = BeautifulSoup(html, "html.parser")
    for script in sopa.select('script[type="application/ld+json"]'):
        try:
            dados = json.loads(script.string or "")
        except (json.JSONDecodeError, TypeError):
            continue
        if isinstance(dados, dict) and dados.get("@context", "").startswith(
            "http://schema.org"
        ) or isinstance(dados, dict) and "schema.org" in str(
            dados.get("@context", "")
        ):
            return dados
    return {}


def _limpar_descricao(bruta: str) -> str:
    """O JSON-LD traz a descrição em HTML (<br>, tags) — vira texto puro."""
    if not bruta:
        return ""
    texto = str(bruta).replace("<br>", "\n").replace("<br/>", "\n")
    texto = BeautifulSoup(texto, "html.parser").get_text("\n", strip=True)
    texto = re.sub(r"\n{3,}", "\n\n", texto)
    return texto.strip()


def parse_detalhe(html: str, anuncio: Anuncio) -> Anuncio:
    """Completa o anúncio com os dados da página individual da OLX."""
    dados = _json_ld(html)

    if not dados:
        # Sem JSON-LD: tenta o mínimo pelo DOM, para não perder a visita.
        sopa = BeautifulSoup(html, "html.parser")
        h1 = _texto(sopa.select_one("h1"))
        if h1 and len(h1) > len(anuncio.titulo or ""):
            anuncio.titulo = h1
        corpo = _texto(sopa.select_one("body"))
        if len(corpo) > len(anuncio.descricao or ""):
            anuncio.descricao = corpo[:8000]
        return anuncio

    obj = dados.get("Object") or {}

    # descrição completa (o card só traz o título)
    descricao = _limpar_descricao(obj.get("description", ""))
    if len(descricao) > len(anuncio.descricao or ""):
        anuncio.descricao = descricao

    nome = _texto_ou(obj.get("name"))
    if len(nome) > len(anuncio.titulo or ""):
        anuncio.titulo = nome

    # endereço oficial: cidade + UF + CEP
    end = ((dados.get("location") or {}).get("address") or {})
    cidade = _texto_ou(end.get("addressLocality"))
    cep = _texto_ou(end.get("postalCode"))

    if cidade and not anuncio.endereco:
        bairro = anuncio.bairro or ""
        anuncio.endereco = f"{bairro}, {cidade}" if bairro else cidade
    if cep:
        anuncio.cep = cep

    # preço (o card pode ter vindo sem preço)
    if anuncio.preco is None:
        preco = (dados.get("priceSpecification") or {}).get("price")
        if preco is None:
            preco = (dados.get("offers") or {}).get("price")
        anuncio.preco = _para_float(preco)

    # galeria completa — o card mostra só as primeiras
    imagens = obj.get("image") or []
    if isinstance(imagens, dict):
        imagens = [imagens]
    urls = []
    for item in imagens:
        if isinstance(item, dict):
            u = item.get("contentUrl") or item.get("url")
        else:
            u = item
        if u:
            urls.append(str(u).split("?")[0])
    urls = list(dict.fromkeys(urls))
    if len(urls) > len(anuncio.fotos_urls or []):
        anuncio.fotos_urls = urls

    return anuncio


# ---------------------------------------------------------------------------
# Coleta (Playwright)
# ---------------------------------------------------------------------------
def _e_bloqueio(page) -> bool:
    """Detecta página de bloqueio/desafio da OLX."""
    try:
        titulo = (page.title() or "").lower()
    except Exception:  # noqa: BLE001
        return False
    sinais = ("acesso negado", "access denied", "um momento", "just a moment",
              "verificação", "blocked", "captcha")
    return any(s in titulo for s in sinais)


def _abrir(page, url: str, tentativas: int = 3, quieto: bool = False) -> str | None:
    """Abre uma URL tratando bloqueio. Devolve o HTML ou None."""
    for tentativa in range(1, tentativas + 1):
        try:
            resp = page.goto(url, wait_until="domcontentloaded",
                             timeout=config.NAV_TIMEOUT_MS)
        except Exception as e:  # noqa: BLE001
            log.warning("erro tratado, a execução segue: %s", e, exc_info=True)
            if not quieto:
                print(f"  [erro] {str(e)[:90]}")
            time.sleep(4 * tentativa)
            continue

        if _e_bloqueio(page):
            if not quieto:
                print(f"  [bloqueio] tentativa {tentativa}, aguardando...")
            page.wait_for_timeout(config.CHALLENGE_TIMEOUT_MS)

        if resp is not None and resp.status < 400 and not _e_bloqueio(page):
            # a listagem monta os cards via JS depois do load
            page.wait_for_timeout(2500)
            return page.content()

        if not quieto:
            print(f"  [tentativa {tentativa}] status {resp.status if resp else '?'}")
        page.wait_for_timeout(4000)
    return None


def coletar_bairro(page, bairro, max_paginas: int | None = None,
                   quieto: bool = True):
    """Itera pelas páginas de busca de um bairro e gera Anuncios.

    `bairro` pode ser um `config.Bairro` ou uma string. Sempre usa a busca
    textual — o caminho por bairro da OLX não filtra.

    `quieto=True` (padrão) não imprime por página: quem chama costuma estar
    desenhando uma barra de progresso na mesma linha.
    """
    if isinstance(bairro, str):
        termo = bairro
        nome = bairro
    else:
        termo = bairro.nome  # nome com acento busca melhor que o slug
        nome = bairro.nome

    limite = max_paginas if max_paginas is not None else config.OLX_MAX_PAGINAS

    for pagina in range(1, limite + 1):
        url = montar_url(termo, pagina)
        if not quieto:
            print(f"[olx] {nome} | pág {pagina}: {url}")

        html = _abrir(page, url)
        if html is None:
            if not quieto:
                print("  [falhou] não consegui abrir. Pulando o resto do bairro.")
            break

        anuncios = parse_cards(html, termo, config.NOME_CIDADE)
        if not anuncios:
            if not quieto:
                print("  [vazio] nenhum card nesta página.")
            break

        if not quieto:
            do_bairro = sum(1 for a in anuncios if bairro_confere(a.endereco, termo))
            print(f"  {len(anuncios)} anúncios ({do_bairro} do bairro buscado)")

        yield from anuncios

        if pagina < limite:
            time.sleep(config.DELAY_MIN)


def enriquecer_detalhe(page, anuncio: Anuncio, pausa: bool = True) -> str:
    """Abre a página individual e completa o anúncio. Devolve um resumo."""
    antes_desc = len(anuncio.descricao or "")
    antes_fotos = len(anuncio.fotos_urls or [])

    html = _abrir(page, anuncio.url)
    if html is None:
        return "falha ao abrir"

    parse_detalhe(html, anuncio)
    calcular_match_quintal(anuncio)

    partes = []
    if len(anuncio.descricao or "") > antes_desc:
        partes.append(f"descrição {antes_desc}->{len(anuncio.descricao)}")
    if len(anuncio.fotos_urls or []) > antes_fotos:
        partes.append(f"fotos {antes_fotos}->{len(anuncio.fotos_urls)}")
    if anuncio.cep:
        partes.append(f"cep={anuncio.cep}")

    if pausa:
        time.sleep(config.DELAY_MIN)
    return " | ".join(partes) if partes else "sem novidade"
