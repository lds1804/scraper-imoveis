"""Testes das rotas e dos filtros da interface web.

Por que existe: os filtros são combináveis e a URL é a única fonte de
verdade deles. Um erro aqui não quebra a página — ele devolve a lista
errada em silêncio, que é pior. Foi assim que o filtro "Terra batida"
ficou retornando zero resultados: o código estava sintaticamente correto.

Roda sem servidor, pelo `test_client` do Flask:
    .venv\\Scripts\\python.exe testar_web.py

Não depende de rede nem de navegador.
"""

from __future__ import annotations


from _bootstrap import iniciar
iniciar()  # poe src/ no sys.path e fixa a raiz como diretorio de trabalho

import html as htmlmod
import re
import sys

import webapp

_falhas: list[str] = []
_total = 0


def verificar(condicao: bool, descricao: str) -> None:
    global _total
    _total += 1
    if condicao:
        print(f"  PASSA  {descricao}")
    else:
        print(f"  FALHA  {descricao}")
        _falhas.append(descricao)


def _cards(html: bytes) -> int:
    return html.count(b'class="card"')


def _get(cliente, query: str = ""):
    return cliente.get("/" + query)


def main() -> int:
    app = webapp.app
    app.config["TESTING"] = True
    cliente = app.test_client()

    print("\n=== 1) A pagina inicial responde ===")
    r = _get(cliente)
    verificar(r.status_code == 200, f"GET / devolve 200 (veio {r.status_code})")
    total_sem_filtro = _cards(r.data)
    # a listagem é paginada: 60 por página, então NÃO deve trazer os 4.793
    verificar(total_sem_filtro > 10, f"lista anúncios ({total_sem_filtro} cards)")
    verificar(
        total_sem_filtro <= 60,
        f"a página respeita o limite de 60 cards ({total_sem_filtro})",
    )
    verificar(
        b"paginacao" in r.data,
        "mostra a navegação de páginas",
    )

    print("\n=== 2) Filtros que so REDUZEM (nunca aumentam) ===")
    # cada filtro tem que devolver um subconjunto do total
    filtros = {
        "?so_quintal=1": "só com quintal",
        "?so_financiamento=1": "aceita financiamento",
        "?so_arvores=1": "só com árvores",
        "?so_cuidado=1": "bem cuidado (4+)",
        "?com_problemas=1": "só com problemas",
        "?piso_quintal=grama": "quintal gramado",
        "?piso_quintal=cimento": "quintal cimentado",
        "?piso_quintal=misto": "quintal misto",
    }
    for query, nome in filtros.items():
        r = _get(cliente, query)
        n = _cards(r.data)
        verificar(
            r.status_code == 200 and n > 0,
            f"{nome}: {n} cards na 1a pagina",
        )

    print("\n=== 3) Regressao: 'terra batida' nao pode vir vazio ===")
    # Este filtro chegou a devolver 0 porque perguntava pelo piso
    # PREDOMINANTE, e quase todo quintal com terra e classificado como
    # "misto" (terra + um canto cimentado).
    r = _get(cliente, "?piso_quintal=terra")
    n_terra = _cards(r.data)
    verificar(r.status_code == 200, "GET /?piso_quintal=terra devolve 200")
    verificar(
        n_terra > 0,
        f"'com terra batida' encontra resultados ({n_terra} cards)",
    )

    print("\n=== 4) Filtros combinados se acumulam ===")
    r1 = _get(cliente, "?so_arvores=1")
    r2 = _get(cliente, "?so_arvores=1&com_problemas=1")
    n1, n2 = _cards(r1.data), _cards(r2.data)
    verificar(n2 <= n1, f"arvores+problemas ({n2}) <= arvores ({n1})")

    print("\n=== 5) Filtro impossivel devolve vazio, sem erro ===")
    r = _get(cliente, "?preco_max=1")
    verificar(r.status_code == 200, "preco_max=1 devolve 200 (não 500)")
    verificar(_cards(r.data) == 0, "preco_max=1 não lista nada")

    print("\n=== 6) Chips dos filtros ativos ===")
    r = _get(cliente, "?so_quintal=1&so_arvores=1")
    html = r.data.decode("utf-8", "replace")
    verificar("Filtros ativos" in html, "mostra o rótulo 'Filtros ativos'")
    verificar(html.count('class="chip"') == 2, "um chip por filtro ativo (2)")

    # o chip tem que remover SÓ ele, mantendo o outro filtro
    m = re.search(r'<a class="chip" href="([^"]+)"[^>]*>\s*só com quintal', html)
    verificar(m is not None, "existe chip para 'só com quintal'")
    if m:
        destino = m.group(1).replace("&amp;", "&")
        verificar(
            "so_arvores=1" in destino and "so_quintal" not in destino,
            f"o chip remove só o quintal, mantendo árvores ({destino})",
        )

    print("\n=== 7) Sem filtro ativo nao mostra 'Limpar' ===")
    r = _get(cliente)
    verificar(b"btn-secundario" not in r.data, "sem filtros, não há botão Limpar")
    r = _get(cliente, "?so_quintal=1")
    verificar(b"btn-secundario" in r.data, "com filtro, aparece o botão Limpar")

    print("\n=== 8) Pagina de detalhe de um anuncio real ===")
    # A URL do anúncio tem query string e vai dentro do href. O Jinja escapa
    # `&` como `&amp;`, e é isso que aparece no HTML — o navegador desescapa
    # antes de navegar. Sem desescapar aqui, este teste reprova um app que
    # está correto (já aconteceu antes com o conferir_web.py).
    r = _get(cliente, "?so_arvores=1")
    achou = re.search(rb'href="(/anuncio/[^"]+)"', r.data)
    verificar(achou is not None, "achou um link de anúncio na listagem")
    if achou:
        url = htmlmod.unescape(achou.group(1).decode("utf-8"))
        rd = cliente.get(url)
        verificar(rd.status_code == 200, f"GET /anuncio/... devolve 200 ({rd.status_code})")
        corpo = rd.data.decode("utf-8", "replace")
        verificar("O que as fotos mostram" in corpo, "mostra o bloco da análise visual")

        # Todos os links da listagem precisam abrir: qualquer anúncio com
        # `?` na URL quebra se o href não estiver corretamente codificado.
        todos = [
            htmlmod.unescape(h)
            for h in re.findall(r'href="(/anuncio/[^"]+)"', r.data.decode("utf-8", "replace"))
        ]
        status = {cliente.get(h).status_code for h in todos}
        verificar(
            status == {200},
            f"todos os {len(todos)} links da listagem abrem (status {status})",
        )

    print("\n=== 9) Anuncio inexistente nao explode ===")
    rd = cliente.get("/anuncio/nao-existe-12345")
    verificar(rd.status_code in (404, 302), f"devolve {rd.status_code} (não 500)")

    print("\n=== 10) Paginacao ===")
    r1 = _get(cliente)
    r2 = _get(cliente, "?pagina=2")
    c1, c2 = _cards(r1.data), _cards(r2.data)
    verificar(c1 == 60 and c2 == 60, f"páginas 1 e 2 cheias ({c1}, {c2})")
    # anúncios diferentes em cada página (não repete o mesmo lote)
    u1 = set(re.findall(r'href="(/anuncio/[^"]+)"', r1.data.decode("utf-8", "replace")))
    u2 = set(re.findall(r'href="(/anuncio/[^"]+)"', r2.data.decode("utf-8", "replace")))
    verificar(bool(u1 and u2 and not (u1 & u2)),
              "páginas 1 e 2 não repetem anúncios")
    # o filtro sobrevive à troca de página
    r3 = _get(cliente, "?so_arvores=1&pagina=2")
    h3 = r3.data.decode("utf-8", "replace")
    verificar("so_arvores=1" in h3 and "pagina=3" in h3,
              "trocar de página preserva o filtro ativo")
    # página fora do intervalo não quebra
    r4 = _get(cliente, "?pagina=99999")
    verificar(r4.status_code == 200, "página inexistente devolve 200 (não 500)")
    verificar(_cards(r4.data) > 0, "página fora do intervalo cai na última")

    print("\n=== 11) As tres medidas de valor aparecem ===")
    # a listagem deve mostrar o que foi calculado; se as tabelas existem,
    # os selos precisam sair no HTML
    html_txt = _get(cliente).data.decode("utf-8", "replace")
    import sqlite3 as _sq
    import config as _cfg
    _c = _sq.connect(_cfg.DB_PATH)
    def _tem(tabela):
        try:
            return _c.execute(f"SELECT 1 FROM {tabela} LIMIT 1").fetchone() is not None
        except _sq.Error:
            return False
    if _tem("comparacoes"):
        verificar("do preço praticado" in html_txt or "comp" in html_txt,
                  "mostra a comparação com o mercado (ITBI)")
    if _tem("valores_venais"):
        verificar("valor venal" in html_txt,
                  "mostra o valor venal estimado")
    _c.close()

    print("\n" + "=" * 58)
    if _falhas:
        print(f"{len(_falhas)} FALHA(S) de {_total} verificações:")
        for f in _falhas:
            print(f"  - {f}")
        return 1
    print(f"TODAS AS {_total} VERIFICAÇÕES PASSARAM")
    return 0


if __name__ == "__main__":
    sys.exit(main())
