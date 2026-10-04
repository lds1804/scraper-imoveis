"""Índices de reajuste de valores monetários (IPCA e IGP-M).

Por que existe: a base do ITBI tem 20 anos (2006–2026). Um imóvel vendido por
R$ 200 mil em 2008 não é comparável a um de R$ 200 mil hoje — são valores de
épocas diferentes. Para cruzar transações antigas com anúncios atuais, todo
valor precisa ser trazido para a data de referência.

ARMADILHA (descoberta na prática): a API clássica do BCB
(`api.bcb.gov.br/dados/serie/...`) **não resolve DNS nesta máquina** —
`getaddrinfo failed`. Os outros subdomínios do BCB resolvem (`www`, `olinda`),
então é um problema específico daquele host, não da rede nem do firewall.
Forçar o IP com header `Host` também não funciona (o TLS usa SNI).

Solução: o **IBGE** publica o IPCA como número-índice na sua API de agregados,
que resolve normalmente. É a fonte daqui.

    GET https://servicodados.ibge.gov.br/api/v3/agregados/1737/
        periodos/-300/variaveis/2266?localidades=N1[all]

  agregado 1737 = IPCA
  variavel 2266 = "IPCA - Número-índice (base: dezembro de 1993 = 100)"

Como o índice é encadeado (base fixa, não variação percentual), o reajuste é
uma simples razão entre dois pontos:

    valor_corrente = valor_historico * (indice_data_alvo / indice_data_origem)

Os índices ficam em cache em `dados/indices.json`, para não bater na API a
cada linha de uma planilha com milhões de registros.
"""

from __future__ import annotations

import json
import os
from typing import Optional

import requests

import config

CACHE = config.caminho("dados", "indices.json")

# IPCA: agregado 1737, variável 2266 (número-índice, base dez/1993 = 100)
IPCA_URL = ("https://servicodados.ibge.gov.br/api/v3/agregados/1737/"
            "periodos/-400/variaveis/2266?localidades=N1[all]")

HEADERS = {"User-Agent": config.USER_AGENT, "Accept": "application/json"}


def _buscar_ipca(verbose: bool = False) -> dict[str, float]:
    """Busca a série do IPCA no IBGE. Devolve {'AAAAMM': indice}."""
    r = requests.get(IPCA_URL, headers=HEADERS, timeout=60)
    r.raise_for_status()
    dados = r.json()
    serie = dados[0]["resultados"][0]["series"][0]["serie"]
    if verbose:
        print(f"IPCA: {len(serie)} meses ({min(serie)} a {max(serie)})")
    return {str(k): float(v) for k, v in serie.items() if v not in (None, "", "...")}


def carregar(usar_cache: bool = True, verbose: bool = False) -> dict[str, float]:
    """Carrega os índices (cache local primeiro, API depois)."""
    if usar_cache and os.path.exists(CACHE):
        try:
            with open(CACHE, encoding="utf-8") as f:
                guardado = json.load(f)
            if guardado.get("ipca"):
                if verbose:
                    print(f"índices do cache: {len(guardado['ipca'])} meses")
                return guardado["ipca"]
        except (OSError, json.JSONDecodeError, KeyError):
            pass  # cache corrompido: busca de novo

    ipca = _buscar_ipca(verbose)
    os.makedirs(os.path.dirname(CACHE), exist_ok=True)
    with open(CACHE, "w", encoding="utf-8") as f:
        json.dump({"fonte": "IBGE agregado 1737/variavel 2266 (IPCA)",
                   "ipca": ipca}, f, ensure_ascii=False, indent=1)
    return ipca


class Reajustador:
    """Traz valores históricos para a data de referência."""

    def __init__(self, indice: Optional[dict[str, float]] = None,
                 verbose: bool = False):
        self.serie = indice if indice is not None else carregar(verbose=verbose)
        if not self.serie:
            raise RuntimeError("série de índices vazia")
        self.ultimo = max(self.serie)

    def _indice_de(self, data: str) -> Optional[float]:
        """Índice do mês da data. `data` pode ser 'AAAAMMDD' ou 'AAAAMM'.

        Usa o mês da data e, se não houver (mês ainda não publicado), cai para
        o mês anterior disponível — evita devolver None perto do presente.
        """
        chave = data.replace("-", "").replace("/", "")[:6]
        if chave in self.serie:
            return self.serie[chave]

        # mês não publicado: anda para trás até achar
        ano, mes = int(chave[:4]), int(chave[4:6])
        for _ in range(24):
            mes -= 1
            if mes == 0:
                ano, mes = ano - 1, 12
            alt = f"{ano:04d}{mes:02d}"
            if alt in self.serie:
                return self.serie[alt]
        return None

    def fator(self, data_origem: str, data_alvo: Optional[str] = None) -> float:
        """Multiplicador para levar um valor de `data_origem` até `data_alvo`.

        Devolve 1.0 quando não há como calcular (data fora da série), para o
        valor original ser mantido em vez de virar None silenciosamente.
        """
        alvo = data_alvo or self.ultimo
        i0 = self._indice_de(data_origem)
        i1 = self._indice_de(alvo)
        if not i0 or not i1:
            return 1.0
        return i1 / i0

    def reajustar(self, valor: Optional[float], data_origem: str,
                  data_alvo: Optional[str] = None) -> Optional[float]:
        """Valor histórico -> valor na data de referência."""
        if valor is None:
            return None
        return valor * self.fator(data_origem, data_alvo)

    @property
    def referencia(self) -> str:
        """Mês de referência usado nos reajustes (AAAAMM)."""
        return self.ultimo


def resumo(verbose: bool = True) -> int:
    """Mostra o estado da série de índices (para --verificar)."""
    try:
        r = Reajustador(verbose=verbose)
    except Exception as e:  # noqa: BLE001
        print(f"[erro] {e}")
        return 1

    print(f"\nsérie IPCA (IBGE): {len(r.serie)} meses")
    print(f"referência: {r.referencia}")
    print(f"intervalo : {min(r.serie)} – {max(r.serie)}")

    print("\nfatores de reajuste para a data de referência:")
    for data in ("20060101", "20100101", "20150101", "20200101", "20250101"):
        f = r.fator(data)
        print(f"   {data[:4]}: x{f:>7.4f}   "
              f"(R$ 100.000 -> R$ {100_000 * f:,.0f})".replace(",", "."))
    return 0


def main() -> int:
    import argparse
    import sys

    p = argparse.ArgumentParser(description="Índices de reajuste (IPCA)")
    p.add_argument("--verificar", action="store_true",
                   help="mostra a série e alguns fatores")
    p.add_argument("--refazer", action="store_true",
                   help="ignora o cache e busca de novo na API")
    args = p.parse_args()

    if args.refazer and os.path.exists(CACHE):
        os.remove(CACHE)
        print("cache removido")

    if args.verificar or args.refazer:
        return resumo()
    p.print_help()
    return 0


if __name__ == "__main__":
    import sys

    sys.exit(main())
