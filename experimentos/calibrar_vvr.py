"""Calibra o valor venal de referência (VVR, base do ITBI) a partir do cadastro do IPTU.

Contexto: a consulta oficial da prefeitura ("Valor Venal de Referência") devolveu
R$ 669.312 para um imóvel cujo venal do IPTU, calculado por nós com a fórmula
da Lei 10.235/86, é R$ 497.181 (-26%). Os dois NÃO são a mesma coisa: o VVR é a
base do ITBI. O ITBI público traz o `valor_venal` de cada venda, e cruzando por
endereço com o cadastro do IPTU dá para medir a relação entre os dois.

O que se mede (vendas de 2025 em diante, endereço único no cadastro):

    vvr_itbi  ~  a * venal_terreno + b * venal_construcao + c * esquina * venal_terreno

`esquina` = lote de esquina, cujo fator a conta do IPTU em `iptu.py` não aplica.
Separa-se uma parte das vendas para validar (o ajuste é feito em 70% e testado
nos 30% restantes, por rua, para não vazar vizinhos).

Uso: python experimentos/calibrar_vvr.py
"""

from __future__ import annotations

import random
import re
import sqlite3
import statistics
import sys

sys.path.insert(0, "src")
from cacaimoveis import config  # noqa: E402

try:
    sys.stdout.reconfigure(errors="replace")
except Exception:  # noqa: BLE001
    pass


def _num(s) -> int:
    return int(re.sub(r"\D", "", s or "") or 0)


def resolver(m: list[list[float]], y: list[float]) -> list[float]:
    """Mínimos quadrados pelas equações normais (3x3, sem numpy)."""
    n = len(m[0])
    a = [[sum(r[i] * r[j] for r in m) for j in range(n)] for i in range(n)]
    b = [sum(r[i] * yy for r, yy in zip(m, y, strict=True)) for i in range(n)]
    for i in range(n):                                    # eliminação de Gauss
        p = max(range(i, n), key=lambda k: abs(a[k][i]))
        a[i], a[p], b[i], b[p] = a[p], a[i], b[p], b[i]
        for k in range(i + 1, n):
            f = a[k][i] / a[i][i]
            for j in range(i, n):
                a[k][j] -= f * a[i][j]
            b[k] -= f * b[i]
    x = [0.0] * n
    for i in reversed(range(n)):
        x[i] = (b[i] - sum(a[i][j] * x[j] for j in range(i + 1, n))) / a[i][i]
    return x


def main() -> None:
    conn = sqlite3.connect(config.DB_PATH)
    vendas: dict[tuple, list] = {}
    for rua, numero, vv, vtr in conn.execute(
            """SELECT rua_chave, numero, valor_venal, valor_transacao FROM itbi
               WHERE data_transacao >= '20250101' AND valor_venal > 0 AND valor_transacao > 50000
                 AND COALESCE(rua_chave,'') <> '' AND COALESCE(numero,'') <> ''"""):
        vendas.setdefault((rua, _num(numero)), []).append((vv, vtr))

    cad: dict[tuple, list] = {}
    for rua, numero, vt, vc, esq in conn.execute(
            """SELECT rua_chave, numero, venal_terreno, venal_construcao, esquinas
               FROM iptu WHERE venal_terreno IS NOT NULL"""):
        k = (rua, _num(numero))
        if k in vendas:
            cad.setdefault(k, []).append((vt or 0.0, vc or 0.0, 1 if (esq or 0) > 0 else 0))

    linhas = []     # (rua, vv, vt, vc, esq)
    for k, lotes in cad.items():
        if len(lotes) != 1:
            continue                                      # endereço ambíguo (condomínio, etc.)
        vt, vc, esq = lotes[0]
        if vt + vc <= 0:
            continue
        for vv, _vtr in vendas[k]:
            linhas.append((k[0], vv, vt, vc, esq))
    print(f"pares (endereço único): {len(linhas):,} | com esquina: "
          f"{sum(1 for x in linhas if x[4]):,}")

    ruas = sorted({x[0] for x in linhas})
    random.seed(11)
    random.shuffle(ruas)
    treino_ruas = set(ruas[:int(len(ruas) * 0.7)])
    treino = [x for x in linhas if x[0] in treino_ruas]
    teste = [x for x in linhas if x[0] not in treino_ruas]

    def razao(grupo, esq=None):
        r = [vv / (vt + vc) for _, vv, vt, vc, e in grupo if esq is None or e == esq]
        return (statistics.median(r), len(r)) if r else (float("nan"), 0)

    print("\nvenal do ITBI / nosso venal do IPTU (mediana):")
    for rot, e in (("todos", None), ("sem esquina", 0), ("com esquina", 1)):
        m, n = razao(linhas, e)
        print(f"  {rot:<12} {m:.3f}   (n={n:,})")

    # ajuste: vv = a*vt + b*vc + c*esq*vt, em porcentagem do erro (pesos 1/vv)
    X = [[vt / vv, vc / vv, e * vt / vv] for _, vv, vt, vc, e in treino]
    a, b, c = resolver(X, [1.0] * len(treino))
    print(f"\najuste em {len(treino):,} vendas ({len(treino_ruas)} ruas):")
    print(f"  VVR = {a:.3f} x terreno + {b:.3f} x construção + {c:.3f} x esquina x terreno")

    def erros(grupo, f):
        return [f(vt, vc, e) / vv - 1 for _, vv, vt, vc, e in grupo]

    modelos = {
        "nosso venal do IPTU (sem ajuste)": lambda vt, vc, e: vt + vc,
        "x fator único": None,
        "ajuste (terreno, construção, esquina)": lambda vt, vc, e: a * vt + b * vc + c * e * vt,
    }
    k = statistics.median(vv / (vt + vc) for _, vv, vt, vc, _e in treino)
    modelos["x fator único"] = lambda vt, vc, e: k * (vt + vc)
    print(f"\nvalidação em {len(teste):,} vendas de ruas que o ajuste não viu "
          f"(erro = estimado / VVR do ITBI - 1):")
    print(f"  {'modelo':<42} {'|erro| mediano':>15} {'viés':>8}")
    for nome, f in modelos.items():
        e = erros(teste, f)
        print(f"  {nome:<42} {statistics.median(abs(x) for x in e) * 100:>14.1f}% "
              f"{statistics.median(e) * 100:>+7.1f}%")

    # o caso que motivou: SQL 078.256.0024.1 (R$ 669.312 oficial; terreno 270.311, construção 226.870, esquina)
    vt, vc = 270311.38, 226870.0
    print("\ncaso conferido na prefeitura (VVR oficial R$ 669.312, lote de esquina):")
    for nome, f in modelos.items():
        est = f(vt, vc, 1)
        print(f"  {nome:<42} R$ {est:>10,.0f}  ({est / 669312 - 1:+.1%})".replace(",", "."))
    print(f"\nconstantes para o código: a={a:.4f} b={b:.4f} c={c:.4f}  fator_unico={k:.4f}")


if __name__ == "__main__":
    main()
