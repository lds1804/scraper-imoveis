"""Compara formatos de URL de busca do Imovelweb para um mesmo bairro.

Motivo: descobrimos anúncios de Curitiba, Rio, Campinas etc. dentro do lote
"parque-sao-domingo". A suspeita é que o slug usado
(`casas-venda-<bairro>-sao-paulo-sp.html`) não seja o formato canônico e o
site esteja caindo num fallback genérico.

Uso:
    python testar_url_bairro.py
"""

from __future__ import annotations

from _bootstrap import iniciar

iniciar()  # poe src/ no sys.path e fixa a raiz como diretorio de trabalho

import collections

from playwright.sync_api import sync_playwright

import config
from scraper_browser import _e_challenge, parse_cards

BASE = config.BASE_URL

# Formatos candidatos para o MESMO bairro + cidade
FORMATOS = [
    # (rótulo, url)
    ("A: tipo-bairro-cidade (atual)",
     f"{BASE}/casas-venda-parque-sao-domingo-sao-paulo-sp.html"),
    ("B: tipo-cidade-uf-bairro",
     f"{BASE}/casas-venda-sao-paulo-sp-parque-sao-domingo.html"),
    ("C: tipo-venda-cidade-bairro",
     f"{BASE}/casas-venda-sao-paulo-parque-sao-domingo.html"),
    ("D: tipo-bairro-sao-paulo-sp (vila mangalot, atual)",
     f"{BASE}/casas-venda-vila-mangalot-sao-paulo-sp.html"),
    ("E: tipo-cidade-uf-bairro (vila mangalot)",
     f"{BASE}/casas-venda-sao-paulo-sp-vila-mangalot.html"),
]


def _resumo(html: str) -> tuple[int, list[tuple[str, int]]]:
    cards = parse_cards(html, "?")
    cidades: collections.Counter[str] = collections.Counter()
    for c in cards:
        end = (c.endereco or "").strip()
        cidade = end.rsplit(",", 1)[-1].strip() if "," in end else "(sem)"
        cidades[cidade] += 1
    return len(cards), cidades.most_common(5)


def main() -> None:
    with sync_playwright() as p:
        ctx = p.chromium.launch_persistent_context(
            config.USER_DATA_DIR,
            headless=True,
            user_agent=config.USER_AGENT,
            locale="pt-BR",
            viewport={"width": 1366, "height": 900},
            args=["--disable-blink-features=AutomationControlled"],
        )
        page = ctx.pages[0] if ctx.pages else ctx.new_page()

        for rotulo, url in FORMATOS:
            print(f"\n{'=' * 78}\n{rotulo}\n{url}")
            try:
                resp = page.goto(url, wait_until="domcontentloaded", timeout=config.NAV_TIMEOUT_MS)
            except Exception as e:  # noqa: BLE001
                print(f"  [erro] {e}")
                continue

            status = resp.status if resp else "?"
            if _e_challenge(page):
                print(f"  status={status}  BLOQUEADO (cloudflare)")
                page.wait_for_timeout(config.CHALLENGE_TIMEOUT_MS)
                continue

            page.wait_for_timeout(3500)
            html = page.content()
            n, cidades = _resumo(html)
            print(f"  status={status} | url final: {page.url}")
            print(f"  cards={n}")
            for cidade, qtd in cidades:
                print(f"      {qtd:3d}  {cidade}")

        ctx.close()


if __name__ == "__main__":
    main()
