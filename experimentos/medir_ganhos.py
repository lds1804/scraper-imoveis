"""Mede duas ideias para preencher numero/CEP sem gastar API.

Ideia 1: os DUPLICADOS ligam a mesma casa em portais diferentes. Se o ZAP tem
numero e CEP e a OLX nao tem nada, o grupo de duplicata permite herdar o dado.
Nao gasta rede: o dado ja esta no banco, so em outra linha.

Ideia 2: a razao venal da RUA erra menos que a do CEP? O anuncio que o usuario
apontou usava a razao do CEP (0,884) enquanto as vendas da propria rua davam
1,031 -- 14% de diferenca.

Uso: python medir_ganhos.py
"""

from __future__ import annotations

import random
import statistics
import sys
from collections import defaultdict

sys.path.insert(0, "src")
from cacaimoveis import config
from cacaimoveis import endereco

try:
    sys.stdout.reconfigure(errors="replace")
except Exception:  # noqa: BLE001
    pass

import sqlite3

conn = sqlite3.connect(config.DB_PATH)
conn.row_factory = sqlite3.Row


def ideia_1() -> None:
    print("=" * 70)
    print("IDEIA 1: os DUPLICADOS podem transferir numero/CEP entre copias?")
    print("=" * 70)
    grupos: dict[object, list] = defaultdict(list)
    for r in conn.execute("""SELECT dup_grupo, url, rua, cep, bairro
                             FROM anuncios WHERE dup_grupo IS NOT NULL"""):
        grupos[r["dup_grupo"]].append(r)

    herda_num = herda_cep = 0
    ads_sem_num = ads_sem_cep = 0
    grupos_viaveis = 0
    for itens in grupos.values():
        tem_num = [i for i in itens if endereco.numero_do_logradouro(i["rua"] or "")]
        tem_cep = [i for i in itens if (i["cep"] or "").strip()]
        if tem_num or tem_cep:
            grupos_viaveis += 1
        for i in itens:
            if not endereco.numero_do_logradouro(i["rua"] or ""):
                ads_sem_num += 1
                if tem_num:
                    herda_num += 1
            if not (i["cep"] or "").strip():
                ads_sem_cep += 1
                if tem_cep:
                    herda_cep += 1

    print(f"  grupos ...................: {len(grupos):,}")
    print(f"  grupos com dado a doar ...: {grupos_viaveis:,}")
    print()
    print(f"  anuncios SEM numero ......: {ads_sem_num:,}")
    print(f"     que HERDARIAM do grupo .: {herda_num:,}"
          f" ({100*herda_num/max(ads_sem_num,1):.0f}%)")
    print(f"  anuncios SEM cep .........: {ads_sem_cep:,}")
    print(f"     que HERDARIAM do grupo .: {herda_cep:,}"
          f" ({100*herda_cep/max(ads_sem_cep,1):.0f}%)")
    print()
    print("  Custo: zero. O dado ja esta no banco, so em outra linha.")


def ideia_2() -> None:
    print()
    print("=" * 70)
    print("IDEIA 2: a razao venal da RUA erra menos que a do CEP?")
    print("=" * 70)
    linhas = []
    for r in conn.execute(
        """SELECT valor_venal_corrigido/valor_transacao_corrigido razao,
                  cep_norm, rua_chave FROM itbi
           WHERE natureza LIKE '1.%' AND proporcao>=99.9 AND uso='10'
             AND ano_arquivo>=2018
             AND COALESCE(valor_venal_corrigido,0)>0
             AND COALESCE(valor_transacao_corrigido,0)>10000
             AND area_construida BETWEEN 20 AND 500
             AND COALESCE(cep,'')<>'' AND COALESCE(rua_chave,'')<>''"""
    ):
        v = r["razao"]
        if not v or not (0.05 < v < 20):
            continue
        d = endereco.normalizar_cep(r["cep_norm"] or "")
        if len(d) >= 5:
            linhas.append((v, d[:5], r["rua_chave"]))
    print(f"  transacoes com cep E rua: {len(linhas):,}")

    random.seed(3)
    random.shuffle(linhas)
    meio = len(linhas) // 2
    treino, teste = linhas[:meio], linhas[meio:]

    def med_por(chave: str, minimo: int, dados):
        g: dict = defaultdict(list)
        for v, c5, rua in dados:
            k = c5 if chave == "cep" else rua
            if k:
                g[k].append(v)
        return {k: statistics.median(v) for k, v in g.items() if len(v) >= minimo}

    m_cep = med_por("cep", 25, treino)
    m_rua = med_por("rua", 5, treino)
    cidade = statistics.median([v for v, _, _ in treino])
    print(f"  regioes: cep5 {len(m_cep):,} | rua (min 5 casos) {len(m_rua):,}")
    print()

    def erro(usar_rua: bool):
        erros = []
        for v, c5, rua in teste:
            if usar_rua and rua in m_rua:
                p = m_rua[rua]
            elif c5 in m_cep:
                p = m_cep[c5]
            else:
                p = cidade
            erros.append(abs(v - p) / v)
        erros.sort()
        return (statistics.median(erros), erros[int(0.75 * len(erros))],
                sum(1 for _, _, r in teste if r in m_rua) / len(teste))

    for nome, ur in (("cidade (piso atual)", False),
                     ("cep5 (o que o app usa)", False),
                     ("rua quando existe", True)):
        md, p75, cob = erro(ur)
        extra = f"  (usa a rua em {100*cob:.0f}% dos casos)" if ur else ""
        print(f"  {nome:<24} erro mediano {100*md:>5.1f}%  p75 {100*p75:>5.1f}%{extra}")


if __name__ == "__main__":
    ideia_1()
    ideia_2()
    conn.close()
