"""De onde o site tira as fotos: do disco local ou do CDN do portal.

POR QUE ISTO EXISTE
-------------------
As fotos são de imobiliárias — obra protegida por direito autoral
(Lei 9.610/98, art. 7º). Baixar para analisar é uma coisa; **servir num site
público é outra**, porque aí quem redistribui é você. Medido: são 5 GB de
57 mil arquivos.

Mas TESTEI e o CDN dos portais aceita ser linkado (medido em 2026-10-05):

    CDN                          sem Referer   Referer de fora
    imgbr.imovelwebcdn.com       200 OK        200 OK
    img.olx.com.br               200 OK        403 BLOQUEADO
    resizedimgs.zapimoveis...    200 OK        403 BLOQUEADO
    www.quintoandar.com.br       200 OK        200 OK

4 de 4 respondem **sem Referer**. Dois bloqueiam quando o pedido vem de outro
site. Ou seja: com `<meta name="referrer" content="no-referrer">` na página,
o navegador não manda o Referer e a imagem carrega — o site mostra a foto
**sem hospedar nada**. O S3 de 5 GB deixa de existir.

AS DUAS COISAS QUE NÃO SE CONFUNDEM
-----------------------------------
    1. pipeline de dados  -> SEMPRE baixa a foto para o disco local.
                             É daí que sai a análise visual (DeepSeek) e o
                             pHash das duplicatas. Nada disso muda.
    2. o que o SITE exibe -> pode linkar. Trocar isto NÃO apaga arquivo nenhum.

`FOTOS_MODO` controla só o item 2. O padrão é `"local"` — o comportamento de
hoje, que continua funcionando para quem roda só na própria máquina.

Uso:
    config.FOTOS_MODO = "link"   -> src aponta para o CDN do portal
    config.FOTOS_MODO = "local"  -> src aponta para /fotos/<caminho>
"""

from __future__ import annotations

import sqlite3

import config


def _rel(caminho: str) -> str:
    """Caminho guardado no banco -> relativo à pasta de fotos.

    O banco tem DOIS formatos porque a raiz do projeto mudou de lugar:
      - antigo, relativo : "fotos/<slug>/00.jpg"
      - novo, absoluto   : "C:/.../fotos/<slug>/00.jpg"
    """
    import os

    base = config.FOTOS_DIR.replace("\\", "/").rstrip("/")
    if caminho.startswith(base + "/"):
        return caminho[len(base) + 1:]
    try:
        rel = os.path.relpath(caminho, base).replace("\\", "/")
    except ValueError:
        return caminho  # unidades diferentes (C: vs D:) no Windows
    return caminho if rel.startswith("..") else rel


def _e_imagem(caminho: str) -> bool:
    """Descarta vetores: são ícones do site baixados por engano."""
    return not caminho.lower().endswith(".svg")


def fotos_do_anuncio(conn: sqlite3.Connection,
                     anuncio_url: str) -> list[dict]:
    """Fotos de um anúncio, já com a URL que o template deve usar em `src`.

    Devolve lista de dicts com:
      src     -> o que vai no <img src>
      url     -> a URL do CDN (mesmo em modo local; o JS usa para o lightbox)
      local   -> o caminho relativo (mesmo em modo link)
    """
    linhas = conn.execute(
        """SELECT foto_url, arquivo_local FROM fotos
           WHERE anuncio_url = ? ORDER BY id""",
        (anuncio_url,),
    ).fetchall()
    return _montar(linhas)


def fotos_de_varios(conn: sqlite3.Connection,
                    urls: list[str]) -> dict[str, list[dict]]:
    """Igual a `fotos_do_anuncio`, para vários anúncios numa consulta só.

    Antes isto era uma consulta por card e cada uma varria as 57 mil linhas
    (`SCAN`): 44 ms × 4.793 cards = **213 s** para abrir a listagem. Com o
    índice `idx_fotos_anuncio` e a consulta em lote, virou 1 consulta.
    """
    saida: dict[str, list[dict]] = {}
    if not urls:
        return saida
    # o SQLite limita variáveis por consulta (999 por padrão)
    for i in range(0, len(urls), 900):
        pedaco = urls[i:i + 900]
        marcadores = ",".join("?" * len(pedaco))
        linhas = conn.execute(
            f"""SELECT anuncio_url, foto_url, arquivo_local FROM fotos
                WHERE anuncio_url IN ({marcadores}) ORDER BY id""",
            pedaco,
        ).fetchall()
        por_ad: dict[str, list] = {}
        for r in linhas:
            por_ad.setdefault(r["anuncio_url"], []).append(r)
        for ad, grupo in por_ad.items():
            saida[ad] = _montar(grupo)
    return saida


def _montar(linhas) -> list[dict]:
    """Converte as linhas do banco na lista de fontes, conforme o modo."""
    modo = getattr(config, "FOTOS_MODO", "local")
    saida = []
    for r in linhas:
        local = (r["arquivo_local"] or "").replace("\\", "/")
        if not _e_imagem(local):
            continue
        remota = (r["foto_url"] or "").strip()
        tem_local = bool(local) and _existe(local)
        if modo == "link" and remota:
            src = remota
        elif tem_local:
            src = None  # o template monta com url_for('foto')
        elif remota:
            # modo local, mas o arquivo não está no disco -> melhor linkar que
            # mostrar imagem quebrada
            src = remota
        else:
            continue
        saida.append({
            "src": src,
            "url": remota,
            "local": _rel(local) if local else "",
        })
    return saida


def _existe(caminho: str) -> bool:
    import os

    if os.path.isabs(caminho):
        return os.path.exists(caminho)
    return os.path.exists(os.path.join(config.FOTOS_DIR, _rel(caminho)))
