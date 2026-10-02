"""Auditoria: mostra os anúncios fora de São Paulo e o resumo por lote de busca.

Uso:
    python auditar_localidade.py            # só relatório
    python auditar_localidade.py --limpar   # remove os anúncios fora de SP
"""

from __future__ import annotations

import argparse
import collections
import os
import shutil
import sqlite3

import config
from scraper_browser import _sem_acento, cidade_do_endereco, bairro_do_endereco


def _e_sp(endereco: str) -> bool:
    cidade = _sem_acento(cidade_do_endereco(endereco)).lower()
    if not cidade:
        return True  # sem endereço: não julga
    return any(
        cidade.startswith(_sem_acento(c).lower()) for c in config.CIDADES_ACEITAS
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--limpar", action="store_true", help="remove os de fora de SP")
    parser.add_argument(
        "--limpar-fora-alvo",
        action="store_true",
        help="remove anúncios cujo bairro NÃO está em config.BAIRROS",
    )
    parser.add_argument(
        "--corrigir-bairros",
        action="store_true",
        help="sobrescreve a coluna `bairro` com o bairro REAL do endereço",
    )
    parser.add_argument(
        "--saida",
        default="",
        help="grava o relatório em arquivo UTF-8 (o console do Windows quebra acentos)",
    )
    args = parser.parse_args()

    linhas: list[str] = []

    def out(txt: str = "") -> None:
        linhas.append(txt)

    conn = sqlite3.connect(config.DB_PATH)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT url, titulo, bairro, endereco, preco FROM anuncios ORDER BY bairro, titulo"
    ).fetchall()

    dentro = [r for r in rows if _e_sp(r["endereco"])]
    fora = [r for r in rows if not _e_sp(r["endereco"])]

    out(f"TOTAL de anúncios : {len(rows)}")
    out(f"  em São Paulo    : {len(dentro)}")
    out(f"  fora de SP      : {len(fora)}")

    if fora:
        out("\n=== FORA DE SÃO PAULO ===")
        for r in fora:
            local = bairro_do_endereco(r["endereco"]) or "(?)"
            cidade = cidade_do_endereco(r["endereco"]) or "(?)"
            out(f"  [busca: {r['bairro']}] {local} — {cidade}")
            out(f"      {r['titulo'][:70]}")

    out("\n=== RESUMO POR LOTE DE BUSCA ===")
    resumo: dict[str, list[int]] = collections.defaultdict(lambda: [0, 0])
    for r in rows:
        resumo[r["bairro"] or "(sem bairro)"][0 if _e_sp(r["endereco"]) else 1] += 1
    for lote, (ok, ruim) in sorted(resumo.items()):
        marca = "   <-- PROBLEMA" if ruim else ""
        out(f"  {lote:24s} SP={ok:4d}   fora={ruim:4d}{marca}")

    # bairros reais dentro de SP (útil para conferir a grafia)
    out("\n=== BAIRROS REAIS (dentro de SP) ===")
    reais = collections.Counter(
        bairro_do_endereco(r["endereco"]) or "(?)" for r in dentro
    )
    for nome, n in reais.most_common():
        out(f"  {n:4d}  {nome}")

    if args.limpar_fora_alvo:
        out("\n=== FORA DA LISTA-ALVO ===")
        alvo_nomes = {b.nome for b in config.BAIRROS}
        remover = [r for r in dentro if (r["bairro"] or "") not in alvo_nomes]
        if not remover:
            out("  nenhum (todos os bairros estão na lista).")
        for r in remover:
            out(f"  [{r['bairro']}] {r['rua'] or ''} — R$ {(r['preco'] or 0):,.0f}")
            out(f"      {(r['titulo'] or '')[:66]}")
        if remover:
            out(f"\n  removendo {len(remover)}...")
            for r in remover:
                for f in conn.execute(
                    "SELECT arquivo_local FROM fotos WHERE anuncio_url = ?", (r["url"],)
                ).fetchall():
                    try:
                        if os.path.exists(f["arquivo_local"]):
                            os.remove(f["arquivo_local"])
                    except OSError:
                        pass
                conn.execute("DELETE FROM fotos WHERE anuncio_url = ?", (r["url"],))
                conn.execute("DELETE FROM anuncios WHERE url = ?", (r["url"],))
                pasta = os.path.join(config.FOTOS_DIR, _slug(r["url"]))
                if os.path.isdir(pasta) and not os.listdir(pasta):
                    shutil.rmtree(pasta, ignore_errors=True)
            conn.commit()
            out(f"  restam {conn.execute('SELECT COUNT(*) FROM anuncios').fetchone()[0]} anúncios.")

    if args.corrigir_bairros:
        out("\n=== CORRIGINDO A COLUNA `bairro` ===")
        corrigidos = 0
        for r in dentro:
            real = bairro_do_endereco(r["endereco"])
            if real and real != r["bairro"]:
                conn.execute(
                    "UPDATE anuncios SET bairro = ? WHERE url = ?", (real, r["url"])
                )
                out(f"  {r['bairro']}  ->  {real}")
                corrigidos += 1
        conn.commit()
        out(f"  {corrigidos} registro(s) corrigido(s).")

    if args.limpar and fora:
        out(f"\n=== REMOVENDO {len(fora)} anúncios fora de SP ===")
        for r in fora:
            # apaga fotos do disco + registros
            for f in conn.execute(
                "SELECT arquivo_local FROM fotos WHERE anuncio_url = ?", (r["url"],)
            ).fetchall():
                try:
                    if os.path.exists(f["arquivo_local"]):
                        os.remove(f["arquivo_local"])
                except OSError:
                    pass
            conn.execute("DELETE FROM fotos WHERE anuncio_url = ?", (r["url"],))
            conn.execute("DELETE FROM anuncios WHERE url = ?", (r["url"],))

            # remove a pasta de fotos se ficou vazia
            pasta = os.path.join(config.FOTOS_DIR, _slug(r["url"]))
            if os.path.isdir(pasta) and not os.listdir(pasta):
                shutil.rmtree(pasta, ignore_errors=True)

            out(f"  removido: {r['titulo'][:60]}")
        conn.commit()
        out(f"\nRestam {conn.execute('SELECT COUNT(*) FROM anuncios').fetchone()[0]} anúncios.")

    conn.close()

    texto = "\n".join(linhas)
    if args.saida:
        with open(args.saida, "w", encoding="utf-8") as f:
            f.write(texto)
        print(f"Relatório salvo em {args.saida}")
    else:
        print(texto)


def _slug(url: str) -> str:
    from storage import _slug as s

    return s(url)


if __name__ == "__main__":
    main()
