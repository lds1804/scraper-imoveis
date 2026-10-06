"""Backfill: corrige dados já salvos no banco (titulo e áreas).

- titulo: se estiver vazio ou for apenas o preço ("R$ ..."), gera a partir do slug.
- area_terreno: preenche a partir da descrição quando estava vazio.
- area_construida: usa a descrição ("área construída") quando disponível.
"""

from __future__ import annotations

from _bootstrap import iniciar

iniciar()  # poe src/ no sys.path e fixa a raiz como diretorio de trabalho

import re
import sqlite3

from cacaimoveis import config
from cacaimoveis.scraper_browser import (
    _area_construida_da_descricao,
    _area_terreno_da_descricao,
    _titulo_da_url,
)

_RE_SO_PRECO = re.compile(r"^R\$\s*[\d.,]+$")


def _banheiros_da_descricao(texto: str | None) -> int | None:
    if not texto:
        return None
    m = re.search(r"(\d+)\s*banheir", texto, re.IGNORECASE)
    return int(m.group(1)) if m else None


def main() -> None:
    conn = sqlite3.connect(config.DB_PATH)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT url, titulo, descricao, area_terreno, area_construida, banheiros "
        "FROM anuncios"
    ).fetchall()

    n_tit = n_terr = n_constr = n_banh = 0
    for r in rows:
        url = r["url"]
        desc = r["descricao"] or ""
        titulo = (r["titulo"] or "").strip()

        novo_titulo = titulo
        if not titulo or _RE_SO_PRECO.match(titulo):
            novo_titulo = _titulo_da_url(url) or titulo
            if novo_titulo != titulo:
                n_tit += 1

        terreno = r["area_terreno"]
        if terreno is None:
            terreno = _area_terreno_da_descricao(desc)
            if terreno is not None:
                n_terr += 1

        construida = r["area_construida"]
        c_desc = _area_construida_da_descricao(desc)
        if c_desc is not None:
            construida = c_desc
            n_constr += 1

        banheiros = r["banheiros"]
        if banheiros is None:
            b_desc = _banheiros_da_descricao(desc)
            if b_desc is not None:
                banheiros = b_desc
                n_banh += 1

        conn.execute(
            "UPDATE anuncios SET titulo=?, area_terreno=?, area_construida=?, "
            "banheiros=? WHERE url=?",
            (novo_titulo, terreno, construida, banheiros, url),
        )

    conn.commit()
    conn.close()
    print(f"Registros analisados : {len(rows)}")
    print(f"titulo corrigido     : {n_tit}")
    print(f"area_terreno preenchida : {n_terr}")
    print(f"area_construida (via descricao): {n_constr}")
    print(f"banheiros (via descricao)     : {n_banh}")


if __name__ == "__main__":
    main()
