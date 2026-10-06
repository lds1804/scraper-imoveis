"""Índices de reajuste de valores monetários (IPCA e IGP-M).

Por que existe: a base do ITBI tem 20 anos (2006–2026). Um imóvel vendido por
R$ 200 mil em 2008 não é comparável a um de R$ 200 mil hoje — são valores de
épocas diferentes. Para cruzar transações antigas com anúncios atuais, todo
valor precisa ser trazido para a data de referência.

DUAS FONTES, e a escolha NÃO é indiferente:

  IGP-M (padrão) — BCB, série 189. É o índice usado em contrato de ALUGUEL e
    o mais citado no mercado imobiliário. Medido nesta base: é o que melhor
    reconstrói o preço de venda observado.

        GET https://api.bcb.gov.br/dados/serie/bcdata.sgs.189/dados?formato=json

  IPCA — IBGE, agregado 1737, variável 2266 (número-índice, base dez/1993).
    É a inflação GERAL, não a de imóveis.

        GET https://servicodados.ibge.gov.br/api/v3/agregados/1737/
            periodos/-400/variaveis/2266?localidades=N1[all]

RETIFICAÇÃO (2026-10-06): o diagnóstico antigo dizia que `api.bcb.gov.br`
"não resolve DNS nesta máquina" (`getaddrinfo failed`). **Estava errado.** O
BCB resolve e responde em ~0,2 s. O que falhava era o **certificado TLS
self-signed** do host — com `verify=False` tudo funciona. O mesmo engano já
havia sido registrado para o portal de dados abertos da prefeitura. A lição:
"não resolve" e "falha o TLS" produzem o mesmo sintoma no `requests`.

O IGP-M é publicado como VARIAÇÃO % mensal, não número-índice. Então a série é
acumulada uma vez aqui:

    nivel = 100.0
    for cada mes:
        nivel *= (1 + variacao_pct / 100)
        serie[AAAAMM] = nivel

Validado contra os acumulados oficiais: 2020 +23,14% · 2021 +17,79% ·
2022 +5,45% · 2023 −3,18% · 2024 +6,54% — todos batem na 2ª casa.

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

# IGP-M: BCB série 189 (variação % mensal, precisa acumular)
IGPM_URL = ("https://api.bcb.gov.br/dados/serie/bcdata.sgs.189/"
            "dados?formato=json")
# IPCA: agregado 1737, variável 2266 (número-índice, base dez/1993 = 100)
IPCA_URL = ("https://servicodados.ibge.gov.br/api/v3/agregados/1737/"
            "periodos/-400/variaveis/2266?localidades=N1[all]")

HEADERS = {"User-Agent": config.USER_AGENT, "Accept": "application/json"}

# Qual índice corrige os valores. O usuário pediu o IGP-M, e a medição nesta
# base confirma: o IGP-M reconstrói melhor o preço de venda observado do que o
# IPCA (viés 0,93 contra 0,87 — mais perto de 1,00), e reduz os casos absurdos
# (acima do dobro do real) de 9% para 7%. O IGP-M também é o índice do mercado
# imobiliário (contratos de aluguel), enquanto o IPCA é a inflação geral.
INDICE_PADRAO = "igpm"


def _buscar_igpm(verbose: bool = False) -> dict[str, float]:
    """Busca o IGP-M no BCB e ACUMULA em número-índice. {'AAAAMM': indice}.

    O BCB devolve `[{'data': '01/06/1989', 'valor': '19.68'}, ...]`, onde o
    valor é a variação percentual do MÊS, não um nível. Para reajustar é
    preciso o nível acumulado: `nivel *= 1 + pct/100`.
    """
    # O certificado deste host é self-signed: sem `verify=False` o requests
    # falha com SSLError, que dá a impressão de "host fora do ar".
    r = requests.get(IGPM_URL, headers=HEADERS, timeout=60, verify=False)
    r.raise_for_status()
    nivel = 100.0
    serie: dict[str, float] = {}
    for linha in r.json():
        dia, mes, ano = str(linha["data"]).split("/")
        nivel *= 1 + float(linha["valor"]) / 100
        serie[f"{ano}{mes}"] = nivel
    if verbose:
        print(f"IGP-M: {len(serie)} meses ({min(serie)} a {max(serie)})")
    return serie


def _buscar_ipca(verbose: bool = False) -> dict[str, float]:
    """Busca a série do IPCA no IBGE. Devolve {'AAAAMM': indice}."""
    r = requests.get(IPCA_URL, headers=HEADERS, timeout=60)
    r.raise_for_status()
    dados = r.json()
    serie = dados[0]["resultados"][0]["series"][0]["serie"]
    if verbose:
        print(f"IPCA: {len(serie)} meses ({min(serie)} a {max(serie)})")
    return {str(k): float(v) for k, v in serie.items() if v not in (None, "", "...")}


def carregar(indice: str = INDICE_PADRAO, usar_cache: bool = True,
             verbose: bool = False) -> dict[str, float]:
    """Carrega a série do índice pedido (cache local primeiro, API depois).

    O cache guarda os DOIS índices, para alternar entre eles sem bater na API.
    Cache antigo (só `ipca`) é reaproveitado: o que faltar é buscado.
    """
    guardado: dict = {}
    if usar_cache and os.path.exists(CACHE):
        try:
            with open(CACHE, encoding="utf-8") as f:
                guardado = json.load(f)
        except (OSError, json.JSONDecodeError):
            guardado = {}  # cache corrompido: busca de novo
    elif usar_cache:
        # cache antigo pode estar no formato antigo (chave 'ipca' no topo)
        try:
            with open(CACHE, encoding="utf-8") as f:
                guardado = json.load(f)
        except (OSError, json.JSONDecodeError):
            guardado = {}

    if guardado.get(indice):
        if verbose:
            print(f"{indice} do cache: {len(guardado[indice])} meses")
        return guardado[indice]

    serie = _buscar_igpm(verbose) if indice == "igpm" else _buscar_ipca(verbose)
    guardado[indice] = serie
    guardado.setdefault("fonte", {})
    if isinstance(guardado["fonte"], str):
        # cache no formato antigo (fonte era uma string)
        guardado["fonte"] = {"ipca": guardado["fonte"]}
    guardado["fonte"][indice] = (
        "BCB serie 189 (IGP-M, acumulado)" if indice == "igpm"
        else "IBGE agregado 1737/variavel 2266 (IPCA)")
    os.makedirs(os.path.dirname(CACHE), exist_ok=True)
    with open(CACHE, "w", encoding="utf-8") as f:
        json.dump(guardado, f, ensure_ascii=False, indent=1)
    return serie


class Reajustador:
    """Traz valores históricos para a data de referência.

    `indice` escolhe a série: "igpm" (padrão) ou "ipca".
    """

    def __init__(self, indice: str = INDICE_PADRAO,
                 indice_serie: Optional[dict[str, float]] = None,
                 verbose: bool = False):
        self.nome = indice
        if indice_serie is not None:
            self.serie = indice_serie
        else:
            self.serie = carregar(indice, verbose=verbose)
        if not self.serie:
            raise RuntimeError(f"série de índices '{indice}' vazia")
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
