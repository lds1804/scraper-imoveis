"""Descobre o slug correto do bairro Parque São Domingo."""


from _bootstrap import iniciar
iniciar()  # poe src/ no sys.path e fixa a raiz como diretorio de trabalho

from playwright.sync_api import sync_playwright

CANDIDATOS = [
    "https://www.imovelweb.com.br/casas-venda-parque-sao-domingo-sao-paulo-sp.html",
    "https://www.imovelweb.com.br/casas-venda-parque-s-domigo-sao-paulo-sp.html",
    "https://www.imovelweb.com.br/casas-venda-parque-sao-domingos-sao-paulo-sp.html",
    "https://www.imovelweb.com.br/casas-venda-jardim-sao-domingos-sao-paulo-sp.html",
    "https://www.imovelweb.com.br/casas-venda-parque-sao-domingos-jardim-sao-domingos-sao-paulo-sp.html",
    # busca geral por texto pode ajudar
    "https://www.imovelweb.com.br/casas-venda-sao-paulo-sp.html",
]

with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    ctx = browser.new_context(locale="pt-BR", viewport={"width": 1366, "height": 900})
    page = ctx.new_page()
    for url in CANDIDATOS:
        try:
            r = page.goto(url, wait_until="domcontentloaded", timeout=60000)
            status = r.status if r else "?"
            title = page.title()
            print(f"{status} | {url}\n     -> {title[:80]}")
        except Exception as e:  # noqa: BLE001
            print(f"ERR | {url}\n     -> {e}")
    browser.close()
