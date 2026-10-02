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
    return {"ic": SimpleNamespace(icone=icone)}


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


def _chips_ativos(filtros: dict) -> list[dict]:
    """Monta os "chips" dos filtros ativos.

    Cada chip leva a uma URL SEM aquele filtro (ou seja, clicar nele remove
    só aquele critério, mantendo os demais).
    """
    ORDEM_PADRAO = "score"

    def base(sem: str) -> str:
        """URL com todos os filtros, exceto `sem`."""
        q: dict[str, str] = {}

        if filtros.get("bairro") and sem != "bairro":
            q["bairro"] = filtros["bairro"]
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
        if filtros.get("piso_quintal") and sem != "piso_quintal":
            q["piso_quintal"] = filtros["piso_quintal"]
        if filtros.get("so_arvores") and sem != "so_arvores":
            q["so_arvores"] = "1"
        if filtros.get("so_cuidado") and sem != "so_cuidado":
            q["so_cuidado"] = "1"
        if filtros.get("com_problemas") and sem != "com_problemas":
            q["com_problemas"] = "1"
        if filtros.get("ordem") and filtros["ordem"] != ORDEM_PADRAO:
            q["ordem"] = filtros["ordem"]

        return url_for("index", **q) if q else url_for("index")

    chips: list[dict] = []

    if filtros.get("bairro"):
        chips.append({
            "rotulo": str(filtros["bairro"]).replace("-", " ").title(),
            "url": base("bairro"),
        })
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
    if filtros.get("piso_quintal"):
        chips.append({
            "rotulo": f"quintal: {PISO_ROTULO.get(filtros['piso_quintal'], filtros['piso_quintal'])}",
            "url": base("piso_quintal"),
        })
    if filtros.get("so_arvores"):
        chips.append({"rotulo": "com árvores (foto)", "url": base("so_arvores")})
    if filtros.get("so_cuidado"):
        chips.append({"rotulo": "bem cuidado (foto)", "url": base("so_cuidado")})
    if filtros.get("com_problemas"):
        chips.append({"rotulo": "com problemas (foto)", "url": base("com_problemas")})

    return chips


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(config.DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


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


def _fotos_locais(anuncio_url: str) -> list[str]:
    """Retorna caminhos web (relativos) das fotos já baixadas do anúncio."""
    conn = _conn()
    rows = conn.execute(
        "SELECT arquivo_local FROM fotos WHERE anuncio_url = ? ORDER BY id",
        (anuncio_url,),
    ).fetchall()
    conn.close()
    caminhos = []
    for r in rows:
        p = r["arquivo_local"].replace("\\", "/")
        # ignora vetores: são ícones do site (ex: right-arrow.svg) baixados por engano
        if p.lower().endswith(".svg"):
            continue
        caminhos.append(_rel_foto(p))
    return caminhos


def _slug_fotos(anuncio_url: str) -> str:
    from storage import _slug

    return _slug(anuncio_url)


# ---------------------------------------------------------------------------
# Rotas
# ---------------------------------------------------------------------------
@app.route("/")
def index():
    conn = _conn()

    bairro = request.args.get("bairro", "").strip()
    preco_max = request.args.get("preco_max", "").strip()
    terreno_min = request.args.get("terreno_min", "").strip()
    quartos_min = request.args.get("quartos_min", "").strip()
    so_quintal = request.args.get("so_quintal") == "1"
    so_financiamento = request.args.get("so_financiamento") == "1"
    sem_financiamento = request.args.get("sem_financiamento") == "1"
    # filtros que dependem da análise visual das fotos (IA)
    piso_quintal = request.args.get("piso_quintal", "").strip()
    so_arvores = request.args.get("so_arvores") == "1"
    so_cuidado = request.args.get("so_cuidado") == "1"
    com_problemas = request.args.get("com_problemas") == "1"
    ordem = request.args.get("ordem", "score")

    sql = "SELECT * FROM anuncios WHERE 1=1"
    params: list = []

    if bairro:
        # a coluna guarda o NOME real ("Vila Mangalot"); aceita também o slug
        # na URL ("vila-mangalot") convertendo antes de comparar
        sql += " AND LOWER(bairro) = LOWER(?)"
        params.append(_bairro(bairro))
    if preco_max:
        sql += " AND preco IS NOT NULL AND preco <= ?"
        params.append(float(preco_max))
    if terreno_min:
        sql += " AND area_terreno IS NOT NULL AND area_terreno >= ?"
        params.append(float(terreno_min))
    if quartos_min:
        sql += " AND quartos IS NOT NULL AND quartos >= ?"
        params.append(int(quartos_min))
    if so_quintal:
        sql += " AND match_quintal = 1"
    if so_financiamento:
        sql += " AND aceita_financiamento = 1"
    if sem_financiamento:
        sql += " AND (aceita_financiamento IS NULL OR aceita_financiamento = 0)"
    if piso_quintal:
        # "terra" é um caso especial: quase nenhum quintal é terra PURA
        # (o normal é terra + um canto cimentado, que o modelo classifica
        # como "misto"). Perguntar pelo piso predominante devolveria zero.
        # Aqui a pergunta é "tem terra?", que é o que o usuário quer saber.
        if piso_quintal == "terra":
            sql += " AND foto_quintal_terra = 1"
        else:
            sql += " AND foto_piso_quintal = ?"
            params.append(piso_quintal)
    if so_arvores:
        sql += " AND foto_arvores = 1"
    if so_cuidado:
        sql += " AND foto_cuidado >= 4"
    if com_problemas:
        sql += " AND COALESCE(foto_problemas, '') <> ''"

    ordem_sql = {
        "score": "score_quintal DESC, preco ASC",
        "preco_asc": "preco ASC",
        "preco_desc": "preco DESC",
        "terreno": "area_terreno DESC",
        "recentes": "rowid DESC",
    }.get(ordem, "score_quintal DESC, preco ASC")

    sql += f" ORDER BY {ordem_sql}"
    anuncios = conn.execute(sql, params).fetchall()

    bairros = [
        r["bairro"]
        for r in conn.execute(
            "SELECT bairro, COUNT(*) c FROM anuncios GROUP BY bairro ORDER BY bairro"
        ).fetchall()
    ]
    conn.close()

    cards = []
    for a in anuncios:
        d = dict(a)
        fotos = _fotos_locais(a["url"])
        d["fotos_card"] = fotos[:8]
        d["n_fotos"] = len(fotos)
        d["capa"] = fotos[0] if fotos else None
        d["slug_fotos"] = _slug_fotos(a["url"])
        cards.append(d)

    chips = _chips_ativos(
        {
            "bairro": bairro,
            "preco_max": preco_max,
            "terreno_min": terreno_min,
            "quartos_min": quartos_min,
            "so_quintal": so_quintal,
            "so_financiamento": so_financiamento,
            "sem_financiamento": sem_financiamento,
            "piso_quintal": piso_quintal,
            "so_arvores": so_arvores,
            "so_cuidado": so_cuidado,
            "com_problemas": com_problemas,
            "ordem": ordem,
        }
    )
    ativos = bool(chips)

    return render_template(
        "index.html",
        cards=cards,
        bairros=bairros,
        filtros={
            "bairro": bairro,
            "preco_max": preco_max,
            "terreno_min": terreno_min,
            "quartos_min": quartos_min,
            "so_quintal": so_quintal,
            "so_financiamento": so_financiamento,
            "sem_financiamento": sem_financiamento,
            "piso_quintal": piso_quintal,
            "so_arvores": so_arvores,
            "so_cuidado": so_cuidado,
            "com_problemas": com_problemas,
            "ordem": ordem,
        },
        chips=chips,
        filtros_ativos=ativos,
        total=len(cards),
    )


@app.route("/anuncio/<path:anuncio_url>")
def detalhe(anuncio_url: str):
    conn = _conn()
    a = conn.execute("SELECT * FROM anuncios WHERE url = ?", (anuncio_url,)).fetchone()
    conn.close()
    if a is None:
        abort(404)

    d = dict(a)
    d["fotos"] = _fotos_locais(anuncio_url)
    # URLs já prontas (o JS do carrossel usa direto, sem montar caminho)
    d["fotos_web"] = [url_for("foto", caminho=f) for f in d["fotos"]]
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

    caminho = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                           "_alvos_detalhe.json")
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
