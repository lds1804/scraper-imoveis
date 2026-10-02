"""Teste rápido: o Playwright consegue acessar o Imovelweb?"""

from playwright.sync_api import sync_playwright

URL = "https://www.imovelweb.com.br/casas-venda-vila-mangalot-sao-paulo-sp.html"

with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    ctx = browser.new_context(
        user_agent=(
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/122.0.0.0 Safari/537.36"
        ),
        locale="pt-BR",
        viewport={"width": 1366, "height": 900},
    )
    page = ctx.new_page()
    resp = page.goto(URL, wait_until="domcontentloaded", timeout=60000)
    print("Status:", resp.status if resp else "sem resposta")
    page.wait_for_timeout(5000)

    html = page.content()
    print("Tamanho do HTML:", len(html))
    print("Tem 'R$':", "R$" in html)
    print("Tem 'imóvel' ou 'imovel':", ("imóvel" in html.lower() or "imovel" in html.lower()))

    with open("debug_playwright.html", "w", encoding="utf-8") as f:
        f.write(html)
    print("HTML salvo em debug_playwright.html")

    browser.close()
