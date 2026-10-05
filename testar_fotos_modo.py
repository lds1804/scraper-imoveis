"""Confere que os DOIS modos de foto funcionam: local e link.

O que precisa ser verdade:
  - modo "local" (padrao): o HTML continua identico ao de antes, servindo
    /fotos/<slug>/00.jpg. Nada muda para quem roda so na propria maquina.
  - modo "link": o HTML passa a apontar para o CDN do portal.
  - nos DOIS casos: o numero de fotos e os selos nao mudam (a analise visual
    depende do disco, nao do modo de exibicao).

Uso: python testar_fotos_modo.py
"""

from __future__ import annotations

import os
import re
import sys
import urllib.parse

try:
    sys.stdout.reconfigure(errors="replace")
except Exception:  # noqa: BLE001
    pass

_RAIZ = os.path.dirname(os.path.abspath(__file__))
# ORDEM IMPORTA: a raiz tem um `webapp.py` que e' um ATALHO de 2 linhas
# (`from _runner import executar; executar('webapp')`) e esse atalho INICIA O
# SERVIDOR ao ser importado. Se a raiz vier primeiro no sys.path, `import
# webapp` pega o atalho em vez do modulo de verdade -- e o teste viraria um
# servidor rodando para sempre. Por isso `src` entra por ULTIMO (fica em 1o).
sys.path.insert(0, _RAIZ)
sys.path.insert(0, os.path.join(_RAIZ, "src"))

# `webapp.py` so inicia o servidor quando executado direto; importado, nao.
# Fixar o diretorio na raiz para achar `imoveis.db` e `fotos/`.
os.chdir(_RAIZ)

import config  # noqa: E402
import webapp  # noqa: E402

assert webapp.__file__.replace("\\", "/").endswith("src/webapp.py"), (
    f"importou o atalho da raiz em vez do modulo: {webapp.__file__}")

OK = FALHA = 0
falhas: list[str] = []


def checar(nome: str, cond: bool, extra: str = "") -> None:
    global OK, FALHA
    if cond:
        OK += 1
        print(f"  PASSA  {nome}" + (f"   {extra}" if extra else ""))
    else:
        FALHA += 1
        falhas.append(nome)
        print(f"  FALHA  {nome}   {extra}")


def html_da_home() -> str:
    with webapp.app.test_client() as c:
        r = c.get("/")
        return r.get_data(as_text=True)


def html_do_detalhe(url: str) -> str:
    with webapp.app.test_client() as c:
        r = c.get("/anuncio/" + urllib.parse.quote(url, safe=""))
        return r.get_data(as_text=True)


def um_anuncio_com_foto(minimo: int = 1, exato: int | None = None) -> str:
    """Escolhe um anuncio pelo numero de fotos.

    Importa: o primeiro anuncio da tabela pode ter 1 foto so, e nesse caso o
    template NAO monta miniatura nem seta (correto). Usar esse anuncio para
    testar miniatura acusa falha onde nao existe.

    `exato` existe porque `ORDER BY COUNT DESC` com `HAVING >= 1` devolve o
    anuncio com MAIS fotos, nao um com uma so -- nao serve para testar o caso
    de foto unica.
    """
    import sqlite3
    conn = sqlite3.connect(config.DB_PATH)
    if exato is not None:
        sql = ("""SELECT a.url FROM anuncios a
                  JOIN fotos f ON f.anuncio_url = a.url
                  GROUP BY a.url HAVING COUNT(f.id) = ?
                  ORDER BY a.url LIMIT 1""")
        linha = conn.execute(sql, (exato,)).fetchone()
    else:
        sql = ("""SELECT a.url FROM anuncios a
                  JOIN fotos f ON f.anuncio_url = a.url
                  GROUP BY a.url HAVING COUNT(f.id) >= ?
                  ORDER BY COUNT(f.id) DESC LIMIT 1""")
        linha = conn.execute(sql, (minimo,)).fetchone()
    conn.close()
    return linha[0]


def srcs(html: str) -> list[str]:
    """Todas as URLs de `src=` das tags <img>. Procurar o nome do portal no
    HTML INTEIRO da' falso positivo: `/anuncio/https://www.zapimoveis.com.br/...`
    e' o LINK do card, nao uma imagem, e aparece centenas de vezes."""
    return re.findall(r'<img[^>]*\bsrc="([^"]+)"', html)


URL = um_anuncio_com_foto(30)
URL_1 = um_anuncio_com_foto(exato=1)
print(f"anuncio de teste (com varias fotos): {URL[:78]}")
print(f"anuncio com 1 foto (caso sem miniatura): {URL_1[:70]}")
print()

# ---------------------------------------------------------------- modo local
print("=" * 70)
print("MODO LOCAL (padrao)")
print("=" * 70)
config.FOTOS_MODO = "local"
webapp.config.FOTOS_MODO = "local"

home = html_da_home()
det = html_do_detalhe(URL)

checar("a home tem <img> com /fotos/",
       any(s.startswith("/fotos/") for s in srcs(home)))
checar("a home NAO usa URL de CDN no src das imagens",
       not any(s.startswith("http") for s in srcs(home)))
checar("a meta no-referrer esta presente",
       'name="referrer" content="no-referrer"' in home)
checar("o detalhe tem <img id=\"foto-atual\" com /fotos/",
       bool(re.search(r'id="foto-atual" src="/fotos/', det)))
n_fotos_local = len(re.findall(r'class="carrossel-mini', det))
checar("o detalhe monta as miniaturas", n_fotos_local > 0,
       f"{n_fotos_local} miniaturas")

# caso de 1 foto: o template NAO deve montar miniatura nem seta
det1 = html_do_detalhe(URL_1)
checar("anuncio de 1 foto nao monta miniatura (correto)",
       len(re.findall(r'class="carrossel-mini', det1)) == 0)

# ----------------------------------------------------------------- modo link
print()
print("=" * 70)
print("MODO LINK (nao hospeda nada)")
print("=" * 70)
config.FOTOS_MODO = "link"
webapp.config.FOTOS_MODO = "link"

home_l = html_da_home()
det_l = html_do_detalhe(URL)

cdn = [s for s in srcs(home_l) if s.startswith("http")]
checar("a home aponta para http(s) do CDN", len(cdn) > 0,
       f"{len(cdn)} imagens; ex: {cdn[0][:70] if cdn else '-'}")
checar("nenhuma img da home usa /fotos/ no modo link",
       not any(s.startswith("/fotos/") for s in srcs(home_l)))
checar("o detalhe aponta para o CDN",
       bool(re.search(r'id="foto-atual" src="https?://', det_l)))
n_fotos_link = len(re.findall(r'class="carrossel-mini', det_l))
checar("o numero de fotos NAO muda com o modo",
       n_fotos_link == n_fotos_local,
       f"local={n_fotos_local} link={n_fotos_link}")
checar("os selos/atributos da visao NAO mudam",
       ("quintal" in det_l) == ("quintal" in det),
       "analise visual vem do disco, nao do modo")

# as fotos continuam no disco nos dois casos
pasta = config.FOTOS_DIR
n_arq = sum(len(a) for _, _, a in os.walk(pasta))
checar("as fotos CONTINUAM no disco (o modo nao apaga nada)",
       n_arq > 50000, f"{n_arq:,} arquivos em fotos/")

# --------------------------------------------------------------- validacao
print()
print("=" * 70)
print("VALIDA QUE A URL DO CDN RESPONDE SEM REFERER")
print("=" * 70)
if cdn:
    import requests
    requests.packages.urllib3.disable_warnings()
    u = cdn[0]
    try:
        r = requests.get(u, timeout=30, verify=False, stream=True,
                         headers={"User-Agent": "Mozilla/5.0",
                                  "Accept": "image/*"})
        ct = r.headers.get("Content-Type", "")
        checar("o CDN devolve 200 sem Referer",
               r.status_code == 200 and ct.startswith("image"),
               f"{r.status_code} {ct}")
        r.close()
    except Exception as e:  # noqa: BLE001
        checar("o CDN devolve 200 sem Referer", False, type(e).__name__)

# volta ao padrao
config.FOTOS_MODO = "local"
webapp.config.FOTOS_MODO = "local"

print()
print("=" * 70)
print(f"{OK} passaram, {FALHA} falharam")
if falhas:
    for f in falhas:
        print(f"  - {f}")
    sys.exit(1)
print("TODAS AS VERIFICACOES PASSARAM")
