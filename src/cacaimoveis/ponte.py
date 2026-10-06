"""Ponte para quando o Playwright está bloqueado pelo Cloudflare.

O navegador integrado do VS Code costuma ter o clearance do Cloudflare válido
quando o perfil do Playwright já foi bloqueado. Estas rotas recebem o HTML que
o navegador captura e gravam em disco, para o parser Python processar depois
(`processar_ponte.py`).

SÓ PARA USO LOCAL. As rotas gravam e apagam arquivos a pedido do navegador, e
é por isso que:

  - `webapp` só registra este blueprint fora de produção
    (`config.EM_PRODUCAO`);
  - toda requisição com cabeçalho `Origin` precisa vir do Imovelweb ou da
    própria máquina. Sem essa checagem, QUALQUER site aberto no navegador
    poderia mandar um POST para `127.0.0.1:5000/_ponte/limpar` — um POST de
    formulário simples nem passa pelo preflight de CORS, então o
    `Access-Control-Allow-Origin` não protegia nada.
"""

from __future__ import annotations

import json
import os
import re
import shutil
from urllib.parse import urlparse

from flask import Blueprint, abort, current_app, request

from cacaimoveis import config

bp = Blueprint("ponte", __name__, url_prefix="/_ponte")

PONTE_DIR = config.PONTE_DIR

# De onde o navegador pode postar: as páginas do Imovelweb (onde o script de
# captura roda) e a própria máquina.
_HOSTS_PERMITIDOS = ("imovelweb.com.br", "127.0.0.1", "localhost")


def _origem_permitida(origem: str) -> bool:
    host = (urlparse(origem).hostname or "").lower()
    return any(host == h or host.endswith("." + h) for h in _HOSTS_PERMITIDOS)


@bp.before_request
def _checar_origem():
    origem = request.headers.get("Origin")
    # sem Origin = ferramenta de linha de comando (curl, requests): é local
    if origem and not _origem_permitida(origem):
        abort(403)


@bp.after_request
def _cors(resp):
    """Libera CORS só para a origem que já passou na checagem."""
    origem = request.headers.get("Origin")
    if origem and _origem_permitida(origem):
        resp.headers["Access-Control-Allow-Origin"] = origem
        resp.headers["Vary"] = "Origin"
        resp.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
        resp.headers["Access-Control-Allow-Headers"] = "Content-Type"
    return resp


@bp.route("/html", methods=["POST", "OPTIONS"])
def receber_html():
    if request.method == "OPTIONS":
        return current_app.make_default_options_response()
    nome = request.args.get("nome", "pagina")
    nome = re.sub(r"[^A-Za-z0-9_.-]", "_", nome)[:120]
    os.makedirs(PONTE_DIR, exist_ok=True)
    with open(os.path.join(PONTE_DIR, f"{nome}.html"), "wb") as f:
        f.write(request.get_data())
    return {"ok": True}


@bp.route("/limpar", methods=["POST"])
def limpar():
    """Apaga os HTMLs temporários depois de processar."""
    if os.path.isdir(PONTE_DIR):
        shutil.rmtree(PONTE_DIR, ignore_errors=True)
    return {"ok": True}


@bp.route("/alvos")
def alvos():
    """Devolve a lista de anúncios que precisam de página de detalhe.

    O navegador usa isso para saber o que capturar (ele não lê o SQLite).
    """
    # fica em `dados/`, não ao lado deste arquivo (que está em `src/`)
    caminho = config.caminho("dados", "_alvos_detalhe.json")
    if not os.path.exists(caminho):
        return {"total": 0, "alvos": []}
    with open(caminho, encoding="utf-8") as f:
        lista = json.load(f)
    return current_app.response_class(
        json.dumps({"total": len(lista), "alvos": lista}, ensure_ascii=False),
        mimetype="application/json",
    )


@bp.route("/progresso")
def progresso():
    """Quantos HTMLs de detalhe já foram capturados."""
    n = 0
    if os.path.isdir(PONTE_DIR):
        n = sum(
            1 for f in os.listdir(PONTE_DIR)
            if f.lower().startswith("det") and f.lower().endswith(".html")
            and os.path.getsize(os.path.join(PONTE_DIR, f)) > 100_000
        )
    return {"capturados": n}
