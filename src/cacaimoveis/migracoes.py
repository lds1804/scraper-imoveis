"""Schema versionado das tabelas que o SITE lê.

Por que existe: o schema crescia por `ALTER TABLE` espalhados em seis
módulos, cada um conferindo `PRAGMA table_info` por conta própria. Medido em
2026-10-06, isso deixou três buracos que só apareceriam num banco NOVO (o do
deploy, o dos testes):

  - `valores_venais` não era criada por código nenhum — `valor_venal.calcular`
    quebraria no primeiro SELECT;
  - os índices de que a listagem depende (`idx_fotos_anuncio`,
    `idx_anuncios_preco`, `idx_anuncios_bairro`) só existiam no `imoveis.db`
    de trabalho, criados à mão. Sem o primeiro a home leva 213 s;
  - a ordem das colunas dependia de qual script rodou primeiro.

Como funciona: `PRAGMA user_version` guarda quantas migrações o banco já
recebeu, e `migrar()` aplica só as que faltam, na ordem. Cada migração é
escrita para funcionar também num banco antigo (versão 0) que já tem parte
das colunas — é o caso do `imoveis.db` atual.

Para mudar o schema: ACRESCENTE uma função ao fim de `MIGRACOES`. Nunca edite
uma que já rodou, porque os bancos existentes não a executarão de novo.

As tabelas de cálculo que o site não lê (`itbi`, `lotes`, `ref_*`,
`preco_*`, `razao_venal`, `cep_da_rua`) continuam com os seus módulos.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Callable

# Colunas de cada tabela, na ordem canônica. Usado tanto para criar quanto
# para completar uma tabela antiga que não tem todas.
_ANUNCIOS = [
    ("url", "TEXT PRIMARY KEY"),
    ("titulo", "TEXT"),
    ("bairro", "TEXT"),
    ("endereco", "TEXT"),
    ("rua", "TEXT"),
    ("cep", "TEXT"),
    ("preco", "REAL"),
    ("area_construida", "REAL"),
    ("area_terreno", "REAL"),
    ("quartos", "INTEGER"),
    ("banheiros", "INTEGER"),
    ("vagas", "INTEGER"),
    ("descricao", "TEXT"),
    ("portal", "TEXT"),
    ("fotos_urls", "TEXT"),
    ("match_quintal", "INTEGER"),
    ("score_quintal", "INTEGER"),
    # 1 = a página individual já foi visitada/parseada
    ("detalhe_ok", "INTEGER DEFAULT 0"),
    ("aceita_financiamento", "INTEGER"),
    # --- análise visual das fotos (NULL = ainda não analisado) ---
    ("foto_ok", "INTEGER"),
    ("foto_tem_quintal", "INTEGER"),
    ("foto_piso_quintal", "TEXT"),
    ("foto_quintal_terra", "INTEGER"),
    ("foto_cimentado", "INTEGER"),
    ("foto_arvores", "INTEGER"),
    ("foto_area_externa", "INTEGER"),
    ("foto_vegetacao", "INTEGER"),
    ("foto_iluminacao", "INTEGER"),
    ("foto_arejamento", "INTEGER"),
    ("foto_cuidado", "INTEGER"),
    ("foto_janelas_grandes", "INTEGER"),
    ("foto_reformado", "INTEGER"),
    ("foto_planta_baixa", "INTEGER"),
    ("foto_fachada", "TEXT"),
    ("foto_piso", "TEXT"),
    ("foto_comodos", "TEXT"),
    ("foto_extras", "TEXT"),
    ("foto_problemas", "TEXT"),
    ("foto_resumo", "TEXT"),
    ("foto_confianca", "TEXT"),
    ("foto_analisada_em", "TEXT"),
    # --- grupos de duplicatas (pHash). MARCA, não remove ---
    ("dup_grupo", "INTEGER"),        # id do grupo (NULL = sem duplicata)
    ("dup_qtd", "INTEGER"),          # quantos anúncios no grupo
    ("dup_melhor", "INTEGER"),       # 1 = anúncio principal do grupo
    ("dup_n_fotos", "INTEGER"),      # fotos do principal
    ("dup_menor_preco", "REAL"),     # menor preço do grupo
    ("dup_detalhes", "TEXT"),        # JSON: preço e link de cada cópia
    # --- chaves normalizadas (comparar_itbi.preparar) ---
    ("rua_norm", "TEXT"),
    ("rua_chave", "TEXT"),
    ("cep_norm", "TEXT"),
]

_FOTOS = [
    ("id", "INTEGER PRIMARY KEY AUTOINCREMENT"),
    ("anuncio_url", "TEXT REFERENCES anuncios(url)"),
    ("foto_url", "TEXT"),
    ("arquivo_local", "TEXT"),
]

_COMPARACOES = [
    ("anuncio_url", "TEXT PRIMARY KEY"),
    ("fonte", "TEXT"),               # 'rua+cep' | 'rua' | 'cep'
    ("n_transacoes", "INTEGER"),
    ("n_numeros", "INTEGER"),        # endereços distintos
    ("n_financiadas", "INTEGER"),
    ("metodo", "TEXT"),
    ("area_ref", "REAL"),
    ("mediana", "REAL"),
    ("media", "REAL"),
    ("minimo", "REAL"),
    ("maximo", "REAL"),
    ("preco_pedido", "REAL"),
    ("razao", "REAL"),               # pedido / mediana (<1 = abaixo)
    ("preco_m2_medio", "REAL"),
    ("confianca", "TEXT"),           # 'alta' | 'media' | 'baixa'
    ("calculado_em", "TEXT"),
    ("ano_mais_antigo", "INTEGER"),
    ("ano_mais_novo", "INTEGER"),
    ("indice_reajuste", "TEXT"),
    ("data_referencia", "TEXT"),
    ("aviso_base_antiga", "TEXT"),
]

_COMPARACOES_DETALHE = [
    ("anuncio_url", "TEXT"),
    ("itbi_id", "INTEGER"),
    ("logradouro", "TEXT"),
    ("numero", "TEXT"),
    ("bairro", "TEXT"),
    ("cep", "TEXT"),
    ("data_transacao", "TEXT"),
    ("area", "REAL"),
    ("area_terreno", "REAL"),
    ("valor_corrigido", "REAL"),
    ("preco_m2", "REAL"),
]

_AREAS_OFICIAIS = [
    ("anuncio_url", "TEXT PRIMARY KEY"),
    ("nivel", "TEXT"),               # 'lote' | 'rua' | 'bairro'
    ("n_lotes", "INTEGER"),
    ("area_anuncio", "REAL"),
    ("area_oficial", "REAL"),
    ("area_terreno_anuncio", "REAL"),
    ("area_terreno_oficial", "REAL"),
    ("dif_pct", "REAL"),             # (anuncio/oficial - 1) * 100
    ("logradouro", "TEXT"),
    ("numero", "TEXT"),
    ("uso", "TEXT"),
    ("calculado_em", "TEXT"),
    ("area_casa", "TEXT"),           # 'construcao' | 'terreno' | 'ambos'
]

_VALORES_VENAIS = [
    ("anuncio_url", "TEXT PRIMARY KEY"),
    ("valor_venal", "REAL"),
    ("razao", "REAL"),
    ("regiao", "TEXT"),
    ("n_pares", "INTEGER"),
    ("preco", "REAL"),
    ("calculado_em", "TEXT"),
    ("origem_cep", "TEXT"),          # do portal ou deduzido da rua
]


def _garantir_tabela(conn: sqlite3.Connection, nome: str,
                     colunas: list[tuple[str, str]]) -> None:
    """Cria a tabela, ou acrescenta as colunas que faltam numa já existente.

    `ALTER TABLE ADD COLUMN` não aceita PRIMARY KEY nem AUTOINCREMENT; essas
    colunas sempre existem desde a criação, então nunca caem no ALTER.
    """
    existentes = {r[1] for r in conn.execute(f"PRAGMA table_info({nome})")}
    if not existentes:
        corpo = ", ".join(f"{c} {t}" for c, t in colunas)
        conn.execute(f"CREATE TABLE {nome} ({corpo})")
        return
    for coluna, tipo in colunas:
        if coluna not in existentes:
            tipo_alter = tipo.replace(" REFERENCES anuncios(url)", "")
            conn.execute(f"ALTER TABLE {nome} ADD COLUMN {coluna} {tipo_alter}")


def _m001_tabelas(conn: sqlite3.Connection) -> None:
    """Todas as tabelas que o site lê, com todas as colunas de hoje."""
    _garantir_tabela(conn, "anuncios", _ANUNCIOS)
    _garantir_tabela(conn, "fotos", _FOTOS)
    _garantir_tabela(conn, "comparacoes", _COMPARACOES)
    _garantir_tabela(conn, "comparacoes_detalhe", _COMPARACOES_DETALHE)
    _garantir_tabela(conn, "areas_oficiais", _AREAS_OFICIAIS)
    _garantir_tabela(conn, "valores_venais", _VALORES_VENAIS)


def _m002_indices(conn: sqlite3.Connection) -> None:
    """Os índices de que a listagem depende (antes criados à mão)."""
    # `execute` um a um, não `executescript`: este último faz COMMIT antes
    # de rodar e quebraria a transação de `migrar()`
    for nome, tabela, colunas in (
        ("idx_fotos_anuncio", "fotos", "anuncio_url"),
        ("idx_anuncios_preco", "anuncios", "preco"),
        ("idx_anuncios_bairro", "anuncios", "bairro"),
        ("idx_anuncios_dup", "anuncios", "dup_grupo"),
        ("idx_anun_chave", "anuncios", "rua_chave"),
        ("idx_anun_cepn", "anuncios", "cep_norm"),
        ("idx_comp_razao", "comparacoes", "razao"),
        ("idx_compdet_url", "comparacoes_detalhe", "anuncio_url"),
        ("idx_ao_nivel", "areas_oficiais", "nivel"),
        ("idx_ao_dif", "areas_oficiais", "dif_pct"),
    ):
        conn.execute(f"CREATE INDEX IF NOT EXISTS {nome} ON {tabela}({colunas})")


def _m003_modelo_da_visao(conn: sqlite3.Connection) -> None:
    """Qual modelo fez a análise das fotos (agora há dois provedores)."""
    _garantir_tabela(conn, "anuncios", [("foto_modelo", "TEXT")])


# A posição na lista É o número da versão (1, 2, ...). Só acrescentar no fim.
MIGRACOES: list[Callable[[sqlite3.Connection], None]] = [
    _m001_tabelas,
    _m002_indices,
    _m003_modelo_da_visao,
]


def versao(conn: sqlite3.Connection) -> int:
    return conn.execute("PRAGMA user_version").fetchone()[0]


def migrar(conn: sqlite3.Connection) -> int:
    """Aplica as migrações pendentes. Devolve a versão final do banco.

    Cada migração roda numa transação própria junto com a troca de versão:
    se ela falhar no meio, o banco fica na versão anterior, inteiro.
    """
    conn.commit()  # BEGIN explícito falha se houver transação pendente
    atual = versao(conn)
    for numero, migracao in enumerate(MIGRACOES, start=1):
        if numero <= atual:
            continue
        try:
            conn.execute("BEGIN")
            migracao(conn)
            # PRAGMA não aceita parâmetro; `numero` é um int nosso
            conn.execute(f"PRAGMA user_version = {numero}")
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise
    return versao(conn)
