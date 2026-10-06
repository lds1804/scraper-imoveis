"""Valor venal OFICIAL do IPTU, calculado pelo cadastro aberto da prefeitura.

POR QUE EXISTE
--------------
O `valor_venal.py` estima um valor de referência a partir do PREÇO (preço x
razão venal/mercado da região). Isso serve para o ITBI, mas não diz nada de
independente: o número é o próprio preço multiplicado por uma constante.

A prefeitura publica todo ano a base do IPTU (cadastro TPCL) no GeoSampa, com
3,9 milhões de unidades e, para cada uma, os insumos do cálculo oficial do
venal: valor do m² do terreno (Planta Genérica de Valores, por face de quadra),
valor do m² da construção (pelo tipo e padrão), testada, tipo de lote, fração
ideal e fator de obsolescência. Com eles o venal sai pela REGRA DA LEI, sem
usar preço nenhum.

A FÓRMULA (Lei 10.235/86, arts. 4º a 11 e Tabelas I a IV)
---------------------------------------------------------
    terreno    = área do terreno x valor m² do terreno
                 x fator de profundidade (Tabela I)
                 x fator do tipo de lote (Tabela III: fundos 0,6; encravado
                   0,5; interno 0,7) x fração ideal
    construção = área construída x valor m² de construção
                 x fator de obsolescência (vem pronto no cadastro)
    venal      = terreno + construção

Fator de profundidade (Tabela I), com p = área / testada em metros inteiros,
limitada a 10..200: p < 20 -> sqrt(p/20); 20 <= p <= 40 -> 1; p > 40 ->
sqrt(40/p). Confere com a tabela da lei (11 -> 0,7416; 41 -> 0,9877; 80 ->
0,7071; 200+ -> 0,4472). A lei agrupa algumas faixas acima de 80 m ("81 e
82"); a fórmula difere delas em menos de 0,5%.

O QUE NÃO ENTRA (e o efeito)
----------------------------
  - Fator de ESQUINA (Tabela II: 1,1 a 1,3 conforme a subdivisão fiscal da
    zona, aplicado até 900 m²). A subdivisão não está no arquivo. Lotes de
    esquina ficam marcados (`esquina = 1`) e o venal deles pode estar 10% a
    30% abaixo do oficial na parte do terreno.
  - Ajustes da revisão da PGV para 2026 (Lei 18.330/2025) que não estejam
    refletidos nos valores unitários do próprio arquivo.

DE ONDE VEM O ARQUIVO
---------------------
GeoSampa > Download de Arquivos > 12_Cadastro > IPTU_INTER > XLS_CSV >
IPTU_<ano>.zip (~70 MB; o CSV tem ~940 MB). Sai um novo por ano. O site tem
proteção anti-robô, então o download é feito no navegador e o arquivo vai
para `dados/iptu/`. `caca-atualizar --completo` avisa quando o do ano corrente
ainda não foi baixado.

Uso:
    caca-iptu --ingerir                  # o IPTU_<ano>.zip mais novo em dados/iptu/
    caca-iptu --ingerir --arquivo X.zip
    caca-iptu --calcular                 # venal de cada anúncio
    caca-iptu --situacao                 # que ano está no banco, o que falta
"""

from __future__ import annotations

import argparse
import csv
import datetime
import glob
import io
import math
import os
import re
import sqlite3
import statistics
import sys
import time
import zipfile

from cacaimoveis import config, endereco, logs, migracoes

log = logs.obter(__name__)

PASTA = config.caminho("dados", "iptu")
URL_GEOSAMPA = (
    "https://geosampa.prefeitura.sp.gov.br/PaginasPublicas/downloadIfr.aspx"
    "?orig=DownloadTemas&arq=12_Cadastro%5CIPTU_INTER%5CXLS_CSV%5C%5CIPTU_{ano}.zip"
    "&arqTipo=zip"
)
CAMINHO_NO_SITE = ("GeoSampa > Download de Arquivos > 12_Cadastro > IPTU_INTER > "
                   "XLS_CSV > IPTU_{ano}.zip")

# Tabela III da Lei 10.235/86 (o rótulo é o do campo TIPO DE TERRENO)
FATOR_TIPO_LOTE = {
    "Lote de fundos": 0.60,
    "Lote encravado": 0.50,
    "Terreno interno": 0.70,
}

# Só casas: o padrão de construção "Residencial horizontal" separa casa de
# apartamento (que é "Residencial vertical"), qualquer que seja o uso.
PADRAO_CASA = "Residencial horizontal"

# Mínimo de casas parecidas para usar a mediana da rua/CEP quando o anúncio
# não casa com um lote específico, e o que é "parecida" (mesma regra do ITBI)
MIN_LOTES = 5
TOLERANCIA_AREA = 0.25


# ---------------------------------------------------------------------------
# A fórmula
# ---------------------------------------------------------------------------
def fator_profundidade(area_terreno: float, testada: float, n_esquinas: int = 0) -> float:
    """Tabela I da Lei 10.235/86 (ver o topo do módulo)."""
    if n_esquinas >= 2 or not testada or not area_terreno:
        return 1.0
    p = int(area_terreno / testada)            # "desprezando a fração de metro"
    p = max(10, min(200, p))
    if p < 20:
        return math.sqrt(p / 20)
    if p <= 40:
        return 1.0
    return math.sqrt(40 / p)


def venal(area_terreno: float, area_construida: float, v_m2_terreno: float,
          v_m2_construcao: float, testada: float = 0.0, tipo_terreno: str = "Normal",
          fracao_ideal: float = 1.0, fator_obsolescencia: float = 1.0,
          n_esquinas: int = 0) -> tuple[float, float]:
    """(venal do terreno, venal da construção) pela regra da lei."""
    terreno = (area_terreno * v_m2_terreno
               * fator_profundidade(area_terreno, testada, n_esquinas)
               * FATOR_TIPO_LOTE.get(tipo_terreno, 1.0)
               * (fracao_ideal or 1.0))
    construcao = area_construida * v_m2_construcao * (fator_obsolescencia or 1.0)
    return terreno, construcao


# ---------------------------------------------------------------------------
# Arquivo
# ---------------------------------------------------------------------------
def arquivos_locais() -> dict[int, str]:
    """{ano: caminho} dos IPTU_<ano>.zip/csv em dados/iptu/."""
    saida = {}
    for caminho in glob.glob(os.path.join(PASTA, "IPTU_*.*")):
        m = re.search(r"IPTU_(\d{4})\.(zip|csv)$", caminho, re.I)
        if m:
            saida[int(m.group(1))] = caminho
    return saida


def _abrir_csv(caminho: str):
    if caminho.lower().endswith(".zip"):
        z = zipfile.ZipFile(caminho)
        nome = next(n for n in z.namelist() if n.lower().endswith(".csv"))
        return io.TextIOWrapper(z.open(nome), encoding="utf-8", errors="replace")
    return open(caminho, encoding="utf-8", errors="replace")


def _num(v) -> float:
    try:
        return float(str(v).replace(",", ".")) if v not in (None, "") else 0.0
    except ValueError:
        return 0.0


def _numero(v) -> str:
    """'0013' -> '13'; '13A' -> '13'. Só o número, para casar com o anúncio."""
    m = re.match(r"\s*0*(\d+)", str(v or ""))
    return m.group(1) if m else ""


def criar_tabela(conn: sqlite3.Connection) -> None:
    """Tabela de cálculo (o site não lê): só as casas do cadastro."""
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS iptu (
            sql              TEXT,
            ano              INTEGER,
            logradouro       TEXT,
            numero           TEXT,
            cep              TEXT,
            rua_chave        TEXT,
            area_terreno     REAL,
            area_construida  REAL,
            v_m2_terreno     REAL,
            v_m2_construcao  REAL,
            testada          REAL,
            tipo_terreno     TEXT,
            esquinas         INTEGER,
            fracao_ideal     REAL,
            fator_obsolescencia REAL,
            ano_construcao   INTEGER,
            pavimentos       INTEGER,
            padrao           TEXT,
            uso              TEXT,
            venal_terreno    REAL,
            venal_construcao REAL
        );
        """
    )


def ingerir(conn: sqlite3.Connection, caminho: str, verbose: bool = True) -> int:
    """Lê o CSV do IPTU e grava SÓ as casas, com o venal já calculado.

    Substitui o ano anterior inteiro: o cadastro de um ano já traz todos os
    imóveis, e guardar dois anos dobraria o banco sem uso.
    """
    criar_tabela(conn)
    m = re.search(r"IPTU_(\d{4})", os.path.basename(caminho))
    ano_arquivo = int(m.group(1)) if m else 0
    t0 = time.time()
    lidas = gravadas = 0
    lote: list[tuple] = []
    conn.execute("DELETE FROM iptu")
    leitor = csv.DictReader(_abrir_csv(caminho), delimiter=";")
    for linha in leitor:
        lidas += 1
        padrao = linha.get("TIPO DE PADRAO DA CONSTRUCAO") or ""
        if not padrao.startswith(PADRAO_CASA):
            continue
        at = _num(linha.get("AREA DO TERRENO"))
        ac = _num(linha.get("AREA CONSTRUIDA"))
        vt = _num(linha.get("VALOR DO M2 DO TERRENO"))
        vc = _num(linha.get("VALOR DO M2 DE CONSTRUCAO"))
        if at <= 0 or vt <= 0:
            continue
        testada = _num(linha.get("TESTADA PARA CALCULO"))
        tipo = (linha.get("TIPO DE TERRENO") or "Normal").strip()
        esquinas = int(_num(linha.get("QUANTIDADE DE ESQUINAS/FRENTES")))
        fracao = _num(linha.get("FRACAO IDEAL")) or 1.0
        fobs = _num(linha.get("FATOR DE OBSOLESCENCIA")) or 1.0
        vterr, vconst = venal(at, ac, vt, vc, testada, tipo, fracao, fobs, esquinas)
        logradouro = (linha.get("NOME DE LOGRADOURO DO IMOVEL") or "").strip()
        lote.append((
            linha.get("NUMERO DO CONTRIBUINTE"),
            int(_num(linha.get("ANO DO EXERCICIO"))) or ano_arquivo,
            logradouro,
            _numero(linha.get("NUMERO DO IMOVEL")),
            endereco.normalizar_cep(linha.get("CEP DO IMOVEL")),
            endereco.chave_canonica(logradouro),
            at, ac, vt, vc, testada, tipo, esquinas, fracao, fobs,
            int(_num(linha.get("ANO DA CONSTRUCAO CORRIGIDO"))) or None,
            int(_num(linha.get("QUANTIDADE DE PAVIMENTOS"))) or None,
            padrao, linha.get("TIPO DE USO DO IMOVEL"),
            round(vterr, 2), round(vconst, 2),
        ))
        if len(lote) >= 20_000:
            gravadas += _gravar(conn, lote)
            lote = []
            if verbose:
                print(f"  {lidas:>10,d} lidas · {gravadas:>9,d} casas ({time.time()-t0:.0f}s)",
                      flush=True)
    gravadas += _gravar(conn, lote)
    conn.executescript(
        """
        CREATE INDEX IF NOT EXISTS idx_iptu_rua_num ON iptu(rua_chave, numero);
        CREATE INDEX IF NOT EXISTS idx_iptu_cep     ON iptu(cep);
        """
    )
    migracoes.gravar_meta(conn, "iptu_ano", str(ano_arquivo))
    migracoes.gravar_meta(conn, "iptu_ingerido_em", datetime.date.today().isoformat())
    conn.commit()
    if verbose:
        print(f"\nIPTU {ano_arquivo}: {gravadas:,d} casas de {lidas:,d} unidades "
              f"({time.time()-t0:.0f}s)")
    return gravadas


def _gravar(conn: sqlite3.Connection, lote: list[tuple]) -> int:
    if lote:
        conn.executemany(f"INSERT INTO iptu VALUES ({','.join('?' * 21)})", lote)
    return len(lote)


# ---------------------------------------------------------------------------
# Anúncio -> venal
# ---------------------------------------------------------------------------
def _lote_do_anuncio(conn, chave: str, numero: str, area_construida: float | None):
    """O lote do cadastro no mesmo endereço (rua + número).

    Um número pode ter várias unidades (casas de vila, sobrados geminados):
    fica a de área construída mais próxima da anunciada.
    """
    linhas = conn.execute(
        "SELECT * FROM iptu WHERE rua_chave = ? AND numero = ?", (chave, numero)).fetchall()
    if not linhas:
        return None
    if area_construida:
        return min(linhas, key=lambda r: abs((r["area_construida"] or 0) - area_construida))
    return linhas[0]


def _medianas(linhas) -> dict:
    return {
        "v_m2_terreno": statistics.median(r["v_m2_terreno"] for r in linhas),
        "v_m2_construcao": statistics.median(r["v_m2_construcao"] for r in linhas),
        "fator_obsolescencia": statistics.median(r["fator_obsolescencia"] for r in linhas),
        "n_lotes": len(linhas),
    }


def estimar(conn: sqlite3.Connection, anuncio) -> dict | None:
    """Venal do IPTU de um anúncio, do nível mais preciso disponível.

      lote -> o próprio imóvel no cadastro (rua + número): venal oficial
      rua  -> valores unitários medianos das casas da rua x áreas do anúncio
      cep  -> o mesmo, no CEP
    """
    chave = endereco.chave_canonica(anuncio["rua"] or "")
    numero = _numero(endereco.numero_do_logradouro(anuncio["rua"] or ""))
    if chave and numero:
        lote = _lote_do_anuncio(conn, chave, numero, anuncio["area_construida"])
        ac_anuncio = anuncio["area_construida"]
        # Área do cadastro muito diferente da anunciada (mais de 2x para um
        # lado ou outro) = o número do endereço provavelmente está errado.
        # Medido: 51 de 986 lotes casados (5%). Esses caem para a rua.
        if (lote is not None and ac_anuncio and lote["area_construida"]
                and not 0.5 <= lote["area_construida"] / ac_anuncio <= 2):
            lote = None
        if lote is not None:
            return {
                "nivel": "lote", "sql": lote["sql"], "n_lotes": 1,
                "venal_terreno": lote["venal_terreno"],
                "venal_construcao": lote["venal_construcao"],
                "v_m2_terreno": lote["v_m2_terreno"],
                "v_m2_construcao": lote["v_m2_construcao"],
                "fator_obsolescencia": lote["fator_obsolescencia"],
                "area_terreno": lote["area_terreno"],
                "area_construida": lote["area_construida"],
                "ano_construcao": lote["ano_construcao"], "padrao": lote["padrao"],
                "esquina": int(lote["tipo_terreno"] in ("De esquina", "Lote de esquina em ZER")),
            }

    # Sem o lote: as casas PARECIDAS (área construída ±25%) da mesma rua, ou
    # do CEP. Medido em 2026-10-06: 4.734 de 4.793 anúncios NÃO informam a
    # área do terreno, então não dá para aplicar a fórmula às áreas do
    # anúncio — usa-se o venal das casas vizinhas de tamanho semelhante.
    ac = anuncio["area_construida"]
    if not ac:
        return None
    cep = endereco.normalizar_cep(anuncio["cep"] or "")
    for nivel, coluna, param in (("rua", "rua_chave", chave), ("cep", "cep", cep)):
        if not param:
            continue
        linhas = conn.execute(
            f"""SELECT * FROM iptu WHERE {coluna} = ?
                AND area_construida BETWEEN ? AND ?""",
            (param, ac * (1 - TOLERANCIA_AREA), ac * (1 + TOLERANCIA_AREA))).fetchall()
        if len(linhas) < MIN_LOTES:
            continue
        totais = sorted(r["venal_terreno"] + r["venal_construcao"] for r in linhas)
        meio = statistics.median(totais)
        # o lote que dá a mediana empresta a decomposição terreno/construção
        ref = min(linhas, key=lambda r: abs(r["venal_terreno"] + r["venal_construcao"] - meio))
        escala = meio / max(ref["venal_terreno"] + ref["venal_construcao"], 1)
        return {"nivel": nivel, "sql": None,
                "venal_terreno": ref["venal_terreno"] * escala,
                "venal_construcao": ref["venal_construcao"] * escala,
                "area_terreno": statistics.median(r["area_terreno"] for r in linhas),
                "area_construida": statistics.median(r["area_construida"] for r in linhas),
                "ano_construcao": None, "padrao": None, "esquina": 0, **_medianas(linhas)}
    return None


def calcular(conn: sqlite3.Connection, refazer: bool = False, verbose: bool = True) -> int:
    """Grava `venal_iptu` para os anúncios (o que a interface lê)."""
    migracoes.migrar(conn)
    if not conn.execute("SELECT 1 FROM sqlite_master WHERE name='iptu'").fetchone():
        if verbose:
            print("Sem cadastro do IPTU no banco. Rode `caca-iptu --ingerir`.")
        return 0
    ano = int(migracoes.ler_meta(conn, "iptu_ano", "0") or 0)
    if refazer:
        conn.execute("DELETE FROM venal_iptu")
    # refaz quem foi calculado com outro ano do cadastro (ingestão nova)
    conn.execute("DELETE FROM venal_iptu WHERE ano_base IS NOT ?", (ano,))
    conn.commit()
    ja = {r[0] for r in conn.execute("SELECT anuncio_url FROM venal_iptu")}
    anuncios = [a for a in conn.execute(
        "SELECT url, rua, cep, area_terreno, area_construida, preco FROM anuncios")
        if a["url"] not in ja]
    feitos = 0
    contagem: dict[str, int] = {}
    for a in anuncios:
        r = estimar(conn, a)
        if r is None:
            continue
        total = r["venal_terreno"] + r["venal_construcao"]
        conn.execute(
            """INSERT OR REPLACE INTO venal_iptu
               (anuncio_url, nivel, sql, valor_venal, venal_terreno, venal_construcao,
                v_m2_terreno, v_m2_construcao, fator_obsolescencia, area_terreno,
                area_construida, ano_construcao, padrao, esquina, n_lotes, ano_base,
                pct_do_preco, calculado_em)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,datetime('now'))""",
            (a["url"], r["nivel"], r["sql"], round(total, 2), round(r["venal_terreno"], 2),
             round(r["venal_construcao"], 2), r["v_m2_terreno"], r["v_m2_construcao"],
             r["fator_obsolescencia"], r["area_terreno"], r["area_construida"],
             r["ano_construcao"], r["padrao"], r["esquina"], r["n_lotes"], ano,
             round(100 * total / a["preco"], 1) if a["preco"] else None),
        )
        feitos += 1
        contagem[r["nivel"]] = contagem.get(r["nivel"], 0) + 1
    conn.commit()
    if verbose:
        total = conn.execute("SELECT COUNT(*) FROM venal_iptu").fetchone()[0]
        print(f"venal do IPTU {ano}: {feitos:,d} novos ({contagem}); {total:,d} no total")
    return feitos


# ---------------------------------------------------------------------------
# Rotina anual
# ---------------------------------------------------------------------------
def situacao(conn: sqlite3.Connection) -> dict:
    """Que ano está no banco, que arquivos existem, e o que fazer."""
    migracoes.migrar(conn)
    no_banco = int(migracoes.ler_meta(conn, "iptu_ano", "0") or 0)
    locais = arquivos_locais()
    mais_novo = max(locais) if locais else 0
    ano_atual = datetime.date.today().year
    acao = ""
    if mais_novo > no_banco:
        acao = "ingerir"
    elif no_banco < ano_atual:
        acao = "baixar"
    return {"no_banco": no_banco, "arquivos": locais, "mais_novo": mais_novo,
            "ano_atual": ano_atual, "acao": acao}


def instrucoes_download(ano: int) -> str:
    return (f"O IPTU {ano} ainda não está em {PASTA}.\n"
            f"  Baixe no navegador (o site bloqueia download por script):\n"
            f"    {URL_GEOSAMPA.format(ano=ano)}\n"
            f"  ou pelo menu: {CAMINHO_NO_SITE.format(ano=ano)}\n"
            f"  e salve como {os.path.join(PASTA, f'IPTU_{ano}.zip')}.\n"
            f"  A prefeitura costuma publicar o arquivo do ano no 1º trimestre.")


def rotina(conn: sqlite3.Connection, verbose: bool = True) -> str:
    """O que `caca-atualizar --completo` faz com o IPTU, todo mês.

    Ingere se apareceu um arquivo mais novo; se o do ano corrente ainda não
    foi baixado, avisa (sem falhar: o do ano anterior continua valendo).
    """
    s = situacao(conn)
    if s["acao"] == "ingerir":
        ingerir(conn, s["arquivos"][s["mais_novo"]], verbose=verbose)
        n = calcular(conn, verbose=verbose)
        return f"IPTU {s['mais_novo']} ingerido; {n} anúncio(s) com venal"
    if s["acao"] == "baixar":
        aviso = instrucoes_download(s["ano_atual"])
        print(aviso)
        log.warning(aviso)
        base = f"usando o IPTU {s['no_banco']}" if s["no_banco"] else "sem IPTU no banco"
        return f"IPTU {s['ano_atual']} não baixado ({base})"
    return f"IPTU {s['no_banco']} em dia"


def main() -> int:
    ap = argparse.ArgumentParser(description="Valor venal oficial pelo cadastro do IPTU")
    ap.add_argument("--ingerir", action="store_true", help="lê o arquivo do IPTU")
    ap.add_argument("--arquivo", help="caminho do IPTU_<ano>.zip (padrão: o mais novo)")
    ap.add_argument("--calcular", action="store_true", help="venal de cada anúncio")
    ap.add_argument("--refazer", action="store_true", help="recalcula todos os anúncios")
    ap.add_argument("--situacao", action="store_true", help="o que está no banco")
    ap.add_argument("--rotina", action="store_true",
                    help="ingere se houver arquivo novo, senão avisa (usado pelo caca-atualizar)")
    args = ap.parse_args()

    conn = sqlite3.connect(config.DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        if args.rotina:
            print(rotina(conn))
            return 0
        if args.ingerir:
            caminho = args.arquivo
            if not caminho:
                locais = arquivos_locais()
                if not locais:
                    print(instrucoes_download(datetime.date.today().year))
                    return 1
                caminho = locais[max(locais)]
            ingerir(conn, caminho)
            calcular(conn)
        elif args.calcular:
            calcular(conn, refazer=args.refazer)
        else:
            s = situacao(conn)
            print(f"IPTU no banco : {s['no_banco'] or 'nenhum'}")
            print(f"arquivos      : {', '.join(map(str, sorted(s['arquivos']))) or 'nenhum'}")
            if s["acao"] == "baixar":
                print(instrucoes_download(s["ano_atual"]))
            elif s["acao"] == "ingerir":
                print(f"há um arquivo mais novo ({s['mais_novo']}): rode --ingerir")
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
