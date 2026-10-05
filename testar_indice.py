"""Qual indice faz as vendas da MESMA RUA convergirem?

O IGP-M voltou a funcionar (a API do BCB responde hoje; o diagnostico antigo
de NXDOMAIN caducou por causa do certificado self-signed).

Mas trocar de indice so vale se MELHORAR o dado. O criterio objetivo:

    se o indice esta certo, uma venda de 2010 e outra de 2024 na MESMA RUA
    devem virar o mesmo R$/m2 depois de corrigidas.

Entao: para cada rua com vendas em varios anos, mede-se a dispersao
intrarrua do R$/m2 corrigido. Vence o indice que deixar as ruas mais
coerentes consigo mesmas.

Isto e o teste honesto. Escolher "porque o usuario pediu" ou "porque e o
padrao de aluguel" nao e medicao.

Uso: python testar_indice.py
"""

from __future__ import annotations

import statistics
import sys

import requests

try:
    sys.stdout.reconfigure(errors="replace")
except Exception:  # noqa: BLE001
    pass

requests.packages.urllib3.disable_warnings()  # noqa: E402

import sqlite3  # noqa: E402

sys.path.insert(0, "src")
import config  # noqa: E402


def serie(codigo: int, inicio: str = "01/01/2006") -> dict[str, float]:
    url = (f"https://api.bcb.gov.br/dados/serie/bcdata.sgs.{codigo}/dados"
           f"?formato=json&dataInicial={inicio}")
    r = requests.get(url, timeout=90, verify=False,
                     headers={"User-Agent": "Mozilla/5.0"})
    r.raise_for_status()
    nivel = 100.0
    por_mes: dict[str, float] = {}
    for d in r.json():
        try:
            pct = float(str(d["valor"]).replace(",", "."))
        except (TypeError, ValueError):
            continue
        nivel *= (1 + pct / 100)
        dia, mes, ano = d["data"].split("/")
        por_mes[f"{ano}-{mes}"] = nivel
    return por_mes


print("baixando series do BCB...")
igpm = serie(189)
ipca = serie(433)
# IPCA tambem pelo numero-indice do IBGE ja usado no projeto
ref_ig, ref_ip = max(igpm), max(ipca)
print(f"  IGP-M: {len(igpm)} meses   IPCA: {len(ipca)} meses")

conn = sqlite3.connect(config.DB_PATH)
conn.row_factory = sqlite3.Row

ruas_anuncio = {r[0] for r in conn.execute(
    "SELECT DISTINCT rua_chave FROM anuncios WHERE COALESCE(rua_chave,'')<>''")}
print(f"  ruas dos anuncios: {len(ruas_anuncio):,}")

# junta as vendas por (rua, ano), na faixa de R$/m2 plausivel
por_rua: dict[str, dict[int, list[float]]] = {}
n = 0
for r in conn.execute(
        """SELECT rua_chave, ano_arquivo, valor_transacao, area_construida
           FROM itbi
           WHERE uso='10' AND natureza LIKE '1.%' AND proporcao>=99.9
             AND area_construida BETWEEN 20 AND 500
             AND COALESCE(valor_transacao,0)>10000
             AND COALESCE(rua_chave,'')<>''"""):
    if r["rua_chave"] not in ruas_anuncio:
        continue
    m = r["valor_transacao"] / r["area_construida"]
    if not (800 <= m <= 25000):
        continue
    por_rua.setdefault(r["rua_chave"], {}).setdefault(
        r["ano_arquivo"], []).append(m)
    n += 1
print(f"  vendas usadas: {n:,} em {len(por_rua):,} ruas")

# so ruas com 2+ anos distintos, cada um com 2+ vendas
ruas = {}
for rua, por_ano in por_rua.items():
    bons = {a: v for a, v in por_ano.items() if len(v) >= 2}
    if len(bons) >= 2:
        ruas[rua] = bons
print(f"  ruas com 2+ anos x 2+ vendas: {len(ruas):,}")


def mediana(v: list[float]) -> float:
    return statistics.median(v)


def dispersao(fator) -> tuple[float, int]:
    """CV medio (mediana) do R$/m2 corrigido dentro de cada rua."""
    cvs = []
    for por_ano in ruas.values():
        med = []
        for ano, vals in por_ano.items():
            f = fator(ano)
            if not f:
                continue
            med.append(mediana([v * f for v in vals]))
        if len(med) < 2:
            continue
        m = mediana(med)
        if m <= 0:
            continue
        # desvio absoluto mediano relativo a mediana da rua
        desv = mediana([abs(x - m) for x in med]) / m
        cvs.append(desv)
    return mediana(cvs) * 100, len(cvs)


def f_ipca(ano: int):
    k = f"{ano}-12"
    return ipca[ref_ip] / ipca[k] if k in ipca else None


def f_igpm(ano: int):
    k = f"{ano}-12"
    return igpm[ref_ig] / igpm[k] if k in igpm else None


def f_nenhum(ano: int):
    return 1.0


print()
print("=" * 70)
print("DISPENSAO INTRARRUA do R$/m2 (menor e melhor)")
print("=" * 70)
res = {}
for nome, f in (("sem correcao", f_nenhum),
                ("IPCA (atual)", f_ipca),
                ("IGP-M (pedido)", f_igpm)):
    d, k = dispersao(f)
    res[nome] = d
    print(f"  {nome:<18} {d:>6.1f}%   (em {k:,} ruas)")

print()
melhor = min(res, key=res.get)
print(f"  -> menor dispersao: {melhor}")
d_ip, d_ig = res["IPCA (atual)"], res["IGP-M (pedido)"]
print(f"  -> IGP-M vs IPCA: {(d_ig - d_ip) / d_ip * 100:+.1f}% de dispersao")

# teste pareado: so as ruas onde os dois indices divergem bastante
print()
print("=" * 70)
print("TESTE PAREADO: so as ruas onde IGP-M e IPCA divergem mais de 10%")
print("=" * 70)
ganha_ig = ganha_ip = empate = 0
for rua, por_ano in ruas.items():
    # so anos que os DOIS indices cobrem (2026 ainda nao tem dezembro)
    anos = sorted(a for a in por_ano
                  if f_igpm(a) is not None and f_ipca(a) is not None)
    if len(anos) < 2:
        continue
    div = abs(f_igpm(anos[0]) / f_ipca(anos[0]) - 1)
    if div < 0.10:
        continue

    def cv_rua(f):
        med = [mediana([v * f(a) for v in por_ano[a]]) for a in anos]
        m = mediana(med)
        return mediana([abs(x - m) for x in med]) / m if m else 1
    c_ip, c_ig = cv_rua(f_ipca), cv_rua(f_igpm)
    if abs(c_ig - c_ip) < 1e-9:
        empate += 1
    elif c_ig < c_ip:
        ganha_ig += 1
    else:
        ganha_ip += 1
print(f"  IGP-M mais coerente: {ganha_ig:,} ruas")
print(f"  IPCA  mais coerente: {ganha_ip:,} ruas")
print(f"  empate:              {empate:,} ruas")
tot = ganha_ig + ganha_ip
if tot:
    print(f"  -> IGP-M vence em {ganha_ig / tot * 100:.1f}% das ruas decididas")
conn.close()
