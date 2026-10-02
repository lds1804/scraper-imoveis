"""Scraper do Imovelweb.

Estratégia:
- Monta URLs de busca por bairro (venda, casas, faixa de preço).
- Faz requisições com headers realistas + rate limiting + retry.
- Como o site usa proteção anti-bot, salvamos o HTML bruto em `debug_html/`
  quando não conseguimos achar os cards, para inspecionar os seletores reais.
"""

from __future__ import annotations

import os
import random
import re
import time
from typing import Iterator, Optional

import requests
from bs4 import BeautifulSoup

import config
from storage import Anuncio

DEBUG_DIR = "debug_html"


# ---------------------------------------------------------------------------
# Sessão HTTP
# ---------------------------------------------------------------------------
def criar_session() -> requests.Session:
    s = requests.Session()
    s.headers.update(
        {
            "User-Agent": config.USER_AGENT,
            "Accept": (
                "text/html,application/xhtml+xml,application/xml;q=0.9,"
                "image/avif,image/webp,*/*;q=0.8"
            ),
            "Accept-Language": "pt-BR,pt;q=0.9,en;q=0.8",
            "Accept-Encoding": "gzip, deflate, br",
            "Connection": "keep-alive",
            "Upgrade-Insecure-Requests": "1",
        }
    )
    return s


def _sleep() -> None:
    time.sleep(random.uniform(config.DELAY_MIN, config.DELAY_MAX))


def get_html(session: requests.Session, url: str) -> Optional[str]:
    """GET com retry e backoff. Salva HTML em debug quando bloqueado."""
    for tentativa in range(1, config.MAX_RETRIES + 1):
        try:
            r = session.get(url, timeout=config.TIMEOUT)
            if r.status_code == 200:
                return r.text
            print(f"  [HTTP {r.status_code}] {url} (tentativa {tentativa})")
            if r.status_code in (403, 429):
                _salvar_debug(url, r.text, f"http_{r.status_code}")
        except Exception as e:  # noqa: BLE001
            print(f"  [erro] {url}: {e} (tentativa {tentativa})")

        if tentativa < config.MAX_RETRIES:
            time.sleep(2 ** tentativa + random.random())

    return None


def _salvar_debug(url: str, html: str, sufixo: str = "") -> None:
    os.makedirs(DEBUG_DIR, exist_ok=True)
    nome = re.sub(r"[^a-zA-Z0-9]+", "_", url)[-80:] + f"_{sufixo}.html"
    caminho = os.path.join(DEBUG_DIR, nome)
    with open(caminho, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"  [debug] HTML salvo em {caminho}")


# ---------------------------------------------------------------------------
# Montagem de URLs
# ---------------------------------------------------------------------------
def montar_url(bairro: str, pagina: int = 1) -> str:
    """Monta a URL de busca do Imovelweb para um bairro e página."""
    slug = f"{config.TIPO}-{config.OPERACAO}-{bairro}-sao-paulo-sp"
    if pagina > 1:
        slug += f"-pagina-{pagina}"
    return f"{config.BASE_URL}/{slug}.html"


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------
def extrair_links_anuncios(html: str) -> list[str]:
    """Extrai URLs de anúncios individuais da página de listagem."""
    soup = BeautifulSoup(html, "lxml")
    links: list[str] = []
    vistos = set()

    # O Imovelweb usa cards com <a> para a página do imóvel.
    # Padrão típico da URL de anúncio: contém um id numérico no final.
    for a in soup.select("a[href]"):
        href = a.get("href", "")
        if not href:
            continue
        if re.search(r"\.html$", href) and any(
            p in href for p in ["/casa-", "/imovel-", "/propriedade"]
        ):
            # ignora links de listagem/paginação
            if any(x in href for x in ["-pagina-", "-venda-", "-aluguel-"]):
                # pode ser listagem, mas também pode ser anúncio; filtramos depois
                pass
            full = href if href.startswith("http") else config.BASE_URL + href
            if full not in vistos:
                vistos.add(full)
                links.append(full)

    return links


def parse_anuncio(html: str, url: str) -> Anuncio:
    """Extrai os dados de um anúncio a partir do HTML da página dele."""
    soup = BeautifulSoup(html, "lxml")
    a = Anuncio(url=url)

    # Título
    h1 = soup.find("h1")
    if h1:
        a.titulo = h1.get_text(strip=True)

    texto = soup.get_text(" ", strip=True)
    a.descricao = texto[:5000]

    # Preço
    m = re.search(r"R\$\s*([\d\.]+)(?:,(\d{2}))?", texto)
    if m:
        inteiro = m.group(1).replace(".", "")
        a.preco = float(inteiro)

    # Áreas (construída e terreno)
    for label, attr in [
        ("constru", "area_construida"),
        ("terreno", "area_terreno"),
    ]:
        mm = re.search(
            rf"{label}[^\d]{{0,20}}(\d+[\.,]?\d*)\s*m", texto, re.IGNORECASE
        )
        if mm:
            val = float(mm.group(1).replace(",", "."))
            setattr(a, attr, val)

    a.fotos_urls = extrair_fotos(html)
    return a


def extrair_fotos(html: str) -> list[str]:
    """Extrai URLs de fotos do anúncio."""
    soup = BeautifulSoup(html, "html.parser")
    fotos = []
    vistos = set()

    for img in soup.find_all("img"):
        src = img.get("src") or img.get("data-src") or ""
        if not src:
            continue
        if any(ext in src.lower() for ext in [".jpg", ".jpeg", ".png", ".webp"]):
            if "logo" in src.lower() or "sprite" in src.lower():
                continue
            if src not in vistos:
                vistos.add(src)
                fotos.append(src)

    return fotos


# ---------------------------------------------------------------------------
# Filtro pós-scraping (quintal com terra)
# ---------------------------------------------------------------------------
def calcular_match_quintal(a: Anuncio) -> None:
    texto = (a.descricao + " " + a.titulo).lower()
    score = 0
    for palavra in config.PALAVRAS_QUINTAL:
        if palavra in texto:
            score += 1
    for neg in config.PALAVRAS_NEGATIVAS:
        if neg in texto:
            score -= 2

    a.score_quintal = score
    a.match_quintal = score > 0


def atende_filtros(a: Anuncio) -> bool:
    """Aplica filtros: preço, área do terreno."""
    if a.preco and config.PRECO_MAX and a.preco > config.PRECO_MAX:
        return False
    if config.PRECO_MIN and a.preco and a.preco < config.PRECO_MIN:
        return False
    if (
        config.AREA_TERRENO_MIN
        and a.area_terreno
        and a.area_terreno < config.AREA_TERRENO_MIN
    ):
        return False
    return True


# ---------------------------------------------------------------------------
# Orquestração da coleta
# ---------------------------------------------------------------------------
def coletar_bairro(session: requests.Session, bairro: str) -> Iterator[str]:
    """Gera links de anúncios de um bairro, iterando pelas páginas."""
    for pagina in range(1, (config.MAX_PAGINAS or 1) + 1):
        url = montar_url(bairro, pagina)
        print(f"[bairro] {bairro} | página {pagina}: {url}")
        html = get_html(session, url)
        if not html:
            break

        links = extrair_links_anuncios(html)
        if not links:
            print("  [aviso] nenhum link de anúncio encontrado nesta página.")
            _salvar_debug(url, html, sufixo="sem_links")
            break

        for link in links:
            yield link

        _sleep()
