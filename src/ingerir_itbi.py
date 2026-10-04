"""Ingere a base de ITBI da Prefeitura no SQLite, com reajuste de valores.

O que é: cada linha do ITBI é uma transação imobiliária REAL de São Paulo —
o preço declarado de venda, o valor venal de referência, endereço, área e
padrão do imóvel. É o dado que nenhum portal de anúncio tem.

Por que reajustar: a base cobre 2006–2026. R$ 200 mil em 2008 e R$ 200 mil
hoje são valores de épocas diferentes; comparar direto engana. Todo valor é
trazido para a data de referência pelo IPCA (`indices.py`), e o valor
original fica guardado ao lado do corrigido — nunca se perde o dado bruto.

O que é filtrado por padrão: só imóveis RESIDENCIAIS (descarta salas,
lojas, galpões) e só transações com valor > 0 (arrematação em leilão às
vezes vem zerada). Os filtros são ajustáveis na linha de comando.

Uso:
    python ingerir_itbi.py                       # todos os anos, residencial
    python ingerir_itbi.py --anos 2025 2026      # só alguns
    python ingerir_itbi.py --uf-residencial 0    # inclui não-residencial
    python ingerir_itbi.py --limite 5000         # amostra (para testar)

Cada ano é uma unidade de trabalho: o progresso é gravado por ano, então
interromper e rodar de novo retoma de onde parou sem refazer o que já entrou.
"""

from __future__ import annotations

import argparse
import glob
import os
import re
import sqlite3
import sys
import time
import warnings

import config
import indices

ITBI_DIR = config.caminho("dados", "itbi")

# Índices das colunas na planilha (0-based), conforme o cabeçalho real.
# O nome é a fonte da verdade; se a Prefeitura mudar a ordem, o cabeçalho
# denuncia e o script avisa em vez de gravar dado trocado.
COLUNAS = {
    "sql_cadastro": "Nº do Cadastro (SQL)",
    "logradouro": "Nome do Logradouro",
    "numero": "Número",
    "complemento": "Complemento",
    "bairro": "Bairro",
    "cep": "CEP",
    "natureza": "Natureza de Transação",
    "valor_transacao": "Valor de Transação (declarado pelo contribuinte)",
    "data_transacao": "Data de Transação",
    "valor_venal": "Valor Venal de Referência",
    "proporcao": "Proporção Transmitida (%)",
    "valor_base": "Base de Cálculo adotada",
    "financiamento": "Tipo de Financiamento",
    "valor_financiado": "Valor Financiado",
    "area_terreno": "Área do Terreno (m2)",
    "area_construida": "Área Construída (m2)",
    "uso": "Uso (IPTU)",
    "descricao_uso": "Descrição do uso (IPTU)",
    "padrao": "Padrão (IPTU)",
    "descricao_padrao": "Descrição do padrão (IPTU)",
    "acc": "ACC (IPTU)",
}

# Os arquivos antigos (2006-2013) podem ter cabeçalho com pequenas variações
# de acentuação/grafia. Normalizamos para comparar.
def _norm(texto: str) -> str:
    import unicodedata

    t = unicodedata.normalize("NFKD", str(texto or ""))
    t = "".join(c for c in t if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", t).strip().lower()


def _mapear_colunas(cabecalho: tuple) -> dict[str, int]:
    """Descobre o índice de cada campo pelo NOME no cabeçalho.

    Fixar os índices no código seria frágil: os arquivos de 2006 têm layout
    diferente dos de 2026. Casar por nome (tolerando acento e caixa) faz o
    parser se adaptar. Campo ausente simplesmente não entra.
    """
    mapa: dict[str, int] = {}
    normalizado = {_norm(v): j for j, v in enumerate(cabecalho) if v is not None}

    for campo, titulo in COLUNAS.items():
        alvo = _norm(titulo)
        # casa exato; se não achar, tenta por prefixo (cabeçalhos variam um pouco)
        if alvo in normalizado:
            mapa[campo] = normalizado[alvo]
            continue
        for nome_norm, j in normalizado.items():
            if nome_norm.startswith(alvo[:26]):
                mapa[campo] = j
                break
    return mapa


def _num(v) -> float | None:
    """Converte célula em número (a planilha mistura texto e float)."""
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return float(v)
    texto = str(v).strip().replace("R$", "").replace(" ", "")
    if not texto:
        return None
    # formato BR: 1.234.567,89
    if "," in texto:
        texto = texto.replace(".", "").replace(",", ".")
    try:
        return float(texto)
    except ValueError:
        return None


def _data_iso(v) -> str:
    """Data da planilha -> 'AAAAMMDD' (o índice do IPCA usa esse formato)."""
    if v is None:
        return ""
    if hasattr(v, "strftime"):
        return v.strftime("%Y%m%d")
    texto = str(v).strip()
    m = re.match(r"(\d{4})-(\d{2})-(\d{2})", texto)
    if m:
        return f"{m.group(1)}{m.group(2)}{m.group(3)}"
    m = re.match(r"(\d{2})/(\d{2})/(\d{4})", texto)
    if m:
        return f"{m.group(3)}{m.group(2)}{m.group(1)}"
    return ""


def _texto(v, limite: int = 120) -> str:
    if v is None:
        return ""
    return re.sub(r"\s+", " ", str(v)).strip()[:limite]


# ---------------------------------------------------------------------------
# Banco
# ---------------------------------------------------------------------------
def criar_tabela(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS itbi (
            id                INTEGER PRIMARY KEY AUTOINCREMENT,
            sql_cadastro      TEXT,
            logradouro        TEXT,
            numero            TEXT,
            complemento       TEXT,
            bairro            TEXT,
            cep               TEXT,
            natureza          TEXT,
            -- valores: original e corrigido pelo IPCA para a data de referência
            valor_transacao   REAL,
            valor_transacao_corrigido REAL,
            valor_venal       REAL,
            valor_venal_corrigido     REAL,
            fator_reajuste    REAL,
            data_transacao    TEXT,          -- AAAAMMDD
            data_referencia   TEXT,          -- AAAAMM do reajuste
            proporcao         REAL,
            financiamento     TEXT,
            valor_financiado  REAL,
            area_terreno      REAL,
            area_construida   REAL,
            uso               TEXT,
            descricao_uso     TEXT,
            padrao            TEXT,
            descricao_padrao  TEXT,
            ano_arquivo       INTEGER,
            mes_arquivo       TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_itbi_bairro ON itbi(bairro);
        CREATE INDEX IF NOT EXISTS idx_itbi_logradouro ON itbi(logradouro);
        CREATE INDEX IF NOT EXISTS idx_itbi_data ON itbi(data_transacao);
        CREATE INDEX IF NOT EXISTS idx_itbi_uso ON itbi(descricao_uso);

        -- controle de ingestão: permite retomar sem reprocessar um ano inteiro
        CREATE TABLE IF NOT EXISTS itbi_ingestao (
            arquivo     TEXT PRIMARY KEY,
            linhas      INTEGER,
            gravadas    INTEGER,
            em          TEXT
        );
        """
    )
    conn.commit()


def anos_ja_ingeridos(conn: sqlite3.Connection) -> set[str]:
    return {r[0] for r in conn.execute("SELECT arquivo FROM itbi_ingestao")}


def _e_residencial(descricao_uso: str) -> bool:
    """True se o uso do IPTU indica imóvel residencial."""
    t = _norm(descricao_uso)
    return t.startswith("resid")


# ---------------------------------------------------------------------------
# Ingestão
# ---------------------------------------------------------------------------
def ingerir_arquivo(conn: sqlite3.Connection, caminho: str, reaj: indices.Reajustador,
                    so_residencial: bool = True, valor_min: float = 0.0,
                    limite: int | None = None, verbose: bool = True) -> tuple[int, int]:
    """Lê um .xlsx (todas as abas de mês) e grava. Devolve (lidas, gravadas)."""
    import openpyxl

    warnings.filterwarnings("ignore")
    nome = os.path.basename(caminho)
    m = re.search(r"(\d{4})", nome)
    ano_arquivo = int(m.group(1)) if m else 0

    wb = openpyxl.load_workbook(caminho, read_only=True, data_only=True)
    # abas de dados são as que parecem "JAN-2026"; o resto é legenda/explicação
    abas = [s for s in wb.sheetnames if re.match(r"^[A-Z]{3}-\d{4}$", s, re.I)]
    if not abas:
        abas = [s for s in wb.sheetnames
                if "legenda" not in _norm(s) and "tabela" not in _norm(s)
                and "explica" not in _norm(s)]

    insert = """
        INSERT INTO itbi (
            sql_cadastro, logradouro, numero, complemento, bairro, cep, natureza,
            valor_transacao, valor_transacao_corrigido, valor_venal,
            valor_venal_corrigido, fator_reajuste, data_transacao, data_referencia,
            proporcao, financiamento, valor_financiado, area_terreno,
            area_construida, uso, descricao_uso, padrao, descricao_padrao,
            ano_arquivo, mes_arquivo
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
    """

    lidas = gravadas = 0
    lote: list[tuple] = []

    for aba in abas:
        ws = wb[aba]
        it = ws.iter_rows(values_only=True)

        try:
            cabecalho = next(it)
        except StopIteration:
            continue
        col = _mapear_colunas(cabecalho)
        if "valor_transacao" not in col:
            if verbose:
                print(f"    [aviso] aba {aba!r} sem coluna de valor; pulando")
            continue

        for linha in it:
            lidas += 1
            if limite and lidas > limite:
                break

            g = lambda campo: linha[col[campo]] if campo in col and col[campo] < len(linha) else None

            desc_uso = _texto(g("descricao_uso"), 60)
            if so_residencial and not _e_residencial(desc_uso):
                continue

            valor = _num(g("valor_transacao"))
            if valor is None or valor < valor_min:
                continue

            data = _data_iso(g("data_transacao"))
            fator = reaj.fator(data) if data else 1.0
            venal = _num(g("valor_venal"))

            lote.append((
                _texto(g("sql_cadastro"), 20),
                _texto(g("logradouro"), 90),
                _texto(g("numero"), 12),
                _texto(g("complemento"), 40),
                _texto(g("bairro"), 60),
                _texto(g("cep"), 12),
                _texto(g("natureza"), 70),
                valor,
                round(valor * fator, 2),
                venal,
                round(venal * fator, 2) if venal else None,
                round(fator, 6),
                data,
                reaj.referencia,
                _num(g("proporcao")),
                _texto(g("financiamento"), 40),
                _num(g("valor_financiado")),
                _num(g("area_terreno")),
                _num(g("area_construida")),
                _texto(g("uso"), 8),
                desc_uso,
                _texto(g("padrao"), 8),
                _texto(g("descricao_padrao"), 50),
                ano_arquivo,
                aba,
            ))

            # grava em lotes: um commit por linha seria lentíssimo (são milhões)
            if len(lote) >= 5000:
                conn.executemany(insert, lote)
                conn.commit()
                gravadas += len(lote)
                lote.clear()

        if limite and lidas > limite:
            break

    if lote:
        conn.executemany(insert, lote)
        conn.commit()
        gravadas += len(lote)

    wb.close()
    return lidas, gravadas


def main() -> int:
    p = argparse.ArgumentParser(description="Ingere a base de ITBI no SQLite")
    p.add_argument("--anos", nargs="+", type=int, metavar="ANO",
                   help="só estes anos (padrão: todos os baixados)")
    p.add_argument("--nao-residencial", action="store_true",
                   help="inclui imóveis não residenciais")
    p.add_argument("--valor-min", type=float, default=1000.0,
                   help="ignora transações abaixo deste valor (padrão 1000)")
    p.add_argument("--limite", type=int, default=None,
                   help="máximo de linhas LIDAS por arquivo (para testar)")
    p.add_argument("--refazer", action="store_true",
                   help="reprocessa anos já ingeridos")
    args = p.parse_args()

    arquivos = sorted(glob.glob(os.path.join(ITBI_DIR, "itbi_*.xlsx")))
    if not arquivos:
        print(f"Nenhum arquivo em {ITBI_DIR}.")
        print("Rode: python itbi.py --baixar")
        return 1

    if args.anos:
        arquivos = [a for a in arquivos
                    if any(str(ano) in os.path.basename(a) for ano in args.anos)]
    if not arquivos:
        print("Nenhum arquivo corresponde aos anos pedidos.")
        return 1

    print("Carregando índices de reajuste (IPCA)...")
    reaj = indices.Reajustador(verbose=True)
    print(f"  referência: {reaj.referencia}\n")

    conn = sqlite3.connect(config.DB_PATH)
    criar_tabela(conn)

    feitos = anos_ja_ingeridos(conn)
    if not args.refazer:
        pendentes = [a for a in arquivos if os.path.basename(a) not in feitos]
        if len(pendentes) < len(arquivos):
            print(f"{len(arquivos) - len(pendentes)} arquivo(s) já ingerido(s), pulando.")
        arquivos = pendentes

    if not arquivos:
        print("Nada a fazer. Use --refazer para reprocessar.")
        return _resumo(conn)

    print(f"Ingerindo {len(arquivos)} arquivo(s):")
    print(f"  {'arquivo':>16s} {'lidas':>9s} {'gravadas':>9s} {'tempo':>8s}")
    print("  " + "-" * 48)

    t0 = time.time()
    for caminho in arquivos:
        nome = os.path.basename(caminho)
        t = time.time()
        try:
            lidas, gravadas = ingerir_arquivo(
                conn, caminho, reaj,
                so_residencial=not args.nao_residencial,
                valor_min=args.valor_min,
                limite=args.limite,
            )
        except Exception as e:  # noqa: BLE001
            print(f"  {nome[-16:]:>16s}  ERRO: {str(e)[:60]}")
            continue

        conn.execute(
            "INSERT OR REPLACE INTO itbi_ingestao (arquivo, linhas, gravadas, em) "
            "VALUES (?,?,?,datetime('now'))",
            (nome, lidas, gravadas),
        )
        conn.commit()
        print(f"  {nome[-16:]:>16s} {lidas:>9,d} {gravadas:>9,d} "
              f"{time.time()-t:>7.1f}s")

    print("  " + "-" * 48)
    print(f"  tempo total: {time.time()-t0:.0f}s")
    return _resumo(conn)


def _resumo(conn: sqlite3.Connection) -> int:
    total = conn.execute("SELECT COUNT(*) FROM itbi").fetchone()[0]
    print(f"\n{'=' * 58}")
    print(f"Total de transações na base: {total:,d}")

    if total:
        print(f"\npor bairro (top 10):")
        for b, n in conn.execute(
            "SELECT bairro, COUNT(*) n FROM itbi GROUP BY bairro "
            "ORDER BY n DESC LIMIT 10"
        ):
            print(f"   {b[:40]:42s} {n:>7,d}")

        print(f"\namostra de valores (corrigidos pelo IPCA para hoje):")
        for r in conn.execute(
            "SELECT bairro, area_construida, valor_transacao, "
            "valor_transacao_corrigido, data_transacao, descricao_padrao "
            "FROM itbi ORDER BY RANDOM() LIMIT 5"
        ):
            d = r[4] or ""
            data = f"{d[6:8]}/{d[4:6]}/{d[0:4]}" if len(d) == 8 else "?"
            print(f"   {r[0][:22]:24s} {r[1] or 0:>5.0f}m²  "
                  f"R$ {r[2]:>10,.0f} -> R$ {r[3]:>10,.0f}  ({data})  {r[5][:22]}")

    conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
