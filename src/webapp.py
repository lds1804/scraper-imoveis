"""Servidor web (Flask) para visualizar os anúncios coletados.

Uso:
    python webapp.py
    # depois abra http://127.0.0.1:5000
"""

from __future__ import annotations

import os
import re
import sqlite3
from types import SimpleNamespace

from flask import (
    Flask,
    abort,
    render_template,
    request,
    send_from_directory,
    url_for,
)

import config
import fotos_fonte as _fonte

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


@app.template_filter("foto_src")
def _foto_src(f) -> str:
    """URL para o <img src> de uma foto, respeitando `config.FOTOS_MODO`.

    A foto chega como dict (ver `fotos_fonte.py`) com três campos:
      src   -> já é a URL do CDN do portal, quando o modo é "link"
      local -> caminho relativo na pasta `fotos/`, quando o modo é "local"

    O `src` é preferido quando existe; senão monta a rota local `/fotos/...`.
    Assim o template não precisa saber em que modo o site está.
    """
    if not f:
        return ""
    if isinstance(f, str):
        # compatibilidade: versões antigas guardavam só o caminho
        return url_for("foto", caminho=f)
    if f.get("src"):
        return f["src"]
    return url_for("foto", caminho=f.get("local", ""))


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
    ORDEM_PADRAO = "score"

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
        if filtros.get("ordem") and filtros["ordem"] != ORDEM_PADRAO:
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
    conn = sqlite3.connect(config.DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


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
    """Mesma faixa usada em `geosampa._grau_diferenca()`, para a interface.

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


def _rel_foto(caminho: str) -> str:
    """Converte o caminho guardado no banco em relativo à pasta de fotos.

    O banco tem DOIS formatos, porque a raiz do projeto mudou de lugar:
      - antigo, relativo : "fotos/<slug>/00.jpg"
      - novo, absoluto   : "C:/.../fotos/<slug>/00.jpg"

    Os dois precisam virar "<slug>/00.jpg", que é o que a rota `/fotos/<path>`
    espera (ela já serve a partir de `FOTOS_DIR`).
    """
    return _fonte._rel(caminho)


def _fotos_locais_lote(conn: sqlite3.Connection,
                       urls: list[str]) -> dict[str, list[dict]]:
    """Fotos de VÁRIOS anúncios numa consulta só, pelo modo configurado.

    Antes isto era uma consulta por card, e cada uma varria a tabela `fotos`
    inteira (57 mil linhas, sem índice em `anuncio_url`). Medido: 44 ms por
    card × 4.793 cards = **213 s** para abrir a listagem — o app parecia
    travado.

    Duas coisas foram corrigidas: o índice `idx_fotos_anuncio` (plan passou de
    `SCAN` para `SEARCH`) e esta consulta em lote, que reaproveita a conexão
    já aberta em vez de abrir 4.793.

    Devolve dicts (não strings) porque cada foto precisa saber o `src` do modo
    atual e a URL remota ao mesmo tempo — ver `fotos_fonte.py`.
    """
    return _fonte.fotos_de_varios(conn, urls)


def _fotos_locais(anuncio_url: str) -> list[dict]:
    """Fotos já baixadas do anúncio, pelo modo configurado."""
    conn = _conn()
    itens = _fonte.fotos_do_anuncio(conn, anuncio_url)
    conn.close()
    return itens


def _slug_fotos(anuncio_url: str) -> str:
    from storage import _slug

    return _slug(anuncio_url)


# ---------------------------------------------------------------------------
# Rotas
# ---------------------------------------------------------------------------
@app.route("/")
def index():
    conn = _conn()

    # MÚLTIPLA escolha: o formulário manda o mesmo nome várias vezes
    # (?bairro=Lapa&bairro=Pirituba) e `getlist` devolve todos. O código
    # antigo usava `get` e ficava só com o PRIMEIRO — o usuário marcaria
    # três bairros e a busca silenciosamente ignoraria dois.
    bairros_sel = [b.strip() for b in request.args.getlist("bairro") if b.strip()]
    pisos_sel = [p.strip() for p in request.args.getlist("piso_quintal") if p.strip()]
    preco_max = request.args.get("preco_max", "").strip()
    terreno_min = request.args.get("terreno_min", "").strip()
    quartos_min = request.args.get("quartos_min", "").strip()
    so_quintal = request.args.get("so_quintal") == "1"
    so_financiamento = request.args.get("so_financiamento") == "1"
    sem_financiamento = request.args.get("sem_financiamento") == "1"
    # filtros que dependem da análise visual das fotos (IA)
    so_arvores = request.args.get("so_arvores") == "1"
    # comparação com o preço praticado (ITBI)
    so_abaixo = request.args.get("so_abaixo") == "1"
    # Mostrar só UMA linha por imóvel. A mesma casa é anunciada por várias
    # imobiliárias (medido: 2.857 anúncios em 1.000 grupos; um sobrado
    # apareceu 25 vezes). Sem isso a lista repete o mesmo imóvel e o usuário
    # perde tempo relendo. Marcado por padrão; `?todas=1` desliga.
    todas = request.args.get("todas") == "1"
    ordem = request.args.get("ordem", "abaixo")

    # "abaixo da mediana do ITBI": razão = preço pedido / mediana das
    # transações comparáveis, então < 1 = abaixo do preço praticado.
    conn = _conn()
    tem_comp = _tem_comparacoes(conn)
    tem_areas = _tem_areas(conn)
    tem_venais = _tem_venais(conn)
    tem_dup = _tem_duplicatas(conn)

    # Todas as colunas do WHERE ganham o prefixo `a.` e o LEFT JOIN entra
    # sempre que a tabela de comparações existe. O LEFT JOIN é necessário
    # (não INNER) para que quem NÃO tem comparação continue aparecendo na
    # listagem — só vai para o fim da ordem.
    if tem_comp:
        sql = ("SELECT a.* FROM anuncios a "
               "LEFT JOIN comparacoes c ON c.anuncio_url = a.url "
               "WHERE 1=1")
    else:
        sql = "SELECT a.* FROM anuncios a WHERE 1=1"
    params: list = []

    if bairros_sel:
        # a coluna guarda o NOME real ("Vila Mangalot"); aceita também o slug
        # na URL ("vila-mangalot") convertendo antes de comparar.
        # Uso `IN` porque agora são vários bairros de uma vez.
        nomes = [_bairro(b).lower() for b in bairros_sel]
        sql += f" AND LOWER(a.bairro) IN ({','.join('?' * len(nomes))})"
        params.extend(nomes)
    if preco_max:
        sql += " AND a.preco IS NOT NULL AND a.preco <= ?"
        params.append(float(preco_max))
    if terreno_min:
        sql += " AND a.area_terreno IS NOT NULL AND a.area_terreno >= ?"
        params.append(float(terreno_min))
    if quartos_min:
        sql += " AND a.quartos IS NOT NULL AND a.quartos >= ?"
        params.append(int(quartos_min))
    if so_quintal:
        sql += " AND a.match_quintal = 1"
    if so_financiamento:
        sql += " AND a.aceita_financiamento = 1"
    if sem_financiamento:
        sql += " AND (a.aceita_financiamento IS NULL OR a.aceita_financiamento = 0)"
    if pisos_sel:
        # OR entre os pisos escolhidos: marcar "terra" e "grama" significa
        # "tem terra OU é gramado", que é o que a pessoa espera ao marcar os
        # dois. Um AND entre eles devolveria sempre zero.
        #
        # "terra" é um caso especial: quase nenhum quintal é terra PURA (o
        # normal é terra + um canto cimentado, que o modelo classifica como
        # "misto"). Perguntar pelo piso predominante devolveria zero — a
        # pergunta aqui é "tem terra?".
        partes = []
        for p in pisos_sel:
            if p == "terra":
                partes.append("a.foto_quintal_terra = 1")
            else:
                partes.append("a.foto_piso_quintal = ?")
                params.append(p)
        sql += " AND (" + " OR ".join(partes) + ")"
    if so_arvores:
        sql += " AND a.foto_arvores = 1"

    # UMA linha por imóvel: só o anúncio principal do grupo de duplicatas
    # (o de menor preço, escolhido em `achar_duplicatas.py --marcar`).
    # Quem não está em grupo nenhum (`dup_grupo IS NULL`) continua aparecendo:
    # não ter cópia não é motivo para sumir da lista.
    if tem_dup and not todas:
        sql += " AND (a.dup_grupo IS NULL OR a.dup_melhor = 1)"

    # "abaixo da mediana do ITBI" — o JOIN precisa existir para o filtro valer
    if so_abaixo and tem_comp:
        sql += " AND c.razao IS NOT NULL AND c.razao < 1"

    # ORDEM PADRÃO: os mais abaixo do mercado primeiro (pedido do usuário).
    # `c.razao IS NULL` vai para o fim: sem comparação não é "bom negócio",
    # é ausência de informação — não faz sentido liderar a lista.
    if tem_comp:
        ordem_sql = {
            "abaixo": "c.razao IS NULL, c.razao ASC, a.score_quintal DESC, a.preco ASC",
            "score": "a.score_quintal DESC, a.preco ASC",
            "preco_asc": "a.preco ASC",
            "preco_desc": "a.preco DESC",
            "terreno": "a.area_terreno DESC",
            "recentes": "a.rowid DESC",
        }.get(ordem, "c.razao IS NULL, c.razao ASC, a.score_quintal DESC, a.preco ASC")
    else:
        ordem_sql = {
            "preco_asc": "a.preco ASC",
            "preco_desc": "a.preco DESC",
            "terreno": "a.area_terreno DESC",
            "recentes": "a.rowid DESC",
        }.get(ordem, "a.score_quintal DESC, a.preco ASC")

    # Desempate final: quem é principal do seu grupo vem antes. Dois anúncios
    # com a MESMA razão (ou o mesmo preço) empatariam, e sem isto a posição
    # ficaria arbitrária. Vale nos dois ramos acima, por isso entra depois.
    sql += f" ORDER BY {ordem_sql}, a.dup_melhor DESC"

    # -----------------------------------------------------------------------
    # PAGINAÇÃO. A listagem sem filtro chega a 4.793 anúncios, e renderizar
    # todos produzia uma página de 33,8 MB que levava ~11 s — e travava o
    # navegador. Com 60 por página a resposta fica em poucos KB.
    # -----------------------------------------------------------------------
    POR_PAGINA = 60
    try:
        pagina = max(1, int(request.args.get("pagina", 1)))
    except (TypeError, ValueError):
        pagina = 1

    total_filtrado = conn.execute(
        f"SELECT COUNT(*) FROM ({sql})", params).fetchone()[0]
    n_paginas = max(1, (total_filtrado + POR_PAGINA - 1) // POR_PAGINA)
    pagina = min(pagina, n_paginas)

    sql_pag = sql + " LIMIT ? OFFSET ?"
    anuncios = conn.execute(
        sql_pag, [*params, POR_PAGINA, (pagina - 1) * POR_PAGINA]).fetchall()

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
    conn.close()

    cards = []
    for a in anuncios:
        d = dict(a)
        d["comp"] = comps.get(a["url"])
        d["area_of"] = areas.get(a["url"])
        d["area_grau"] = _grau_area(
            (areas.get(a["url"]) or {}).get("dif_pct"))
        d["venal"] = venais.get(a["url"])
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
    chips = _chips_ativos(
        {
            "bairro": bairros_sel,
            "preco_max": preco_max,
            "terreno_min": terreno_min,
            "quartos_min": quartos_min,
            "so_quintal": so_quintal,
            "so_financiamento": so_financiamento,
            "sem_financiamento": sem_financiamento,
            "piso_quintal": pisos_sel,
            "so_arvores": so_arvores,
            "so_abaixo": so_abaixo,
            "ordem": ordem,
        }
    )
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
        filtros={
            "bairro": bairros_sel,
            "preco_max": preco_max,
            "terreno_min": terreno_min,
            "quartos_min": quartos_min,
            "so_quintal": so_quintal,
            "so_financiamento": so_financiamento,
            "sem_financiamento": sem_financiamento,
            "piso_quintal": pisos_sel,
            "so_arvores": so_arvores,
            "so_abaixo": so_abaixo,
            "todas": todas,
            "ordem": ordem,
        },
        chips=chips,
        filtros_ativos=ativos,
        tem_comp=tem_comp,
        tem_dup=tem_dup,
        total=total_filtrado,
    )


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
    conn.close()
    if a is None:
        abort(404)

    d = dict(a)
    d["comp"] = comp
    d["transacoes"] = transacoes
    d["area_of"] = area_of
    d["area_grau"] = _grau_area((area_of or {}).get("dif_pct"))
    d["venal"] = venal
    d["fotos"] = _fotos_locais(anuncio_url)
    # `fotos_web` só é usado quando NÃO há link remoto (modo local). No modo
    # link o template usa `src` direto, que já é a URL do CDN do portal.
    d["fotos_web"] = [
        url_for("foto", caminho=f["local"]) if f["src"] is None else f["src"]
        for f in d["fotos"]
    ]
    d["fotos_modo"] = getattr(config, "FOTOS_MODO", "local")
    d["slug_fotos"] = _slug_fotos(anuncio_url)
    return render_template("detalhe.html", a=d)


@app.route("/fotos/<path:caminho>")
def foto(caminho: str):
    """Serve as fotos locais baixadas."""
    return send_from_directory(config.FOTOS_DIR, caminho, max_age=3600)


# ---------------------------------------------------------------------------
# Ponte para quando o Playwright está bloqueado pelo Cloudflare
# ---------------------------------------------------------------------------
# O navegador integrado do VS Code costuma ter o clearance do Cloudflare válido
# quando o perfil do Playwright já foi bloqueado. Esta rota recebe o HTML da
# listagem e grava em disco, para o parser Python processar depois.
# Ferramenta de uso pessoal e local (só aceita 127.0.0.1).
PONTE_DIR = "html_ponte"


@app.route("/_ponte/html", methods=["POST", "OPTIONS"])
def _ponte_receber_html():
    if request.method == "OPTIONS":
        resp = app.make_default_options_response()
    else:
        nome = request.args.get("nome", "pagina")
        nome = re.sub(r"[^A-Za-z0-9_.-]", "_", nome)[:120]
        os.makedirs(PONTE_DIR, exist_ok=True)
        with open(os.path.join(PONTE_DIR, f"{nome}.html"), "wb") as f:
            f.write(request.get_data())
        resp = app.response_class('{"ok":true}', mimetype="application/json")

    # o navegador posta de outro domínio (imovelweb.com.br) -> libera CORS
    resp.headers["Access-Control-Allow-Origin"] = "*"
    resp.headers["Access-Control-Allow-Methods"] = "POST, OPTIONS"
    resp.headers["Access-Control-Allow-Headers"] = "Content-Type"
    return resp


@app.route("/_ponte/limpar", methods=["POST"])
def _ponte_limpar():
    """Apaga os HTMLs temporários depois de processar."""
    if os.path.isdir(PONTE_DIR):
        import shutil

        shutil.rmtree(PONTE_DIR, ignore_errors=True)
    return {"ok": True}


@app.route("/_ponte/alvos")
def _ponte_alvos():
    """Devolve a lista de anúncios que precisam de página de detalhe.

    O navegador usa isso para saber o que capturar (ele não lê o SQLite).
    """
    import json as _json

    # fica em `dados/`, não ao lado deste arquivo (que está em `src/`)
    caminho = config.caminho("dados", "_alvos_detalhe.json")
    if not os.path.exists(caminho):
        return {"total": 0, "alvos": []}
    with open(caminho, encoding="utf-8") as f:
        alvos = _json.load(f)

    resp = app.response_class(
        _json.dumps({"total": len(alvos), "alvos": alvos}, ensure_ascii=False),
        mimetype="application/json",
    )
    resp.headers["Access-Control-Allow-Origin"] = "*"
    return resp


@app.route("/_ponte/progresso")
def _ponte_progresso():
    """Quantos HTMLs de detalhe já foram capturados."""
    n = 0
    if os.path.isdir(PONTE_DIR):
        n = sum(
            1 for f in os.listdir(PONTE_DIR)
            if f.lower().startswith("det") and f.lower().endswith(".html")
            and os.path.getsize(os.path.join(PONTE_DIR, f)) > 100_000
        )
    resp = app.response_class(
        f'{{"capturados": {n}}}', mimetype="application/json"
    )
    resp.headers["Access-Control-Allow-Origin"] = "*"
    return resp


if __name__ == "__main__":
    os.makedirs(config.FOTOS_DIR, exist_ok=True)
    app.run(host="127.0.0.1", port=5000, debug=True)
