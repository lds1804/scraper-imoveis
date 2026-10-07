"""Coordenadas dos anúncios (Nominatim/OpenStreetMap), com cache no banco.

Por que existe: o banco não guarda latitude/longitude, e o Street View
embutido precisa delas. O Nominatim é grátis e não pede chave, mas a política
de uso exige no máximo 1 consulta por segundo e um User-Agent que identifique
o aplicativo. Por isso:

  - cada endereço é consultado UMA vez e o resultado fica na tabela `geocode`
    (inclusive "não achei", para não repetir a pergunta a cada visita);
  - as consultas são espaçadas em `INTERVALO_S`;
  - falha de rede NÃO vai para o cache: a próxima visita tenta de novo.

Precisão guardada junto: "casa" (o número bateu) ou "rua" (só a rua). A tela
mostra isso, porque o pino na rua pode estar a uma quadra do imóvel.

Uso:
    caca-geocodificar                 # todos os endereços ainda sem coordenada
    caca-geocodificar --limite 200    # no máximo 200 consultas
"""

from __future__ import annotations

import argparse
import re
import sqlite3
import sys
import time

import requests

from cacaimoveis import config, logs
from cacaimoveis.filtros import sem_acento

log = logs.obter(__name__)

URL = "https://nominatim.openstreetmap.org/search"
# Nominatim exige identificar o aplicativo (sem isso bloqueia o IP)
USER_AGENT = "caca-imoveis-pessoal/1.0 (projeto pessoal de pesquisa de imoveis)"
INTERVALO_S = 1.1
# caixa do município de São Paulo (oeste,norte,leste,sul): evita achar a
# "Rua das Flores" de outra cidade
CAIXA_SP = "-46.83,-23.36,-46.36,-23.99"
# resultado negativo vale 30 dias (o OSM ganha endereços com o tempo)
VALIDADE_NEGATIVO_DIAS = 30

_ultima_consulta = 0.0


def garantir_tabela(conn: sqlite3.Connection) -> None:
    conn.execute(
        """CREATE TABLE IF NOT EXISTS geocode (
               chave TEXT PRIMARY KEY, lat REAL, lon REAL,
               precisao TEXT, consultado_em TEXT)""")


def separar_numero(rua: str) -> tuple[str, str]:
    """'Rua X, 335' -> ('Rua X', '335'). Sem número -> ('Rua X', '')."""
    rua = re.sub(r"[\s,]+$", "", (rua or "").strip())
    m = re.match(r"^(.*?)[,\s]+(\d+[A-Za-z]?)$", rua)
    return (m.group(1).strip(" ,"), m.group(2)) if m else (rua, "")


def chave_do_endereco(rua: str, bairro: str, cep: str = "") -> str:
    nome, numero = separar_numero(rua)
    return "|".join((sem_acento(nome), numero, sem_acento(bairro or ""), cep or ""))


def _consultar(**params) -> dict | None:
    """Uma consulta ao Nominatim. Devolve o 1º resultado ou None (não achou).

    Levanta `requests.RequestException` em falha de rede: quem chama decide
    não gravar nada no cache nesse caso.
    """
    global _ultima_consulta
    espera = INTERVALO_S - (time.monotonic() - _ultima_consulta)
    if espera > 0:
        time.sleep(espera)
    _ultima_consulta = time.monotonic()
    r = requests.get(
        URL, timeout=20, headers={"User-Agent": USER_AGENT, "Accept-Language": "pt-BR"},
        params={"format": "jsonv2", "limit": 1, "countrycodes": "br",
                "viewbox": CAIXA_SP, "bounded": 1, "addressdetails": 1, **params})
    r.raise_for_status()
    achados = r.json()
    return achados[0] if achados else None


def _e_casa(resultado: dict) -> bool:
    return (resultado.get("category") == "building"
            or resultado.get("type") in ("house", "building", "yes", "apartments")
            or resultado.get("addresstype") in ("house", "building"))


def _cep_confere(resultado: dict, cep: str) -> bool:
    """O resultado está na mesma região do CEP do anúncio?

    Medido em amostra de 10 endereços: a busca por nome de rua devolveu a rua
    HOMÔNIMA de outro bairro em 3 (ex.: CEP 05131 do anúncio, 05163 do achado).
    Os 4 primeiros dígitos do CEP formam a sub-região; sem CEP no anúncio ou
    no resultado, não há como conferir e o resultado vale.
    """
    achado = re.sub(r"\D", "", (resultado.get("address") or {}).get("postcode", ""))
    cep = re.sub(r"\D", "", cep or "")
    if len(achado) < 4 or len(cep) < 4:
        return True
    return achado[:4] == cep[:4]


def buscar(rua: str, bairro: str, cep: str = "") -> tuple[float, float, str] | None:
    """(lat, lon, precisao) para o endereço, ou None. Pode levantar erro de rede.

    Tenta, nesta ordem: rua com número + CEP; rua sem número + CEP; texto livre
    com o bairro. Só aceita resultado cujo CEP bata com o do anúncio.
    """
    nome, numero = separar_numero(rua)
    if not nome:
        return None  # só o bairro: o pino cairia no meio dele, sem valor
    cep_fmt = f"{cep[:5]}-{cep[5:]}" if len(cep or "") == 8 else ""
    tentativas = []
    if numero:
        tentativas.append({"street": f"{numero} {nome}", "city": "São Paulo",
                           **({"postalcode": cep_fmt} if cep_fmt else {})})
    tentativas.append({"street": nome, "city": "São Paulo",
                       **({"postalcode": cep_fmt} if cep_fmt else {})})
    if bairro:
        tentativas.append({"q": f"{nome}, {bairro}, São Paulo"})
    for params in tentativas:
        r = _consultar(**params)
        if not r or not _cep_confere(r, cep):
            continue
        com_numero = bool(numero) and "street" in params and params["street"].startswith(numero)
        precisao = "casa" if com_numero and _e_casa(r) else "rua"
        return float(r["lat"]), float(r["lon"]), precisao
    return None


def obter(conn: sqlite3.Connection, rua: str, bairro: str, cep: str = "",
          consultar_rede: bool = True) -> dict | None:
    """Coordenadas do endereço, do cache ou (se permitido) do Nominatim.

    Devolve {"lat", "lon", "precisao"} ou None quando não há coordenada.
    """
    if not separar_numero(rua)[0]:
        return None
    garantir_tabela(conn)
    chave = chave_do_endereco(rua, bairro, cep)
    linha = conn.execute(
        "SELECT lat, lon, precisao, consultado_em FROM geocode WHERE chave = ?",
        (chave,)).fetchone()
    if linha:
        if linha[0] is not None:
            return {"lat": linha[0], "lon": linha[1], "precisao": linha[2]}
        recente = conn.execute(
            "SELECT ? >= datetime('now', ?)",
            (linha[3], f"-{VALIDADE_NEGATIVO_DIAS} days")).fetchone()[0]
        if recente:
            return None
    if not consultar_rede:
        return None
    try:
        achado = buscar(rua, bairro, cep)
    except (requests.RequestException, ValueError) as e:
        log.warning("geocode: falha ao consultar '%s': %s", rua, e)
        return None
    lat, lon, precisao = achado if achado else (None, None, "nenhuma")
    conn.execute(
        "INSERT OR REPLACE INTO geocode (chave, lat, lon, precisao, consultado_em) "
        "VALUES (?, ?, ?, ?, datetime('now'))", (chave, lat, lon, precisao))
    conn.commit()
    return {"lat": lat, "lon": lon, "precisao": precisao} if achado else None


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Busca coordenadas dos endereços dos anúncios")
    ap.add_argument("--limite", type=int, default=0, metavar="N",
                    help="no máximo N consultas ao Nominatim (0 = todas)")
    args = ap.parse_args(argv)

    conn = sqlite3.connect(config.DB_PATH, timeout=30)
    garantir_tabela(conn)
    linhas = conn.execute(
        """SELECT DISTINCT COALESCE(rua, ''), COALESCE(bairro, ''), COALESCE(cep, '')
           FROM anuncios WHERE removido_em IS NULL AND COALESCE(rua, '') <> ''""").fetchall()
    vistos: set[str] = set()
    pendentes = []
    for rua, bairro, cep in linhas:
        chave = chave_do_endereco(rua, bairro, cep)
        if chave in vistos or not separar_numero(rua)[0]:
            continue
        vistos.add(chave)
        if not conn.execute("SELECT 1 FROM geocode WHERE chave = ?", (chave,)).fetchone():
            pendentes.append((rua, bairro, cep))
    if args.limite:
        pendentes = pendentes[:args.limite]
    print(f"{len(pendentes)} endereço(s) a consultar (~{len(pendentes) * 2 * INTERVALO_S / 60:.0f} min no pior caso)",
          flush=True)

    achados = 0
    for i, (rua, bairro, cep) in enumerate(pendentes, 1):
        try:
            if obter(conn, rua, bairro, cep):
                achados += 1
        except KeyboardInterrupt:
            print("\n[interrompido; o que já foi consultado está salvo]")
            break
        if i % 25 == 0:
            print(f"  {i}/{len(pendentes)}  com coordenada: {achados}", flush=True)
    conn.close()
    print(f"Pronto: {achados} de {len(pendentes)} com coordenada.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
