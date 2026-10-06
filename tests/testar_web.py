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
import urllib.parse

from flask import url_for

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
    # (os antigos 'bem cuidado' e 'com problemas' sairam: o usuário pediu
    #  para tirar — a análise das fotos continua visível no card e no
    #  detalhe, só não filtra mais)
    filtros = {
        "?so_quintal=1": "com quintal",
        "?so_financiamento=1": "aceita financiamento",
        "?so_arvores=1": "árvore de porte",
        "?so_abaixo=1": "abaixo do preço praticado",
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

    print("\n=== 2b) Filtro por bairro (multipla escolha) ===")
    # O filtro de bairro deixou de ser um <select> e virou um painel de
    # caixas de marcação, para aceitar VÁRIOS bairros de uma vez. O teste
    # antigo procurava um <select name="bairro">, que não existe mais.
    #
    # O filtro precisa REALMENTE reduzir: comparamos a contagem exibida, não
    # o número de cards (todos mostram 60 na primeira página, o que não
    # distingue nada).
    r0 = _get(cliente)
    m0 = re.search(r"<b>([\d.]+)</b> im", r0.data.decode("utf-8", "replace"))
    total_geral = int(m0.group(1).replace(".", "")) if m0 else 0
    verificar(total_geral > 0, f"total sem filtro = {total_geral}")

    html0 = r0.data.decode("utf-8", "replace")
    # as opções são caixas de marcação com o nome do bairro no value
    opcoes = re.findall(r'<input type="checkbox" name="bairro" value="([^"]+)"',
                        html0)
    verificar(len(opcoes) >= 5, f"o painel lista {len(opcoes)} bairros")

    # o botão de resumo precisa dizer "Todos" quando nada está marcado
    verificar('class="multi-valor"' in html0, "o campo de bairro tem resumo no botão")

    def _n_anuncios(html):
        m = re.search(r"<b>([\d.]+)</b> im", html)
        return int(m.group(1).replace(".", "")) if m else -1

    def _achata(html):
        """Colapsa espaços/quebras de linha para comparar o HTML renderizado.

        O template quebra a linha entre o `value` e o `checked` da caixa de
        marcação, então procurar a string crua não acha nada.
        """
        return re.sub(r"\s+", " ", html)

    for b in opcoes[:3]:
        rb = _get(cliente, "?bairro=" + urllib.parse.quote(b))
        html_b = _achata(rb.data.decode("utf-8", "replace"))
        tot = _n_anuncios(html_b)
        verificar(
            0 < tot < total_geral,
            f"bairro {b!r}: {tot} anúncios (< {total_geral} do total)",
        )
        # o bairro escolhido precisa ficar marcado no painel
        verificar(
            f'name="bairro" value="{b}" checked' in html_b,
            f"bairro {b!r} fica marcado no painel",
        )

    # MÚLTIPLA escolha: a soma dos dois tem de ser exatamente a união
    if len(opcoes) >= 2:
        a, b2 = opcoes[0], opcoes[1]
        q = ("?bairro=" + urllib.parse.quote(a) +
             "&bairro=" + urllib.parse.quote(b2))
        rj = _get(cliente, q)
        juntos = _n_anuncios(rj.data.decode("utf-8", "replace"))
        so_a = _n_anuncios(_get(cliente, "?bairro=" + urllib.parse.quote(a))
                      .data.decode("utf-8", "replace"))
        so_b = _n_anuncios(_get(cliente, "?bairro=" + urllib.parse.quote(b2))
                      .data.decode("utf-8", "replace"))
        # dois bairros são conjuntos disjuntos, então a soma tem de fechar
        verificar(
            juntos == so_a + so_b,
            f"dois bairros somam certo: {so_a} + {so_b} = {juntos}",
        )
        verificar(
            rj.status_code == 200,
            "dois bairros de uma vez devolvem 200",
        )
        # e ambos os valores precisam ir no formulário, não só o último
        html_j = _achata(rj.data.decode("utf-8", "replace"))
        verificar(
            html_j.count('name="bairro" value="%s" checked' % a) == 1
            and html_j.count('name="bairro" value="%s" checked' % b2) == 1,
            "os dois bairros ficam marcados ao mesmo tempo",
        )

    print("\n=== 2c) Filtro de piso do quintal (multipla escolha) ===")
    # Mesma mudança: aceita vários pisos de uma vez. A união de dois pisos
    # distintos também precisa fechar exatamente.
    pisos = re.findall(r'<input type="checkbox" name="piso_quintal" value="([^"]+)"',
                       html0)
    verificar(len(pisos) >= 3, f"o painel lista {len(pisos)} tipos de piso")

    if len(pisos) >= 2:
        p1, p2 = pisos[0], pisos[1]
        n1 = _n_anuncios(_get(cliente, "?piso_quintal=" + p1)
                    .data.decode("utf-8", "replace"))
        n2 = _n_anuncios(_get(cliente, "?piso_quintal=" + p2)
                    .data.decode("utf-8", "replace"))
        nj = _n_anuncios(_get(cliente, f"?piso_quintal={p1}&piso_quintal={p2}")
                    .data.decode("utf-8", "replace"))
        # aqui PODE haver sobreposição: uma foto de quintal pode ser
        # classificada em mais de um piso, então a união é >= o maior e
        # <= a soma. Não exigimos igualdade, mas exigimos que a união não
        # seja MENOR que qualquer um dos dois (o que indicaria que só o
        # último valor foi aplicado).
        verificar(
            max(n1, n2) <= nj <= n1 + n2,
            f"dois pisos combinam certo: max({n1},{n2}) <= {nj} <= {n1 + n2}",
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
    r2 = _get(cliente, "?so_arvores=1&so_financiamento=1")

    def _conta(r):
        m = re.search(r"<b>([\d.]+)</b> im", r.data.decode("utf-8", "replace"))
        return int(m.group(1).replace(".", "")) if m else 0

    t1, t2 = _conta(r1), _conta(r2)
    verificar(t2 <= t1, f"arvores+financiamento ({t2}) <= arvores ({t1})")

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

    print("\n=== 8b) TODO anuncio com '?' na URL abre (a classe que quebra) ===")
    # A listagem mostra 60 anuncios por pagina, e nem todos os que tem '?'
    # caem nela: o teste acima passa mesmo que 131 anuncios estejam
    # quebrados. O risco e o '?' cru no href, que corta a URL no meio antes
    # de chegar no Flask. So passa se o href sair codificado (%3F), e a
    # prova tem que ser feita na base inteira, nao numa pagina.
    #
    # Medido nesta base: 131 anuncios tem '?' na URL (2,7%).
    import sqlite3 as _sq3
    import config as _cfg3

    _c3 = _sq3.connect(_cfg3.DB_PATH)
    _com_interrogacao = [
        l[0] for l in _c3.execute("SELECT url FROM anuncios WHERE url LIKE '%?%'")
    ]
    _com_cifrao = [
        l[0] for l in _c3.execute("SELECT url FROM anuncios WHERE url LIKE '%$%'")
    ]
    _c3.close()

    verificar(
        len(_com_interrogacao) > 50,
        f"a base tem anuncios com '?' na URL ({len(_com_interrogacao)})",
    )
    _ruins = []
    with app.test_request_context():
        # url_for e exatamente o que o template usa para montar o href
        for _u in _com_interrogacao:
            _href = htmlmod.unescape(url_for("detalhe", anuncio_url=_u))
            _st = cliente.get(_href).status_code
            if _st != 200:
                _ruins.append((_st, _u))
    _exemplo = f", ex.: {_ruins[0][1][:70]}" if _ruins else ""
    verificar(
        not _ruins,
        f"todos os {len(_com_interrogacao)} anuncios com '?' abrem "
        f"({len(_ruins)} falharam{_exemplo})",
    )

    # O '$' NAO precisa ser codificado: a RFC 3986 o aceita cru num caminho,
    # e o navegador repassa igual. Isto esta aqui para registrar que foi
    # testado e NAO e um defeito -- para ninguem "consertar" depois o que
    # nao esta quebrado, nem se assustar ao ver '$' nos hrefs.
    _cifrao_ruins = []
    with app.test_request_context():
        for _u in _com_cifrao:
            _href = htmlmod.unescape(url_for("detalhe", anuncio_url=_u))
            if cliente.get(_href).status_code != 200:
                _cifrao_ruins.append(_u)
    verificar(
        not _cifrao_ruins,
        f"os {len(_com_cifrao)} anuncios com '$' cru tambem abrem "
        f"(o '$' e legal em caminho, nao precisa de codigo)",
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

    print("\n=== 12) Rotulo do valor venal em linguagem clara ===")
    # O texto mostrava a chave interna da região ('cep5:02919'), que não diz
    # nada a quem lê. Agora traduz para a precisão: CEP / faixa de CEP / cidade.
    # Este teste pega um anúncio de cada grau e confere o rótulo e a coerência.
    verificar("cep5:" not in html_txt and "cep4:" not in html_txt,
              "não vaza a chave interna da região no HTML")
    if _tem("valores_venais"):
        linhas = _c.execute("""SELECT anuncio_url, regiao, origem_cep FROM valores_venais
                               WHERE regiao <> 'cidade' LIMIT 1""").fetchone()
        if linhas:
            # Duas armadilhas juntas aqui, as duas já custaram tempo:
            #  1. `_get()` faz "/" + query, então passar "/anuncio/..." viraria
            #     "//anuncio/..." — que é URL protocolo-relativa, com "anuncio"
            #     no lugar do host. Por isso o cliente é chamado DIRETO.
            #  2. A URL do anúncio tem `:` e `/`, e alguns têm `?` e `&`. Sem
            #     codificar, os separadores viram rota e dá 404.
            rd = cliente.get("/anuncio/" + urllib.parse.quote(linhas[0], safe=""))
            hd = rd.data.decode("utf-8", "replace")
            verificar(rd.status_code == 200,
                      f"detalhe do anúncio com região fina abre ({rd.status_code})")
            verificar("do CEP" in hd or "faixa de CEP" in hd or "região do CEP" in hd,
                      "rótulo da região fina é legível")
            verificar("transaç" in hd, "detalhe explica em que a razão se baseia")
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
