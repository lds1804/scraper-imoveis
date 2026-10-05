"""Mede se trocar IPCA por IGP-M muda alguma coisa.

O projeto documenta "IGP-M nao foi possivel" (NXDOMAIN). Isso CADUCOU: a API
do BCB responde hoje (o problema real era certificado self-signed, nao DNS).
S-rie 189 = IGP-M (indice).

Antes de implementar, a pergunta honesta: muda o resultado? O objetivo de
corrigir valores passados e comparar uma venda de 2010 com o preco de hoje.
Se os dois indices derem fatores parecidos, trocar nao vale o trabalho.

Uso: python medir_igpm.py
"""

from __future__ import annotations

import json
import sys
import time

import requests

try:
    sys.stdout.reconfigure(errors="replace")
except Exception:  # noqa: BLE001
    pass

requests.packages.urllib3.disable_warnings()  # noqa: E402


def serie_sgs(codigo: int, inicio: str = "01/01/2006") -> dict[str, float]:
    """Baixa uma serie do SGS e acumula num numero-indice (base 100)."""
    url = (f"https://api.bcb.gov.br/dados/serie/bcdata.sgs.{codigo}/dados"
           f"?formato=json&dataInicial={inicio}")
    r = requests.get(url, timeout=90, verify=False,
                     headers={"User-Agent": "Mozilla/5.0"})
    r.raise_for_status()
    dados = r.json()
    # as series 189 (IGP-M) e 433 (IPCA) vem como VARIACAO % mensal
    nivel = 100.0
    por_mes: dict[str, float] = {}
    for d in dados:
        try:
            pct = float(str(d["valor"]).replace(",", "."))
        except (TypeError, ValueError):
            continue
        nivel *= (1 + pct / 100)
        # dd/mm/aaaa -> aaaa-mm
        dia, mes, ano = d["data"].split("/")
        por_mes[f"{ano}-{mes}"] = nivel
    return por_mes


print("baixando as duas series do BCB...")
t0 = time.time()
igpm = serie_sgs(189)   # IGP-M
print(f"  IGP-M  (189): {len(igpm):,} meses | {time.time()-t0:.1f}s")
t0 = time.time()
ipca = serie_sgs(433)   # IPCA (variacao mensal)
print(f"  IPCA   (433): {len(ipca):,} meses | {time.time()-t0:.1f}s")
print(f"  IPCA   (433): {len(ipca):,} meses")

print()
print("=" * 72)
print("COMPARACAO: fator de correcao de cada ano ate o ULTIMO mes")
print("=" * 72)
ult_igpm, ult_ipca = max(igpm), max(ipca)
print(f"  referencia: {ult_igpm} (IGP-M) / {ult_ipca} (IPCA)")
print()
print(f"  {'ano':<6} {'IGP-M':>9} {'IPCA':>9} {'diferenca':>11}")
print("-" * 40)
for ano in range(2006, 2027):
    k = f"{ano}-12"
    if k not in igpm or k not in ipca:
        continue
    f_ig = igpm[ult_igpm] / igpm[k]
    f_ip = ipca[ult_ipca] / ipca[k]
    dif = (f_ig / f_ip - 1) * 100
    print(f"  {ano:<6} {f_ig:>9.3f} {f_ip:>9.3f} {dif:>+10.1f}%")

print()
# o que importa: a mediana de R$/m2 de cada ano MUDA muito?
import sqlite3
sys.path.insert(0, "src")
import config
conn = sqlite3.connect(config.DB_PATH)
conn.row_factory = sqlite3.Row

print("=" * 72)
print("EFEITO NO DADO: R$/m2 mediano por ano (so vendas da nossa regiao)")
print("=" * 72)
# pega vendas nas ruas dos anuncios
ruas = {r[0] for r in conn.execute(
    "SELECT DISTINCT rua_chave FROM anuncios WHERE COALESCE(rua_chave,'')<>''")}
print(f"  ruas dos anuncios: {len(ruas):,}")

print()
print(f"  {'ano':<6} {'vend':>6} {'R$/m2 cru':>11} {'x IPCA':>10} {'x IGP-M':>10}")
print("-" * 52)
for ano in range(2006, 2027):
    m2s = []
    for r in conn.execute(
        """SELECT valor_transacao, area_construida, rua_chave
           FROM itbi WHERE ano_arquivo=? AND uso='10'
             AND natureza LIKE '1.%' AND proporcao>=99.9
             AND area_construida BETWEEN 20 AND 500
             AND COALESCE(valor_transacao,0)>10000""", (ano,)):
        if r["rua_chave"] not in ruas:
            continue
        m = r["valor_transacao"] / r["area_construida"]
        if 800 <= m <= 25000:
            m2s.append(m)
    if len(m2s) < 20:
        continue
    m2s.sort()
    cru = m2s[len(m2s) // 2]
    k = f"{ano}-12"
    fi = igpm.get(k)
    fp = ipca.get(k)
    x_ip = cru * (ipca[ult_ipca] / fp) if fp else 0
    x_ig = cru * (igpm[ult_igpm] / fi) if fi else 0
    print(f"  {ano:<6} {len(m2s):>6,} {cru:>11,.0f} {x_ip:>10,.0f} {x_ig:>10,.0f}")
conn.close()
