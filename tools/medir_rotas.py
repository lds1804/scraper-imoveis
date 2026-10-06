"""Mede o tempo de cada rota da interface, uma a uma, com log ao vivo.

Por que existe: a interface começou a demorar e não havia como ver ONDE.
Este script bate em cada rota e imprime o tempo de cada uma, com `flush`
para o progresso aparecer enquanto roda (o buffer do Python esconde tudo
quando a saída vai para arquivo/pipe).

Uso:
    python tests/medir_rotas.py
"""

from __future__ import annotations

import sys
import time

from _bootstrap import iniciar

iniciar()

import re


def _log(msg: str) -> None:
    print(msg, flush=True)


def main() -> int:
    t0 = time.time()
    from cacaimoveis import webapp

    app = webapp.app
    app.config["TESTING"] = True
    cliente = app.test_client()
    _log(f"import + app criado em {time.time()-t0:.2f}s")

    # --- listagem sem filtro (a mais pesada: 4.793 cards) ---
    t = time.time()
    r = cliente.get("/")
    _log(f"GET /                    {r.status_code} · {len(r.data):>9,d} bytes "
         f"em {time.time()-t:>6.2f}s")

    # --- acha um link de anúncio para testar o detalhe ---
    m = re.search(rb'href="/anuncio/([^"]+)"', r.data)
    if not m:
        _log("NAO achei link de anuncio na listagem")
        return 1
    url = m.group(1).decode("utf-8", "replace")

    # --- filtros, do mais seletivo ao mais amplo ---
    for q in ("?so_quintal=1", "?so_arvores=1", "?com_problemas=1",
              "?so_abaixo=1", "?ordem=preco_asc", "?preco_max=700000"):
        t = time.time()
        r2 = cliente.get("/" + q)
        n = r2.data.count(b'class="card"')
        _log(f"GET /{q:<20} {r2.status_code} · {n:>5,d} cards "
             f"em {time.time()-t:>6.2f}s")

    # --- detalhe ---
    t = time.time()
    r3 = cliente.get("/anuncio/" + url)
    _log(f"GET /anuncio/...         {r3.status_code} · {len(r3.data):>9,d} bytes "
         f"em {time.time()-t:>6.2f}s")

    # --- 404 ---
    t = time.time()
    r4 = cliente.get("/anuncio/nao-existe-xyz")
    _log(f"GET /anuncio/inexistente {r4.status_code} em {time.time()-t:>6.2f}s")

    _log(f"\nTOTAL: {time.time()-t0:.1f}s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
