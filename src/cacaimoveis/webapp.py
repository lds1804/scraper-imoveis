"""Servidor web (Flask) para visualizar os anúncios coletados.

Uso:
    caca-web                      # ou: python -m cacaimoveis.webapp
    # depois abra http://127.0.0.1:5000
"""

from __future__ import annotations

import dataclasses
import os
import sqlite3
from types import SimpleNamespace

from flask import (
    Flask,
    abort,
    g,
    render_template,
    request,
    send_from_directory,
    url_for,
)

from cacaimoveis import config
from cacaimoveis import filtros as flt

app = Flask(
    __name__,
    # Os caminhos são absolutos (a partir da raiz do projeto) porque este
    # arquivo vive em `src/`: o padrão do Flask procuraria `src/web/templates`.
    template_folder=config.caminho("web", "templates"),
    static_folder=config.caminho("web", "static"),
)


@app.context_processor
def _injetar_icones():
    """Deixa `ic.icone(...)` disponível em todos os templates."""
    from flask import get_template_attribute

    # get_template_attribute devolve o próprio macro já resolvido
    icone = get_template_attribute("_icones.html", "icone")
    return {
        "ic": SimpleNamespace(icone=icone),
        # rótulos e opções do piso do quintal, para o seletor múltiplo os
        # montar sem repetir a lista no template (fonte única de verdade)
        "PISO_ROTULO": PISO_ROTULO,
        "PISO_OPCOES": PISO_OPCOES,
    }


@app.template_filter("moeda")
def _moeda(valor) -> str:
    if valor is None:
        return "Preço a consultar"
    try:
        return "R$ " + f"{float(valor):,.0f}".replace(",", ".")
    except (TypeError, ValueError):
        return "R$ ?"


@app.template_filter("m2")
def _m2(valor) -> str:
    if valor is None:
        return "?"
    return f"{float(valor):,.0f} m²".replace(",", ".")


@app.template_filter("numero")
def _numero(valor) -> str:
    """Formata número inteiro/decimal no padrão pt-BR (sem unidade)."""
    if valor is None:
        return "?"
    try:
        f = float(valor)
    except (TypeError, ValueError):
        return "?"
    if f == int(f):
        return f"{int(f):,}".replace(",", ".")
    return f"{f:,.1f}".replace(",", "X").replace(".", ",").replace("X", ".")


@app.template_filter("bairro")
def _bairro(slug) -> str:
    """'vila-mangalot' -> 'Vila Mangalot'; 'parque-sao-domingo' -> 'Parque São Domingo'."""
    if not slug:
        return ""
    texto = str(slug).replace("-", " ").title()
    # .title() não acentua: corrige os nomes de bairro conhecidos
    trocas = {
        "Sao ": "São ",
        "Sao": "São",
        "Jose": "José",
        "America": "América",
        "Pirituba": "Pirituba",
    }
    for de, para in trocas.items():
        texto = texto.replace(de, para)
    return texto


# ---------------------------------------------------------------------------
# Acesso ao banco
# ---------------------------------------------------------------------------
# Rótulos dos pisos de quintal (usados nos chips e na interface).
# "terra" fala em TER (não em predominância): quase nenhum quintal é terra
# pura e o que o usuário busca é justamente a presença de terra.
PISO_ROTULO = {
    "terra": "com terra batida",
    "grama": "grama",
    "cimento": "cimentado",
    "misto": "misto",
    "incerto": "não visível",
}

# Ordem em que as opções aparecem no seletor múltiplo. Só entram as que fazem
# sentido FILTRAR — "incerto" fica de fora porque "quintal não visível" não é
# um piso, é ausência de dado, e quem marca isso espera ver quintal.
PISO_OPCOES = [
    ("terra", "com terra batida"),
    ("grama", "gramado"),
    ("cimento", "cimentado"),
    ("misto", "misto"),
]


def _chips_ativos(filtros: dict) -> list[dict]:
    """Monta os "chips" dos filtros ativos.

    Cada chip leva a uma URL SEM aquele filtro (ou seja, clicar nele remove
    só aquele critério, mantendo os demais).

    Para os filtros de MÚLTIPLA escolha (`bairro` e `piso_quintal`), o chip
    remove apenas UM valor e os outros continuam aplicados — clicar em
    "Lapa" quando "Lapa + Pirituba" estavam marcados deve deixar só
    "Pirituba", não limpar os dois.
    """
    def base(sem: str, valor: str | None = None) -> str:
        """URL com todos os filtros, exceto `sem` (ou só o `valor` dele)."""
        q: dict = {}

        def multi(chave: str, valores: list[str]) -> None:
            restantes = [v for v in valores if v != valor] if (sem == chave and valor) \
                else ([] if sem == chave else list(valores))
            if restantes:
                q[chave] = restantes

        multi("bairro", filtros.get("bairro") or [])
        multi("piso_quintal", filtros.get("piso_quintal") or [])

        if filtros.get("preco_max") and sem != "preco_max":
            q["preco_max"] = filtros["preco_max"]
        if filtros.get("terreno_min") and sem != "terreno_min":
            q["terreno_min"] = filtros["terreno_min"]
        if filtros.get("quartos_min") and sem != "quartos_min":
            q["quartos_min"] = filtros["quartos_min"]
        if filtros.get("so_quintal") and sem != "so_quintal":
            q["so_quintal"] = "1"          # a rota espera "1", não "True"
        if filtros.get("so_financiamento") and sem != "so_financiamento":
            q["so_financiamento"] = "1"
        if filtros.get("sem_financiamento") and sem != "sem_financiamento":
            q["sem_financiamento"] = "1"
        if filtros.get("so_arvores") and sem != "so_arvores":
            q["so_arvores"] = "1"
        if filtros.get("so_abaixo") and sem != "so_abaixo":
            q["so_abaixo"] = "1"
        if filtros.get("ordem") and filtros["ordem"] != flt.ORDEM_PADRAO:
            q["ordem"] = filtros["ordem"]

        return url_for("index", **q) if q else url_for("index")

    chips: list[dict] = []

    # um chip por bairro escolhido: clicar remove só ele
    for b in filtros.get("bairro") or []:
        chips.append({"rotulo": str(b), "url": base("bairro", b)})

    if filtros.get("preco_max"):
        valor = float(filtros["preco_max"])
        chips.append({
            "rotulo": f"até R$ {valor:,.0f}".replace(",", "."),
            "url": base("preco_max"),
        })
    if filtros.get("terreno_min"):
        valor = float(filtros["terreno_min"])
        chips.append({
            "rotulo": f"terreno ≥ {valor:,.0f} m²".replace(",", "."),
            "url": base("terreno_min"),
        })
    if filtros.get("quartos_min"):
        chips.append({
            "rotulo": f"{filtros['quartos_min']}+ quartos",
            "url": base("quartos_min"),
        })
    if filtros.get("so_quintal"):
        chips.append({"rotulo": "só com quintal", "url": base("so_quintal")})
    if filtros.get("so_financiamento"):
        chips.append({"rotulo": "aceita financiamento", "url": base("so_financiamento")})
    if filtros.get("sem_financiamento"):
        chips.append({"rotulo": "sem financiamento", "url": base("sem_financiamento")})

    # um chip por piso escolhido
    for p in filtros.get("piso_quintal") or []:
        chips.append({
            "rotulo": f"quintal: {PISO_ROTULO.get(p, p)}",
            "url": base("piso_quintal", p),
        })

    if filtros.get("so_arvores"):
        chips.append({"rotulo": "com árvores (foto)", "url": base("so_arvores")})
    if filtros.get("so_abaixo"):
        chips.append({"rotulo": "abaixo do preço praticado", "url": base("so_abaixo")})

    return chips


def _conn() -> sqlite3.Connection:
    """A conexão da requisição atual (uma só, fechada no fim da requisição).

    Antes cada rota abria a sua e fechava "na mão" no fim — e `index()` abria
    DUAS, das quais a primeira nunca era fechada. Qualquer exceção no meio
    também deixava a conexão aberta. Guardar em `flask.g` e fechar no
    `teardown_appcontext` resolve os dois casos.
    """
    if "db" not in g:
        g.db = sqlite3.connect(config.DB_PATH)
        g.db.row_factory = sqlite3.Row
        flt.registrar_funcoes(g.db)
    return g.db


@app.teardown_appcontext
def _fechar_conn(_exc) -> None:
    conn = g.pop("db", None)
    if conn is not None:
        conn.close()


def _tem_comparacoes(conn: sqlite3.Connection) -> bool:
    """A tabela de comparações existe e tem dados?

    Checa de verdade em vez de confiar no try/except: assim uma falha de SQL
    (coluna renomeada) não vira silenciosamente "sem comparações", que foi o
    tipo de bug que já escondeu dado neste projeto.
    """
    try:
        existe = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='comparacoes'"
        ).fetchone()
        if not existe:
            return False
        return conn.execute(
            "SELECT 1 FROM comparacoes WHERE razao IS NOT NULL LIMIT 1"
        ).fetchone() is not None
    except sqlite3.Error:
        return False


def _comparacoes(conn: sqlite3.Connection, urls: list[str]) -> dict[str, dict]:
    """Busca a comparação ITBI de vários anúncios de uma vez."""
    if not urls:
        return {}
    marcadores = ",".join("?" * len(urls))
    try:
        linhas = conn.execute(
            f"SELECT * FROM comparacoes WHERE anuncio_url IN ({marcadores})",
            urls,
        ).fetchall()
    except sqlite3.Error:
        return {}
    return {r["anuncio_url"]: dict(r) for r in linhas}


def _tem_areas(conn: sqlite3.Connection) -> bool:
    """A tabela de áreas oficiais (GeoSampa) existe e tem dados?"""
    try:
        existe = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='areas_oficiais'"
        ).fetchone()
        if not existe:
            return False
        return conn.execute(
            "SELECT 1 FROM areas_oficiais WHERE dif_pct IS NOT NULL LIMIT 1"
        ).fetchone() is not None
    except sqlite3.Error:
        return False


def _areas_oficiais(conn: sqlite3.Connection, urls: list[str]) -> dict[str, dict]:
    """Área oficial do cadastro fiscal (GeoSampa) de vários anúncios."""
    if not urls:
        return {}
    marcadores = ",".join("?" * len(urls))
    try:
        linhas = conn.execute(
            f"SELECT * FROM areas_oficiais WHERE anuncio_url IN ({marcadores})",
            urls,
        ).fetchall()
    except sqlite3.Error:
        return {}
    return {r["anuncio_url"]: dict(r) for r in linhas}


def _tem_venais(conn: sqlite3.Connection) -> bool:
    """A tabela de valores venais existe e tem dados?"""
    try:
        if not conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='valores_venais'"
        ).fetchone():
            return False
        return conn.execute(
            "SELECT 1 FROM valores_venais LIMIT 1").fetchone() is not None
    except sqlite3.Error:
        return False


def _tem_duplicatas(conn: sqlite3.Connection) -> bool:
    """Existe pelo menos um anúncio marcado como cópia de outro?

    Sem isso a listagem não pode mostrar "uma linha por imóvel", e usar o
    filtro com a coluna ausente devolveria erro em vez de lista.
    """
    try:
        return conn.execute(
            "SELECT 1 FROM anuncios WHERE dup_grupo IS NOT NULL LIMIT 1"
        ).fetchone() is not None
    except sqlite3.Error:
        return False


def _copias_dos_grupos(conn: sqlite3.Connection, anuncios) -> dict[str, dict]:
    """Quantas cópias tem o grupo de cada anúncio, e a variação de preço.

    Serve para o aviso no card ("+24 anúncios do mesmo imóvel, de R$ 470.000
    a R$ 499.000"): o usuário vê que existem outras ofertas do mesmo imóvel
    sem precisar abrir nada.
    """
    grupos = {a["dup_grupo"] for a in anuncios if a["dup_grupo"] is not None}
    if not grupos:
        return {}
    marcadores = ",".join("?" * len(grupos))
    faixas: dict[object, tuple] = {}
    for r in conn.execute(
        f"""SELECT dup_grupo g, COUNT(*) n,
                   MIN(preco) menor, MAX(preco) maior
            FROM anuncios WHERE dup_grupo IN ({marcadores})
            GROUP BY dup_grupo""",
        list(grupos),
    ):
        faixas[r["g"]] = (r["n"], r["menor"], r["maior"])
    return faixas


def _venais(conn: sqlite3.Connection, urls: list[str]) -> dict[str, dict]:
    """Valor venal estimado (referência tributária) de vários anúncios."""
    if not urls:
        return {}
    marcadores = ",".join("?" * len(urls))
    try:
        linhas = conn.execute(
            f"SELECT * FROM valores_venais WHERE anuncio_url IN ({marcadores})",
            urls,
        ).fetchall()
    except sqlite3.Error:
        return {}
    return {r["anuncio_url"]: dict(r) for r in linhas}


def _grau_area(dif: float | None) -> str:
    """Mesma faixa usada em `referencia_geosampa._grau()`, para a interface.

    A zona morta de +-15% existe porque o anúncio arredonda e pode contar a
    área de forma diferente da prefeitura (útil x construída, varanda,
    edícula). Dentro dela não há o que concluir.
    """
    if dif is None:
        return "sem dado"
    a = abs(dif)
    if a <= 15:
        return "compativel"
    if a <= 30:
        return "atencao"
    return "divergente"


def _grau_da_area(area_of: dict | None) -> str:
    """Grau da conferência de área, respeitando QUAL área o anúncio informou.

    O `dif_pct` compara o número do anúncio com a CONSTRUÇÃO do cadastro. Mas
    medido nos 929 lotes exatos: em 12% dos casos o número do anúncio é o
    **terreno** (o portal quase não tem o campo de construção) e em 16%
    terreno e construção são iguais. Nesses casos comparar com a construção
    acusava divergência que não existe.

    `area_casa` (gravado por `referencia_geosampa`) diz o que o número do
    anúncio é; quando ele casa com o terreno, o grau é compatível — o anúncio
    não está mentindo, só publicou outro campo.
    """
    if not area_of:
        return "sem dado"
    if area_of.get("area_casa") in ("terreno", "ambos"):
        return "compativel"
    return _grau_area(area_of.get("dif_pct"))


def _rel_foto(caminho: str) -> str:
    """Converte o caminho guardado no banco em relativo à pasta de fotos.

    O banco tem DOIS formatos, porque a raiz do projeto mudou de lugar:
      - antigo, relativo : "fotos/<slug>/00.jpg"
      - novo, absoluto   : "C:/.../fotos/<slug>/00.jpg"

    Os dois precisam virar "<slug>/00.jpg", que é o que a rota `/fotos/<path>`
    espera (ela já serve a partir de `FOTOS_DIR`).
    """
    base = config.FOTOS_DIR.replace("\\", "/").rstrip("/")
    if caminho.startswith(base + "/"):
        return caminho[len(base) + 1:]
    try:
        rel = os.path.relpath(caminho, base).replace("\\", "/")
    except ValueError:
        return caminho  # unidades diferentes (C: vs D:) no Windows
    # se saiu para fora da pasta de fotos, não dá para tornar relativo
    return caminho if rel.startswith("..") else rel


def _fotos_locais_lote(conn: sqlite3.Connection,
                       urls: list[str]) -> dict[str, list[str]]:
    """Fotos de VÁRIOS anúncios numa consulta só.

    Antes isto era `_fotos_locais()` chamada uma vez por card, e cada chamada
    abria a própria conexão e varria a tabela `fotos` inteira (57 mil linhas,
    sem índice em `anuncio_url`). Medido: 44 ms por card × 4.793 cards =
    **213 s** para abrir a listagem — o app parecia travado.

    Duas coisas foram corrigidas: o índice `idx_fotos_anuncio` (plan passou de
    `SCAN` para `SEARCH`) e esta consulta em lote, que reaproveita a conexão
    já aberta em vez de abrir 4.793.
    """
    saida: dict[str, list[str]] = {}
    if not urls:
        return saida
    # lotes: o SQLite tem limite de variáveis por consulta (999 por padrão)
    for i in range(0, len(urls), 900):
        pedaco = urls[i:i + 900]
        marcadores = ",".join("?" * len(pedaco))
        for r in conn.execute(
            f"""SELECT anuncio_url, arquivo_local FROM fotos
                WHERE anuncio_url IN ({marcadores}) ORDER BY id""",
            pedaco,
        ):
            p = r["arquivo_local"].replace("\\", "/")
            # ignora vetores: são ícones do site (ex: right-arrow.svg)
            if p.lower().endswith(".svg"):
                continue
            saida.setdefault(r["anuncio_url"], []).append(_rel_foto(p))
    return saida


def _fotos_locais(anuncio_url: str) -> list[str]:
    """Retorna caminhos web (relativos) das fotos já baixadas do anúncio."""
    rows = _conn().execute(
        "SELECT arquivo_local FROM fotos WHERE anuncio_url = ? ORDER BY id",
        (anuncio_url,),
    ).fetchall()
    caminhos = []
    for r in rows:
        p = r["arquivo_local"].replace("\\", "/")
        # ignora vetores: são ícones do site (ex: right-arrow.svg) baixados por engano
        if p.lower().endswith(".svg"):
            continue
        caminhos.append(_rel_foto(p))
    return caminhos


def _slug_fotos(anuncio_url: str) -> str:
    from cacaimoveis.storage import _slug

    return _slug(anuncio_url)


# ---------------------------------------------------------------------------
# Rotas
# ---------------------------------------------------------------------------
def _cobertura_visual(conn: sqlite3.Connection, f: flt.Filtros,
                      tem_comp: bool, tem_dup: bool) -> dict | None:
    """Quantos imóveis o filtro pelas fotos consegue de fato avaliar.

    Os filtros "com árvores" e "piso do quintal" só enxergam anúncios cujas
    fotos passaram pela análise visual. Quem não foi analisado some do
    resultado sem aviso — e "12 casas com árvore" parece ser a resposta sobre
    a base inteira. Mostrar "X de Y analisados" torna o viés visível.
    Devolve None quando nenhum filtro visual está ativo.
    """
    if not (f.so_arvores or f.piso_quintal):
        return None
    sem_visuais = dataclasses.replace(f, so_arvores=False, piso_quintal=[])
    sql, params = flt.montar_consulta(conn, sem_visuais, tem_comp, tem_dup)
    total, analisados = conn.execute(
        f"SELECT COUNT(*), COUNT(foto_analisada_em) FROM ({sql})", params).fetchone()
    if total == analisados:
        return None
    return {"total": total, "analisados": analisados}


@app.route("/")
def index():
    f = flt.Filtros.da_url(request.args)

    conn = _conn()
    tem_comp = _tem_comparacoes(conn)
    tem_areas = _tem_areas(conn)
    tem_venais = _tem_venais(conn)
    tem_dup = _tem_duplicatas(conn)
    sql, params = flt.montar_consulta(conn, f, tem_comp, tem_dup)

    # -----------------------------------------------------------------------
    # PAGINAÇÃO. A listagem sem filtro chega a 4.793 anúncios, e renderizar
    # todos produzia uma página de 33,8 MB que levava ~11 s — e travava o
    # navegador. Com 60 por página a resposta fica em poucos KB.
    # -----------------------------------------------------------------------
    POR_PAGINA = flt.POR_PAGINA
    try:
        pagina = max(1, int(request.args.get("pagina", 1)))
    except (TypeError, ValueError):
        pagina = 1

    total_filtrado = conn.execute(
        f"SELECT COUNT(*) FROM ({sql})", params).fetchone()[0]
    n_paginas = max(1, (total_filtrado + POR_PAGINA - 1) // POR_PAGINA)
    pagina = min(pagina, n_paginas)

    anuncios = conn.execute(
        sql + " LIMIT ? OFFSET ?",
        [*params, POR_PAGINA, (pagina - 1) * POR_PAGINA]).fetchall()

    cobertura_visual = _cobertura_visual(conn, f, tem_comp, tem_dup)

    bairros = [
        r["bairro"]
        for r in conn.execute(
            "SELECT bairro, COUNT(*) c FROM anuncios GROUP BY bairro ORDER BY bairro"
        ).fetchall()
    ]
    urls = [a["url"] for a in anuncios]
    comps = _comparacoes(conn, urls) if tem_comp else {}
    areas = _areas_oficiais(conn, urls) if tem_areas else {}
    venais = _venais(conn, urls) if tem_venais else {}
    # quantas cópias do mesmo imóvel existem, e a faixa de preço delas
    faixas = _copias_dos_grupos(conn, anuncios) if tem_dup else {}
    # uma consulta só para as fotos de todos os cards da página
    fotos_lote = _fotos_locais_lote(conn, urls)

    cards = []
    for a in anuncios:
        d = dict(a)
        d["comp"] = comps.get(a["url"])
        d["area_of"] = areas.get(a["url"])
        d["area_grau"] = _grau_da_area(areas.get(a["url"]))
        d["venal"] = venais.get(a["url"])
        # nota de encaixe, para o selo no card (calculada no próprio SELECT)
        d["encaixe"] = a["_nota"]
        # "n_copias" é quantas ofertas do mesmo imóvel existem (1 = única).
        # O aviso no card só aparece quando há mais de uma.
        grupo = a["dup_grupo"]
        if grupo is not None and grupo in faixas:
            n_cop, menor, maior = faixas[grupo]
            d["n_copias"] = n_cop
            d["preco_min_copias"] = menor
            d["preco_max_copias"] = maior
        else:
            d["n_copias"] = 1
            d["preco_min_copias"] = None
            d["preco_max_copias"] = None
        fotos = fotos_lote.get(a["url"], [])
        d["fotos_card"] = fotos[:8]
        d["n_fotos"] = len(fotos)
        d["capa"] = fotos[0] if fotos else None
        d["slug_fotos"] = _slug_fotos(a["url"])
        cards.append(d)

    chips = _chips_ativos(f.como_dict())
    ativos = bool(chips)

    # A contagem exibida é o TOTAL filtrado, não o número de cards na página —
    # senão o usuário leria "60 imóveis" tendo 2.127 no resultado.
    return render_template(
        "index.html",
        cards=cards,
        bairros=bairros,
        pagina=pagina,
        n_paginas=n_paginas,
        total_filtrado=total_filtrado,
        por_pagina=POR_PAGINA,
        filtros=f.como_dict(),
        chips=chips,
        cobertura_visual=cobertura_visual,
        filtros_ativos=ativos,
        tem_comp=tem_comp,
        tem_dup=tem_dup,
        total=total_filtrado,
    )


def _copias_do_anuncio(conn: sqlite3.Connection,
                       anuncio_url: str,
                       dup_grupo) -> list[dict]:
    """As OUTRAS ofertas do mesmo imóvel (o grupo de duplicatas).

    Por que na página do anúncio e não expandindo a lista: a mesma casa é
    anunciada por várias imobiliárias (medido: um sobrado apareceu 25 vezes).
    Expandir no card transformaria uma linha em 25 cards iguais, que é o
    oposto de ajudar a comparar. Aqui fica tudo numa tabela curta — preço,
    portal e o número de fotos — na ordem do mais barato, que é a pergunta
    que o usuário realmente tem.

    Devolve sem o próprio anúncio (ele já está na tela).
    """
    if dup_grupo is None:
        return []
    linhas = conn.execute(
        """SELECT url, portal, preco, titulo, area_construida, quartos,
                  dup_melhor, dup_n_fotos
           FROM anuncios
           WHERE dup_grupo = ? AND url <> ?
           ORDER BY dup_melhor DESC, preco ASC""",
        (dup_grupo, anuncio_url),
    ).fetchall()
    return [dict(r) for r in linhas]


def _url_voltar() -> str:
    """Para onde o botão "Voltar" da página de anúncio deve levar.

    DEFEITO CORRIGIDO (2026-10-06): o botão era `url_for('index')` sem
    parâmetro, então VOLTAR SEMPRE caía na listagem limpa — quem tinha
    filtrado por bairro, quintal e preço perdia tudo ao olhar um anúncio e
    voltar. O usuário reclamou: "o voltar limpa os filtros de busca".

    A informação existe: o navegador manda `Referer` com a URL de origem.
    Usar o Referer (em vez de passar os filtros na URL do anúncio) tem duas
    vantagens:
      - preserva TAMBÉM a página (`?pagina=7`) e a ordem, sem precisar
        reescrever todos os links de card;
      - o link do anúncio continua limpo e compartilhável.

    Só aceitamos Referer DESTE site: um Referer externo mandaria o usuário
    para outra página qualquer, e um valor manipulado poderia virar redirect
    aberto (o clássico "open redirect").
    """
    ref = request.referrer or ""
    if ref:
        # mesma origem? compara o começo da URL com o host da requisição
        raiz = request.host_url.rstrip("/")
        if ref.startswith(raiz) and "/anuncio/" not in ref:
            return ref
    # sem Referer (acesso direto, aba nova, link compartilhado): lista limpa,
    # que é o melhor palpite possível
    return url_for("index")


@app.route("/anuncio/<path:anuncio_url>")
def detalhe(anuncio_url: str):
    conn = _conn()
    a = conn.execute("SELECT * FROM anuncios WHERE url = ?", (anuncio_url,)).fetchone()
    comp = None
    area_of = None
    transacoes: list = []
    if a is not None and _tem_comparacoes(conn):
        comp = _comparacoes(conn, [anuncio_url]).get(anuncio_url)
        if comp:
            transacoes = [
                dict(r) for r in conn.execute(
                    """SELECT * FROM comparacoes_detalhe WHERE anuncio_url = ?
                       ORDER BY preco_m2""",
                    (anuncio_url,),
                ).fetchall()
            ]
    if a is not None and _tem_areas(conn):
        area_of = _areas_oficiais(conn, [anuncio_url]).get(anuncio_url)
    venal = None
    if a is not None and _tem_venais(conn):
        venal = _venais(conn, [anuncio_url]).get(anuncio_url)
    copias: list[dict] = []
    if a is not None:
        copias = _copias_do_anuncio(conn, anuncio_url, a["dup_grupo"])
    if a is None:
        abort(404)

    d = dict(a)
    d["comp"] = comp
    d["transacoes"] = transacoes
    d["area_of"] = area_of
    d["area_grau"] = _grau_da_area(area_of)
    d["venal"] = venal
    d["copias"] = copias
    # o menor preço do grupo (com o próprio anúncio incluído) é a referência
    # para dizer em quanto ele está acima da oferta mais barata
    precos = [c["preco"] for c in copias if c["preco"]] + \
        ([d["preco"]] if d.get("preco") else [])
    d["grupo_menor_preco"] = min(precos) if precos else None
    d["grupo_maior_preco"] = max(precos) if precos else None
    d["grupo_n"] = len(copias) + 1
    d["fotos"] = _fotos_locais(anuncio_url)
    # URLs já prontas (o JS do carrossel usa direto, sem montar caminho)
    d["fotos_web"] = [url_for("foto", caminho=f) for f in d["fotos"]]
    d["slug_fotos"] = _slug_fotos(anuncio_url)
    return render_template("detalhe.html", a=d, url_voltar=_url_voltar())


@app.route("/fotos/<path:caminho>")
def foto(caminho: str):
    """Serve as fotos locais baixadas."""
    return send_from_directory(config.FOTOS_DIR, caminho, max_age=3600)


# ---------------------------------------------------------------------------
# Ponte do navegador (só na máquina local — ver `ponte.py`)
# ---------------------------------------------------------------------------
if not config.EM_PRODUCAO:
    from cacaimoveis import ponte

    app.register_blueprint(ponte.bp)


def main() -> None:
    """Servidor de desenvolvimento (`caca-web` ou `python -m cacaimoveis.webapp`)."""
    os.makedirs(config.FOTOS_DIR, exist_ok=True)
    # debug liga o console interativo do Werkzeug, que executa código: só
    # fora de produção, e sempre preso a 127.0.0.1
    app.run(host="127.0.0.1", port=5000, debug=not config.EM_PRODUCAO)


if __name__ == "__main__":
    main()
