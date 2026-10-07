"""Mede qual índice leva melhor uma venda antiga até a data de outra: IGP-M, IPCA ou FipeZap.

A pergunta: o ITBI é corrigido pelo IGP-M. O FipeZap (preço pedido de
apartamentos em SP, série mensal de 2008 em diante) acompanharia melhor o
preço de CASAS vendidas?

Método (backtest com pares): pega vendas de casa na MESMA rua e de tamanho
parecido (área construída até ±30% de diferença). Para cada par (i antiga,
j nova), corrige o R$/m² da antiga até a data da nova com cada índice e mede o
erro contra o R$/m² real da nova:

    erro = ln( R$/m²_antiga x (índice[data_nova] / índice[data_antiga]) / R$/m²_nova )

Erro 0 = índice perfeito. Reporta o erro absoluto mediano (quanto menor melhor)
e o viés (mediana do erro com sinal: negativo = o índice subestima a alta).
Só entram pares com mais de 24 meses de intervalo: perto no tempo o índice
quase não importa e o ruído de cada venda domina.

Uso: python experimentos/medir_fipezap.py
"""

from __future__ import annotations

import math
import os
import random
import sqlite3
import statistics
import sys
from collections import defaultdict

import requests

sys.path.insert(0, "src")
from cacaimoveis import config, indices  # noqa: E402

try:
    sys.stdout.reconfigure(errors="replace")
except Exception:  # noqa: BLE001
    pass

XLSX = config.caminho("dados", "fipezap.xlsx")
URL = "https://downloads.fipe.org.br/indices/fipezap/fipezap-serieshistoricas.xlsx"
LACUNA_MIN_MESES = 24


def fipezap_sp(coluna: int = 2) -> dict[str, float]:
    """Número-índice mensal de São Paulo ('AAAAMM' -> índice). coluna 2 = Total."""
    import openpyxl

    if not os.path.exists(XLSX):
        r = requests.get(URL, headers={"User-Agent": "Mozilla/5.0"}, timeout=180)
        r.raise_for_status()
        with open(XLSX, "wb") as f:
            f.write(r.content)
    ws = openpyxl.load_workbook(XLSX, read_only=True, data_only=True)["São Paulo"]
    return {f"{r[1].year}{r[1].month:02d}": float(r[coluna])
            for r in ws.iter_rows(values_only=True)
            if r[1] is not None and hasattr(r[1], "year") and isinstance(r[coluna], (int, float))}


def meses(a: str, b: str) -> int:
    return (int(b[:4]) - int(a[:4])) * 12 + int(b[4:6]) - int(a[4:6])


def main() -> None:
    series = {
        "sem correção": None,
        "IPCA": indices.carregar("ipca"),
        "IGP-M": indices.carregar("igpm"),
        "FipeZap SP": fipezap_sp(2),
    }
    ini = max(min(s) for s in series.values() if s)
    fim = min(max(s) for s in series.values() if s)
    print(f"janela comum dos índices: {ini} a {fim}")

    conn = sqlite3.connect(config.DB_PATH)
    ruas = {r[0] for r in conn.execute(
        "SELECT DISTINCT rua_chave FROM anuncios WHERE COALESCE(rua_chave,'')<>''")}
    por_rua: dict[str, list] = defaultdict(list)
    for chave, valor, area, data in conn.execute(
            """SELECT rua_chave, valor_transacao, area_construida, data_transacao
               FROM itbi WHERE uso='10' AND natureza LIKE '1.%' AND proporcao>=99.9
                 AND area_construida BETWEEN 20 AND 500 AND COALESCE(valor_transacao,0)>10000
                 AND COALESCE(rua_chave,'')<>''"""):
        m2 = valor / area
        mes = (data or "")[:6]
        if chave in ruas and 800 <= m2 <= 25000 and ini <= mes <= fim:
            por_rua[chave].append((mes, m2, area))
    print(f"ruas com venda: {len(por_rua):,} | vendas: {sum(len(v) for v in por_rua.values()):,}")

    random.seed(7)
    pares = []
    for vendas in por_rua.values():
        vendas.sort()
        candidatos = []
        for i in range(len(vendas)):
            for j in range(i + 1, len(vendas)):
                (m0, p0, a0), (m1, p1, a1) = vendas[i], vendas[j]
                if meses(m0, m1) >= LACUNA_MIN_MESES and abs(math.log(a1 / a0)) <= math.log(1.3):
                    candidatos.append((m0, p0, m1, p1))
        random.shuffle(candidatos)
        pares += candidatos[:150]            # tira o peso das ruas com muitas vendas
    print(f"pares (rua igual, área ±30%, >{LACUNA_MIN_MESES} meses): {len(pares):,}\n")

    erros: dict[str, list[float]] = {k: [] for k in series}
    for m0, p0, m1, p1 in pares:
        for nome, s in series.items():
            f = 1.0 if s is None else s[m1] / s[m0]
            erros[nome].append(math.log(p0 * f / p1))

    print(f"{'índice':<14} {'|erro| mediano':>15} {'viés (mediana)':>16} {'|erro| médio':>14}")
    print("-" * 62)
    for nome, e in sorted(erros.items(), key=lambda kv: statistics.median(abs(x) for x in kv[1])):
        print(f"{nome:<14} {statistics.median(abs(x) for x in e) * 100:>14.1f}% "
              f"{statistics.median(e) * 100:>+15.1f}% {statistics.fmean(abs(x) for x in e) * 100:>13.1f}%")

    # por intervalo entre as vendas
    print("\n|erro| mediano por intervalo entre as vendas:")
    faixas = [(24, 48), (48, 84), (84, 1000)]
    print(f"{'intervalo':<14}" + "".join(f"{n:>14}" for n in series) + f"{'pares':>9}")
    for lo, hi in faixas:
        idx = [k for k, (m0, _p0, m1, _p1) in enumerate(pares) if lo <= meses(m0, m1) < hi]
        if len(idx) < 30:
            continue
        linha = "".join(f"{statistics.median(abs(erros[n][k]) for k in idx) * 100:>13.1f}%"
                        for n in series)
        print(f"{lo // 12}-{min(hi // 12, 99)} anos".ljust(14) + linha + f"{len(idx):>9,}")

    # quem ganha par a par: FipeZap x IGP-M
    ganha = sum(1 for a, b in zip(erros["FipeZap SP"], erros["IGP-M"], strict=True)
                if abs(a) < abs(b))
    n = len(pares)
    p = ganha / n
    ep = math.sqrt(p * (1 - p) / n)
    print(f"\nFipeZap erra menos que o IGP-M em {p * 100:.1f}% dos pares "
          f"(±{1.96 * ep * 100:.1f} pontos, 95%)")
    conn.close()


if __name__ == "__main__":
    main()
