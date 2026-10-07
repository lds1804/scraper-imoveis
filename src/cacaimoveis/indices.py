"""Índices de reajuste de valores monetários (FipeZap, IGP-M e IPCA).

Por que existe: a base do ITBI tem 20 anos (2006–2026). Um imóvel vendido por
R$ 200 mil em 2008 não é comparável a um de R$ 200 mil hoje — são valores de
épocas diferentes. Para cruzar transações antigas com anúncios atuais, todo
valor precisa ser trazido para a data de referência.

TRÊS FONTES, e a escolha NÃO é indiferente:

  FipeZap SP (padrão desde 2026-10-07) — Fipe/ZAP, série mensal de preço pedido
    de imóveis residenciais em São Paulo, de 2008 em diante. É índice de
    IMÓVEIS (de apartamentos prontos, não de casas) e, mesmo assim, venceu os
    outros no backtest de `experimentos/medir_fipezap.py`: em 18.620 pares de
    vendas de casas na mesma rua (área ±30%, >24 meses entre elas), levar a
    venda antiga até a data da nova erra 30,6% (mediana) com o FipeZap, 37,1%
    com o IGP-M e 35,9% com o IPCA; o viés é +0,7% (IGP-M: -0,4%, IPCA: -10,7%).
    Ganha em todos os intervalos e erra menos que o IGP-M em 57% dos pares.
    Os pares reaproveitam vendas, então a margem de ±0,7 ponto é otimista; o
    ganho de 6 pontos não depende dela.

        GET https://downloads.fipe.org.br/indices/fipezap/fipezap-serieshistoricas.xlsx

  IGP-M — BCB, série 189. É o índice usado em contrato de ALUGUEL e
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
import time

import requests

from cacaimoveis import config, logs

log = logs.obter(__name__)

CACHE = config.caminho("dados", "indices.json")

# IGP-M: BCB série 189 (variação % mensal, precisa acumular)
IGPM_URL = ("https://api.bcb.gov.br/dados/serie/bcdata.sgs.189/"
            "dados?formato=json")
# IPCA: agregado 1737, variável 2266 (número-índice, base dez/1993 = 100)
IPCA_URL = ("https://servicodados.ibge.gov.br/api/v3/agregados/1737/"
            "periodos/-400/variaveis/2266?localidades=N1[all]")

# FipeZap: planilha com uma aba por cidade; a de São Paulo, coluna "Total" do
# número-índice de venda residencial (média móvel trimestral).
FIPEZAP_URL = "https://downloads.fipe.org.br/indices/fipezap/fipezap-serieshistoricas.xlsx"
FIPEZAP_XLSX = config.caminho("dados", "fipezap.xlsx")

HEADERS = {"User-Agent": config.USER_AGENT, "Accept": "application/json"}

# Os índices saem uma vez por mês. O cache valia para sempre (só `--refazer`
# limpava), então o gatilho "saiu dado novo" de `comparar_itbi --atualizar` nunca
# disparava sozinho. Agora o cache vence depois de TTL_DIAS e, se a busca falhar,
# o cache velho serve (melhor um índice antigo que nenhum).
TTL_DIAS = 25

# Qual índice corrige os valores. A escolha foi medida duas vezes:
#   2026-10-06: IGP-M batia o IPCA (viés 0,93 contra 0,87).
#   2026-10-07: o FipeZap SP bate os dois no backtest com pares de vendas
#               (ver o docstring do módulo e `experimentos/medir_fipezap.py`).
# Se o FipeZap não puder ser baixado nem estiver em cache, o Reajustador cai
# para o IGP-M e avisa.
INDICE_PADRAO = "fipezap"
INDICES = ("fipezap", "igpm", "ipca")


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


def _buscar_fipezap(verbose: bool = False) -> dict[str, float]:
    """Baixa a planilha do FipeZap e devolve {'AAAAMM': número-índice} de São Paulo."""
    import openpyxl

    r = requests.get(FIPEZAP_URL, headers={"User-Agent": config.USER_AGENT}, timeout=180)
    r.raise_for_status()
    os.makedirs(os.path.dirname(FIPEZAP_XLSX), exist_ok=True)
    with open(FIPEZAP_XLSX, "wb") as f:
        f.write(r.content)
    ws = openpyxl.load_workbook(FIPEZAP_XLSX, read_only=True, data_only=True)["São Paulo"]
    serie = {}
    for linha in ws.iter_rows(values_only=True):
        data, total = linha[1], linha[2]
        if hasattr(data, "year") and isinstance(total, (int, float)):
            serie[f"{data.year}{data.month:02d}"] = float(total)
    if verbose:
        print(f"FipeZap SP: {len(serie)} meses ({min(serie)} a {max(serie)})")
    return serie


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

    baixados = guardado.get("baixado_em")
    if not isinstance(baixados, dict):
        baixados = guardado["baixado_em"] = {}
    antigo = guardado.get(indice)
    if antigo:
        idade_dias = (time.time() - float(baixados.get(indice, 0))) / 86400
        if idade_dias <= TTL_DIAS:
            if verbose:
                print(f"{indice} do cache: {len(antigo)} meses")
            return antigo

    buscar = {"fipezap": _buscar_fipezap, "igpm": _buscar_igpm}.get(indice, _buscar_ipca)
    try:
        serie = buscar(verbose)
    except Exception as e:  # noqa: BLE001
        if antigo:
            log.warning("índice %s não atualizado (%s); usando o cache antigo", indice, e)
            return antigo
        raise
    guardado[indice] = serie
    baixados[indice] = time.time()
    guardado.setdefault("fonte", {})
    if isinstance(guardado["fonte"], str):
        # cache no formato antigo (fonte era uma string)
        guardado["fonte"] = {"ipca": guardado["fonte"]}
    guardado["fonte"][indice] = (
        "Fipe/ZAP, aba São Paulo (FipeZap venda residencial, total)" if indice == "fipezap"
        else "BCB serie 189 (IGP-M, acumulado)" if indice == "igpm"
        else "IBGE agregado 1737/variavel 2266 (IPCA)")
    os.makedirs(os.path.dirname(CACHE), exist_ok=True)
    with open(CACHE, "w", encoding="utf-8") as f:
        json.dump(guardado, f, ensure_ascii=False, indent=1)
    return serie


class Reajustador:
    """Traz valores históricos para a data de referência.

    `indice` escolhe a série: "fipezap" (padrão), "igpm" ou "ipca". Se o FipeZap
    não puder ser obtido (sem rede e sem cache), cai para o IGP-M e avisa: o
    `nome` reflete o que foi realmente usado, e é ele que fica gravado nas
    comparações.
    """

    def __init__(self, indice: str = INDICE_PADRAO,
                 indice_serie: dict[str, float] | None = None,
                 verbose: bool = False):
        self.nome = indice
        if indice_serie is not None:
            self.serie = indice_serie
        else:
            try:
                self.serie = carregar(indice, verbose=verbose)
            except Exception as e:  # noqa: BLE001
                if indice != "fipezap":
                    raise
                log.warning("FipeZap indisponível (%s); usando o IGP-M", e)
                print(f"[aviso] FipeZap indisponível ({e}); usando o IGP-M")
                self.nome = indice = "igpm"
                self.serie = carregar(indice, verbose=verbose)
        if not self.serie:
            raise RuntimeError(f"série de índices '{indice}' vazia")
        self.ultimo = max(self.serie)

    def _indice_de(self, data: str) -> float | None:
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

    def fator(self, data_origem: str, data_alvo: str | None = None) -> float:
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

    def reajustar(self, valor: float | None, data_origem: str,
                  data_alvo: str | None = None) -> float | None:
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
        log.warning("erro tratado, a execução segue: %s", e, exc_info=True)
        print(f"[erro] {e}")
        return 1

    print(f"\nsérie {r.nome} : {len(r.serie)} meses")
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

    p = argparse.ArgumentParser(description="Índices de reajuste (FipeZap, IGP-M, IPCA)")
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
