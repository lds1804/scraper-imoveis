"""Testa as rotas do webapp fazendo requests HTTP simples.

Uso:
    python conferir_web.py
"""


from _bootstrap import iniciar

iniciar()  # poe src/ no sys.path e fixa a raiz como diretorio de trabalho

import html as htmlmod
import re
import urllib.error
import urllib.parse
import urllib.request

BASE = "http://127.0.0.1:5000"


def get(path: str) -> tuple[int, str]:
    # quote preserva caracteres como '?' e '&' da URL do anúncio
    url = BASE + urllib.parse.quote(path, safe="/?&=:,;%+@$!*'()~")
    try:
        with urllib.request.urlopen(url, timeout=15) as r:
            return r.status, r.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", errors="replace")


# 1) Home
s, html = get("/")
n_cards = html.count('class="card"')
print(f"GET /                    -> {s} | cards={n_cards}")

# 2) Filtro: só com quintal
s, html = get("/?so_quintal=1")
print(f"GET /?so_quintal=1       -> {s} | cards={html.count('class=\"card\"')}")

# 3) Filtro: terreno >= 200
s, html = get("/?terreno_min=200")
print(f"GET /?terreno_min=200    -> {s} | cards={html.count('class=\"card\"')}")

# 4) Filtro: bairro + preço
s, html = get("/?bairro=vila-mangalot&preco_max=700000&ordem=preco_asc")
print(f"GET /?bairro...&preco... -> {s} | cards={html.count('class=\"card\"')}")

# 5) Detalhe: pega o 1º link de anúncio da home
s, html = get("/")
m = re.search(r'href="(/anuncio/[^"]+)"', html)
if m:
    # desescapa entidades HTML (&amp; -> &) antes de requisitar
    link = htmlmod.unescape(m.group(1))
    s, dhtml = get(link)
    n_img = len(re.findall(r'/fotos/[^"]+', dhtml))
    tem_link_anuncio = "Ver no imovelweb" in dhtml
    n_minis = dhtml.count('class="carrossel-mini')
    print(
        f"GET /anuncio/...         -> {s} | imgs={n_img} | "
        f"miniaturas={n_minis} | link_original={tem_link_anuncio}"
    )

    # 6) Baixa a 1ª foto da galeria para checar o /fotos
    fotos = re.findall(r'src="(/fotos/[^"]+)"', dhtml)
    if fotos:
        s_f, _ = get(fotos[0])
        print(f"GET {fotos[0][:50]}... -> {s_f}")
    else:
        print("Nenhuma foto na galeria (anúncio sem fotos baixadas).")
else:
    print("Nenhum link de anúncio encontrado.")

# 7) 404 esperado para anúncio inexistente
s, _ = get("/anuncio/https://exemplo.com/nao-existe.html")
print(f"GET /anuncio/inexistente -> {s} (esperado 404)")

# 8) Filtro combinado com chip ativo
s, html = get("/?bairro=vila-mangalot&preco_max=700000&so_quintal=1")
n_chips = html.count('class="chip"')
print(f"GET /?filtros combinados -> {s} | chips={n_chips}")
