"""Completa numero e CEP de um anuncio usando as COPIAS do mesmo imovel.

POR QUE EXISTE
--------------
Medido: o ZAP publica numero (44%) e CEP (100%), enquanto OLX e QuintoAndar
nao publicam NENHUM dos dois (2.767 anuncios, 58% da base). Isso derruba a
precisao de tudo que depende de endereco: comparacao com ITBI, area oficial
do cadastro, valor venal e agora a deducao do CEP pela rua.

A deteccao de duplicatas ja descobriu quais anuncios sao o MESMO imovel
(2.857 anuncios em 1.000 grupos, 949 grupos misturando portais). Se numa copia
existe o CEP e na outra nao, o dado pode ser herdado **dentro do grupo**.

Medido antes de escrever este script:

    anuncios sem numero ....... 2.323  ->  955 herdariam (41%)
    anuncios sem cep .......... 1.639  -> 1.388 herdariam (85%)

O custo e ZERO: o dado ja esta no banco, so em outra linha. Nenhuma
requisicao de rede, nenhuma chamada de API.

A REGRA DE CONSISTENCIA
-----------------------
Antes de gravar, confere se o grupo inteiro concorda. Se duas copias trazem
CEPs diferentes, o grupo esta mal formado e NADA e herdado -- melhor manter o
campo vazio do que gravar um valor duvidoso, porque um CEP errado contamina a
comparacao com o ITBI em silencio.

Nunca sobrescreve dado existente: so preenche campo vazio.

Uso:
    python herdar_endereco.py              # mostra o que faria
    python herdar_endereco.py --gravar     # grava de verdade
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from collections import defaultdict

from cacaimoveis import config, endereco

try:
    sys.stdout.reconfigure(errors="replace")
except Exception:  # noqa: BLE001
    pass


def _consenso_rua(itens: list) -> tuple[str, str]:
    """Rua consensual do grupo e o motivo quando NÃO dá para herdar.

    Devolve `(rua, "")` quando há um valor claro, ou `("", motivo)` quando não.

    Compara pela CHAVE (`chave_rua`, que ignora o número), não pelo texto.
    A primeira versão comparava o texto e acusava 157 "conflitos" que eram
    apenas a mesma rua escrita com e sem número ("Rua X" vs "Rua X, 90").

    Dois casos exigem cuidado:
      - mesma rua, UM número distinto entre as cópias -> pode herdar;
      - mesma rua, VÁRIOS números distintos -> NÃO pode. É o caso real de
        "Rua Joaquim Oliveira Freitas, 2228" e ", 2326" no mesmo grupo: são
        imóveis diferentes (ou um anúncio com número errado), e escolher um
        ao acaso gravaria endereço falso.
    """
    nao_vazias = [(i["rua"] or "").strip() for i in itens]
    nao_vazias = [r for r in nao_vazias if r]
    if not nao_vazias:
        return "", ""

    chaves = {endereco.chave_rua(r) for r in nao_vazias}
    if len(chaves) > 1:
        return "", "ruas diferentes"

    numeros = {endereco.numero_do_logradouro(r) for r in nao_vazias}
    numeros.discard("")
    if len(numeros) > 1:
        return "", "numeros diferentes na mesma rua"
    if len(numeros) == 1:
        num = numeros.pop()
        # a rua escrita por extenso, sem nenhum número, serve de molde
        base = next((r for r in nao_vazias if not endereco.numero_do_logradouro(r)),
                    nao_vazias[0])
        if endereco.numero_do_logradouro(base):
            return base, ""
        return f"{base.rstrip(' ,')}, {num}", ""

    # Ninguém tem número: herda a MELHOR grafia da rua (não a normalizada,
    # que é maiúscula sem acento e ficaria feia na tela). Escolhe a mais
    # longa, que em geral é a escrita por extenso: "Rua São Francisco de
    # Assis" em vez de "R Sao Francisco Assis".
    return max(nao_vazias, key=len), ""


def _consenso(valores: list[str]) -> tuple[str, bool]:
    """Valor unico do grupo, e se houve consenso.

    Devolve ("", False) quando ha dois valores diferentes: nesse caso o grupo
    esta mal formado e e mais seguro nao herdar nada.
    """
    distintos = {v for v in valores if v}
    if len(distintos) == 1:
        return distintos.pop(), True
    return "", False


def main() -> int:
    p = argparse.ArgumentParser(
        description="Herda numero e CEP entre copias do mesmo imovel")
    p.add_argument("--gravar", action="store_true",
                   help="grava no banco (sem isso, só mostra o que faria)")
    args = p.parse_args()

    conn = sqlite3.connect(config.DB_PATH)
    conn.row_factory = sqlite3.Row

    grupos: dict[object, list] = defaultdict(list)
    for r in conn.execute("""SELECT url, dup_grupo, rua, cep, bairro
                             FROM anuncios WHERE dup_grupo IS NOT NULL"""):
        grupos[r["dup_grupo"]].append(r)
    print(f"grupos de duplicatas: {len(grupos):,}")

    # Para cada grupo, o consenso de "rua completa (com numero)" e de CEP.
    consenso_rua: dict[object, str] = {}
    consenso_cep: dict[object, str] = {}
    motivos: dict[str, int] = defaultdict(int)
    for g, itens in grupos.items():
        rua, motivo = _consenso_rua(itens)
        if rua:
            consenso_rua[g] = rua
        elif motivo:
            motivos[motivo] += 1

        ceps = [endereco.normalizar_cep(i["cep"]) for i in itens]
        cep, ok = _consenso([c for c in ceps if c])
        if ok:
            consenso_cep[g] = cep
        elif any(ceps):
            motivos["cep divergente"] += 1

    print(f"  grupos com dado a herdar de rua: {len(consenso_rua):,}")
    print(f"  grupos com dado a herdar de cep: {len(consenso_cep):,}")
    for m, n in sorted(motivos.items(), key=lambda x: -x[1]):
        print(f"  NAO herdam · {m}: {n:,} grupos")

    grava_rua: list[tuple[str, str, str]] = []
    grava_cep: list[tuple[str, str]] = []
    for g, itens in grupos.items():
        alvo_rua = consenso_rua.get(g, "")
        alvo_cep = consenso_cep.get(g, "")
        for i in itens:
            rua_atual = (i["rua"] or "").strip()
            # Só preenche campo VAZIO ou sem número. Nunca sobrescreve um
            # endereço que já existe em outro formato.
            if alvo_rua and not endereco.numero_do_logradouro(rua_atual):
                if not rua_atual or endereco.chave_rua(rua_atual) == endereco.chave_rua(alvo_rua):
                    grava_rua.append((alvo_rua, endereco.chave_rua(alvo_rua), i["url"]))
            if alvo_cep and not (i["cep"] or "").strip():
                grava_cep.append((alvo_cep, i["url"]))

    print()
    print(f"  rua a preencher ...: {len(grava_rua):,}")
    print(f"  cep a preencher ...: {len(grava_cep):,}")
    print()
    print("  amostra de rua:")
    for nova, _, u in grava_rua[:5]:
        print(f"    {nova[:44]:<44} <- {u[-42:]}")
    print("  amostra de cep:")
    for cep, u in grava_cep[:5]:
        print(f"    {cep:<10} <- {u[-42:]}")

    if not args.gravar:
        print()
        print("  (nada foi gravado; rode com --gravar para aplicar)")
        conn.close()
        return 0

    conn.executemany("UPDATE anuncios SET rua = ? WHERE url = ?",
                     [(r, u) for r, _, u in grava_rua])
    conn.executemany("UPDATE anuncios SET cep = ? WHERE url = ?", grava_cep)
    # as colunas normalizadas usadas nas cascatas precisam ser refeitas
    conn.executemany(
        "UPDATE anuncios SET rua_norm = ?, rua_chave = ? WHERE url = ?",
        [(endereco.normalizar_logradouro(r), ch, u) for r, ch, u in grava_rua],
    )
    conn.executemany(
        "UPDATE anuncios SET cep_norm = ? WHERE url = ?",
        [(c, u) for c, u in grava_cep],
    )
    conn.commit()

    print()
    print(f"  GRAVADO: {len(grava_rua):,} ruas e {len(grava_cep):,} CEPs")
    print("  rode `comparar_itbi.py --preparar` e `valor_venal.py --calcular"
          " --refazer` para aproveitar o dado novo")

    sem_cep = conn.execute(
        "SELECT COUNT(*) FROM anuncios WHERE COALESCE(cep,'')=''").fetchone()[0]
    sem_rua = conn.execute(
        "SELECT COUNT(*) FROM anuncios WHERE COALESCE(rua,'')=''").fetchone()[0]
    print(f"\n  restam sem cep: {sem_cep:,} | sem rua: {sem_rua:,}")
    conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
