"""Sonda o robots.txt dos 4 portais e mede a latencia de cada fonte externa.

Serve para dois fins:
  1. LEGALIDADE — o robots.txt diz o que o dono do site autoriza um robo a
     fazer. E a primeira coisa que um juizo olha.
  2. CADENCIA — quanto tempo cada fonte leva para atualizar. Sem isso, dizer
     "atualiza diariamente" e chute.

Uso: python sonda_fontes.py
"""

from __future__ import annotations

import sys
import time

import requests

try:
    sys.stdout.reconfigure(errors="replace")
except Exception:  # noqa: BLE001
    pass

requests.packages.urllib3.disable_warnings()  # noqa: E402

HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                   "AppleWebKit/537.36 (KHTML, like Gecko) "
                   "Chrome/120.0 Safari/537.36"),
    "Accept-Language": "pt-BR,pt;q=0.9",
}

PORTAIS = [
    ("Imovelweb", "https://www.imovelweb.com.br/robots.txt"),
    ("OLX", "https://www.olx.com.br/robots.txt"),
    ("ZAP", "https://www.zapimoveis.com.br/robots.txt"),
    ("VivaReal", "https://www.vivareal.com.br/robots.txt"),
    ("QuintoAndar", "https://www.quintoandar.com.br/robots.txt"),
]

print("=" * 72)
print("1. ROBOTS.TXT DOS PORTAIS  (o que cada site AUTORIZA)")
print("=" * 72)
for nome, url in PORTAIS:
    try:
        r = requests.get(url, timeout=25, verify=False, headers=HEADERS)
        print()
        print(f"--- {nome}  [{r.status_code}]  {len(r.text):,} bytes")
        keep = []
        for linha in r.text.splitlines():
            l = linha.strip()
            if not l or l.startswith("#"):
                continue
            low = l.lower()
            if low.startswith(("user-agent", "disallow", "allow", "crawl-delay",
                               "sitemap", "host")):
                keep.append(l)
        # resume: quantas regras, quais User-agent
        agents = [k for k in keep if k.lower().startswith("user-agent")]
        print(f"    User-agent declarados: {agents[:8]}")
        # nos interessa: o que vale para '*' e o que vale para busca
        estrela = False
        for k in keep:
            low = k.lower()
            if low.startswith("user-agent"):
                estrela = k.split(":", 1)[1].strip() == "*"
                continue
            if not estrela:
                continue
            if low.startswith("disallow") and len(k) > 10:
                alvo = k.split(":", 1)[1].strip()
                if any(t in alvo.lower() for t in
                       ("/imovel", "/imoveis", "/venda", "/casas", "/busca",
                        "/buscar", "/search", "?", "/comprar")):
                    print(f"    [*] {k[:105]}")
        cds = [k for k in keep if "crawl-delay" in k.lower()]
        print(f"    crawl-delay: {cds if cds else 'nenhum declarado'}")
    except Exception as e:  # noqa: BLE001
        print(f"--- {nome}: ERRO {type(e).__name__}: {str(e)[:70]}")

print()
print("=" * 72)
print("2. LATENCIA E DISPONIBILIDADE DAS FONTES EXTERNAS")
print("=" * 72)
FONTES = [
    ("IBGE IPCA (agregado 1737)",
     "https://servicodados.ibge.gov.br/api/v3/agregados/1737/"
     "periodos/-3/variaveis/2266?localidades=N1[all]"),
    ("BCB SGS 189 (IGP-M)",
     "https://api.bcb.gov.br/dados/serie/bcdata.sgs.189/dados"
     "?formato=json&dataInicial=01/01/2026"),
    ("GeoSampa WFS (cadastro de lotes)",
     "https://wfs.geosampa.prefeitura.sp.gov.br/geoserver/geoportal/"
     "wfs?service=WFS&version=2.0.0&request=GetFeature&"
     "typeNames=geoportal:lote_cidadao&count=1&outputFormat=json&"
     "propertyName=cd_setor_fiscal"),
    ("Prefeitura ITBI (pagina da Fazenda)",
     "https://prefeitura.sp.gov.br/web/fazenda/w/acesso_a_informacao/"
     "20706/itbi"),
    ("Portal dados abertos (CKAN)",
     "https://dadosabertos.prefeitura.sp.gov.br/api/3/action/package_list"),
    ("GLUE API (ZAP/VivaReal)",
     "https://glue-api.vivareal.com/v2/listings?business=SALE&"
     "listingType=USED&categoryPage=RESULT&addressCity=Sao Paulo&"
     "addressState=SP&size=1&from=0&includeFields=search"),
]
for nome, url in FONTES:
    try:
        t0 = time.time()
        r = requests.get(url, timeout=45, verify=False, headers=HEADERS)
        dt = time.time() - t0
        # extrai pistas de atualizacao
        pista = ""
        if "Last-Modified" in r.headers:
            pista = f"Last-Modified: {r.headers['Last-Modified']}"
        elif "last_updated" in r.text[:3000]:
            i = r.text.find("last_updated")
            pista = r.text[i:i + 90].replace("\n", " ")
        print(f"  {nome:<38} {r.status_code} | {dt:>5.1f}s | {len(r.content):>9,}b")
        if pista:
            print(f"       {pista[:96]}")
    except Exception as e:  # noqa: BLE001
        print(f"  {nome:<38} FALHA: {type(e).__name__}: {str(e)[:50]}")
