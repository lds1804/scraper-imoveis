"""Compara o preço PEDIDO nos anúncios com os preços PRATICADOS no ITBI.

A ideia: o ITBI registra o preço real declarado de cada venda em São Paulo.
Se um anúncio pede muito abaixo do que se pratica nas vendas da mesma rua (ou
do mesmo CEP), é candidato a estar barato — ou a ter algum problema que as
fotos e o texto não contam.

CASCATA DE COMPARAÇÃO (do mais preciso para o mais amplo):

    1. mesmo LOGRADOURO + mesmo CEP   -> a mesma via, mesmo trecho
    2. mesmo LOGRADOURO              -> a mesma via (CEP do anúncio pode faltar)
    3. mesmo CEP                      -> a mesma região de CEP (poucas ruas)

Para cada nível, só entram transações de área construída PARECIDA (±25%) —
comparar uma casa de 60 m² com um sobrado de 300 m² não diz nada.

VALORES: o ITBI cobre 2006–2026, então todo valor é corrigido para a data de
referência pelo índice em `indices.py` (FipeZap SP por padrão; ver lá o backtest
que o escolheu sobre o IGP-M). Sem isso, uma venda de 2010 pareceria uma pechincha só por ser
antiga.

JANELA DE 10 ANOS: além de corrigir o valor, a comparação DESCARTA vendas muito
antigas. As duas coisas juntas é que resolvem — corrigir sem limitar a época
ainda mistura 2007 com 2020 na mesma mediana. Ver `JANELA_ANOS` para o caso
real que denunciou o defeito e a medição do viés.

RESSALVA IMPORTANTE que vai no resultado: o índice reajusta preços pela média da
cidade, mas cada imóvel pode valorizar mais ou menos que isso. Então a média serve como
referência "se o imóvel tivesse acompanhado o índice", não como preço de
mercado exato. Por isso o resultado guarda QUANTAS transações foram usadas e
QUAIS foram — para você julgar, não para aceitar um número anônimo.

Uso:
    python comparar_itbi.py --preparar          # cria os índices (1ª vez)
    python comparar_itbi.py --amostra 10        # mostra 10 comparações
    python comparar_itbi.py --abaixo            # só os abaixo da média
    python comparar_itbi.py --detalhar <url>    # a comparação de um anúncio
"""

from __future__ import annotations

import argparse
import re
import sqlite3
import sys
import time

from cacaimoveis import config, endereco, indices, logs, migracoes

log = logs.obter(__name__)

# o console do PowerShell 5.1 usa cp1252 e derruba o script em qualquer caractere
# fora dessa tabela. Melhor degradar com '?' do que perder a execução inteira.
try:
    sys.stdout.reconfigure(errors="replace")
    sys.stderr.reconfigure(errors="replace")
except Exception:  # noqa: BLE001
    pass

# Regra da chave de rua gravada em `rua_chave` (ver `preparar`). Medido em
# 2026-10-06 (experimentos/medir_abreviacoes.py): a v2, que expande as
# abreviações da prefeitura, acha a rua de +58 anúncios sem comparação e não
# perde nenhum casamento por rua.
VERSAO_CHAVE_RUA = "2"

# tolerância de área: casa de 100 m² compara com 75–125 m²
TOLERANCIA_AREA = 0.25
# mínimo de transações para a comparação ser considerada confiável
MIN_TRANSACOES = 3

# ---------------------------------------------------------------------------
# FILTROS DE QUALIDADE DAS TRANSAÇÕES
#
# O ITBI registra TODA transmissão, não só venda de mercado. Sem filtrar, a
# mediana da rua é arrastada para baixo por linhas que não são preço de casa:
#
#   R JOSE ATALIBA ORTIZ 474  138 m²  R$ 4.469  ->  R$ 32/m²   (doação?)
#   R JOSE ATALIBA ORTIZ 764  211 m²  R$ 1.253.461 -> R$ 5.941/m² (normal)
#
# As causas reais, verificadas na base:
#   - natureza '12.Dação em pagamento', '8.Cessião de direitos hereditários',
#     '4.Arrematação (em leilão)', '3.Adjudicação' -> valor simbólico/avaliação
#   - proporcao = 50 / 25 / 0,01 -> transmitiu só uma FRÇÃO do imóvel, mas o
#     valor registrado é o da fração (0,01% aparece 752 vezes!)
#   - erro de digitação / valor venal lançado no campo de transação
#
# Medido na base: os filtros abaixo descartam 82 mil de 330 mil linhas e
# preservam 75% do dado — ou seja, cortam o lixo sem esvaziar a amostra.
# ---------------------------------------------------------------------------

# 10 = RESIDÊNCIA (casa). 12 = cortiço/coletiva e 14 = uso misto têm R$/m²
# incompatível com uma casa e distorceriam a mediana.
USO_RESIDENCIAL = "10"
# só '1.Compra e venda' (exclui doação, herança, permuta, leilão...)
SO_COMPRA_E_VENDA = True
# só transmissão integral do imóvel
PROPORCAO_MINIMA = 99.9
# área construída plausível para casa
AREA_MINIMA = 20.0
# faixa de R$/m² aceitável em São Paulo (corta simbólicos e erros grosseiros)
M2_MINIMO = 800.0
M2_MAXIMO = 25_000.0
# se a rua/CEP tiver mais transações que isso, usa as mais RECENTES
LIMITE_TRANSACOES = 500

# Mínimo de NÚMEROS distintos para agregar por número em vez de por transação.
# Ver a explicação em `_mediana_robusta()`.
MIN_NUMEROS = 3

# ---------------------------------------------------------------------------
# JANELA TEMPORAL — o defeito mais grave que a base tinha
#
# O ITBI cobre 20 anos (2006–2026) e a comparação usava TODAS as vendas da rua
# ao mesmo tempo. Numa rua que vendeu em 2007 e em 2020, a mediana ficava entre
# as duas épocas e nada mais era comparável com o preço de hoje.
#
# CASO REAL que denunciou o defeito (ZAP 2890722139, R Pedro Ferreira de Souza):
#   nº 19, 160 m², 2020, FINANCIADA  -> R$ 860.008 a preços de hoje
#   nº 19, 160 m², 2007, sem fin.    -> R$ 288.277
#   nº 20, 148 m², 2007, sem fin.    -> R$ 256.743
#   mediana de tudo = R$ 288.277 -> anúncio de R$ 900.000 marcado "+212%",
#   quando na verdade estava a +5% (a venda financiada é o valor de mercado).
#
# MEDIDO (1.197 ruas, usando a venda financiada recente como gabarito):
#   sem janela     -> viés 0,84  (a comparação SUBESTIMA ~16% e faz TODO
#                                 anúncio parecer mais caro do que é)
#   janela 10 anos -> viés 0,99  (praticamente sem viés)
#
# O viés não é um erro de caso isolado: 0,84 significa que o erro aparecia em
# favor de "está caro" na base inteira.
# ---------------------------------------------------------------------------
JANELA_ANOS = 10

# Se a janela deixar poucas transações, ela é ESTENDIDA em vez de perder a
# comparação. Medido (1.199 ruas, gabarito = venda financiada recente):
#
#   min  viés  erro   casos que estenderam
#    1    1,04  22,4%   8%   <- melhor equilíbrio
#    2    1,02  22,2%  22%
#    3    1,00  21,9%  38%   <- viés perfeito, mas 38% volta ao defeito
#
# Com min 3 a janela é abandonada em 38% dos casos — ou seja, em quase 4 de
# cada 10 anúncios nada muda. Com min 1 ela vale em 92% e o viés fica em 1,04
# (contra 0,84 sem janela nenhuma).
MIN_DENTRO_JANELA = 1

# ---------------------------------------------------------------------------
# TRANSAÇÕES FINANCIADAS SÃO MAIS CONFIÁVEIS
#
# Numa compra financiada o banco AVALIA o imóvel antes de liberar o dinheiro:
# o valor declarado não pode ser livremente reduzido para pagar menos ITBI,
# senão o financiamento não sai. Numa compra direta (recurso próprio), as
# partes podem declarar abaixo do preço real para reduzir o imposto.
#
# Medido na base (ITBI, uso residencial, compra e venda):
#
#     FINANCIADO          64 mil tx  ->  media R$ 5.927/m2   (avaliado)
#     nao financiado     210 mil tx  ->  media R$ 4.407/m2   (-26%)
#
# Logo, misturar as diretas DERRUBA a mediana e faz todo anúncio parecer caro.
# A cascata abaixo tenta só as financiadas primeiro e só cai para o conjunto
# completo quando não há financiadas suficientes.
#
# CUIDADO COM O ANO: a coluna de financiamento não existe antes de 2011 (0%
# em 2006-2010). Vazio ali significa 'não sei', não 'não financiado' — então
# esses anos entram normalmente, só não conseguem ser priorizados.
PRIMEIRO_ANO_COM_FINANCIAMENTO = 2011


def _clausula_qualidade() -> str:
    """Monta o WHERE com os filtros de qualidade (valores são constantes
    do módulo, por isso podem ser interpolados com segurança)."""
    partes = [
        f"area_construida >= {AREA_MINIMA}",
        "COALESCE(valor_transacao_corrigido,0) > 0",
        ("valor_transacao_corrigido/area_construida "
         f"BETWEEN {M2_MINIMO} AND {M2_MAXIMO}"),
    ]
    if SO_COMPRA_E_VENDA:
        partes.append("natureza LIKE '1.%'")
    if USO_RESIDENCIAL:
        partes.append(f"uso = '{USO_RESIDENCIAL}'")
    if PROPORCAO_MINIMA is not None:
        partes.append(f"proporcao >= {PROPORCAO_MINIMA}")
    return " AND ".join(partes)


# uma transação é 'financiada' quando tem convênio E valor financiado lançado
# (só um dos dois pode ser preenchimento parcial). Ver comentário acima.
_CLAUSULA_FINANCIADO = (
    "(COALESCE(financiamento,'') <> '' OR COALESCE(valor_financiado,0) > 0) "
    f"AND ano_arquivo >= {PRIMEIRO_ANO_COM_FINANCIAMENTO}"
)


_QUALIDADE = _clausula_qualidade()


def descrever_filtros() -> str:
    """Texto legível dos filtros ativos, para ir no relatório.

    Só ASCII: o console do PowerShell usa cp1252 e aborta em caracteres
    como '\u2265' (>=) — descoberto do jeito ruim.
    """
    itens = [f"uso {'RESIDENCIA' if USO_RESIDENCIAL == '10' else USO_RESIDENCIAL}"]
    if SO_COMPRA_E_VENDA:
        itens.append("compra e venda")
    if PROPORCAO_MINIMA is not None:
        itens.append(f"proporcao >= {PROPORCAO_MINIMA:g}%")
    itens.append(f"area >= {AREA_MINIMA:g} m2")
    itens.append(f"R$/m2 entre {M2_MINIMO:,.0f} e {M2_MAXIMO:,.0f}")
    itens.append(f"financiadas primeiro (desde {PRIMEIRO_ANO_COM_FINANCIAMENTO})")
    return " | ".join(itens)


# ---------------------------------------------------------------------------
# Preparação: colunas normalizadas
# ---------------------------------------------------------------------------
def preparar(conn: sqlite3.Connection, verbose: bool = True) -> None:
    """Cria e preenche as colunas normalizadas em `itbi` e `anuncios`."""
    # em `anuncios` as colunas vêm das migrações; em `itbi` (tabela de
    # cálculo, fora das migrações) são acrescentadas aqui
    migracoes.migrar(conn)
    cols = {r[1] for r in conn.execute("PRAGMA table_info(itbi)")}
    for nova in ("rua_norm", "rua_chave", "cep_norm"):
        if nova not in cols:
            conn.execute(f"ALTER TABLE itbi ADD COLUMN {nova} TEXT")
    conn.commit()

    # Versão da chave de rua. Mudou a regra (v2: abreviações da prefeitura
    # expandidas, "R DR JOSE" = "Rua Doutor José")? Então as chaves gravadas
    # estão velhas: zera para o bloco abaixo recalcular tudo, e as
    # comparações feitas com a chave antiga são refeitas.
    if migracoes.ler_meta(conn, "versao_chave_rua", "1") != VERSAO_CHAVE_RUA:
        if verbose:
            print(f"chave de rua mudou para a versão {VERSAO_CHAVE_RUA}: recalculando...")
        conn.execute("UPDATE itbi SET rua_norm = NULL")
        conn.execute("UPDATE anuncios SET rua_norm = NULL")
        conn.execute("DELETE FROM comparacoes")
        conn.execute("DELETE FROM comparacoes_detalhe")
        migracoes.gravar_meta(conn, "versao_chave_rua", VERSAO_CHAVE_RUA)
        conn.commit()

    # ---- ITBI ----
    pend = conn.execute(
        "SELECT COUNT(*) FROM itbi WHERE COALESCE(rua_norm,'')=''"
    ).fetchone()[0]
    if pend and verbose:
        print(f"normalizando {pend:,d} linhas do ITBI...")
    if pend:
        linhas = conn.execute(
            "SELECT rowid, logradouro, cep FROM itbi WHERE COALESCE(rua_norm,'')=''"
        ).fetchall()
        conn.executemany(
            "UPDATE itbi SET rua_norm=?, rua_chave=?, cep_norm=? WHERE rowid=?",
            [
                (endereco.normalizar_logradouro(r[1]),
                 endereco.chave_canonica(r[1]),
                 endereco.normalizar_cep(r[2]),
                 r[0])
                for r in linhas
            ],
        )
        conn.commit()

    # ---- anuncios ----
    pend = conn.execute(
        "SELECT COUNT(*) FROM anuncios WHERE COALESCE(rua_norm,'')='' "
        "AND COALESCE(rua,'')<>''"
    ).fetchone()[0]
    if pend and verbose:
        print(f"normalizando {pend:,d} anúncios...")
    if pend:
        linhas = conn.execute(
            "SELECT url, rua, cep FROM anuncios "
            "WHERE COALESCE(rua_norm,'')='' AND COALESCE(rua,'')<>''"
        ).fetchall()
        conn.executemany(
            "UPDATE anuncios SET rua_norm=?, rua_chave=?, cep_norm=? WHERE url=?",
            [
                (endereco.normalizar_logradouro(r[1]),
                 endereco.chave_canonica(r[1]),
                 endereco.normalizar_cep(r[2]),
                 r[0])
                for r in linhas
            ],
        )
        conn.commit()

    # índices para a comparação não ficar lenta
    conn.executescript(
        """
        CREATE INDEX IF NOT EXISTS idx_itbi_chave   ON itbi(rua_chave);
        CREATE INDEX IF NOT EXISTS idx_itbi_cepn    ON itbi(cep_norm);
        CREATE INDEX IF NOT EXISTS idx_itbi_chave_cep ON itbi(rua_chave, cep_norm);
        CREATE INDEX IF NOT EXISTS idx_anun_chave   ON anuncios(rua_chave);
        CREATE INDEX IF NOT EXISTS idx_anun_cepn    ON anuncios(cep_norm);
        """
    )
    conn.commit()

    if verbose:
        n_itbi = conn.execute("SELECT COUNT(*) FROM itbi").fetchone()[0]
        n_inorm = conn.execute(
            "SELECT COUNT(*) FROM itbi WHERE COALESCE(rua_norm,'')<>''"
        ).fetchone()[0]
        n_anu = conn.execute(
            "SELECT COUNT(*) FROM anuncios WHERE COALESCE(rua,'')<>''"
        ).fetchone()[0]
        n_anorm = conn.execute(
            "SELECT COUNT(*) FROM anuncios WHERE COALESCE(rua_norm,'')<>''"
        ).fetchone()[0]
        print(f"\nITBI    : {n_inorm:,d} de {n_itbi:,d} linhas normalizadas")
        print(f"anúncios: {n_anorm:,d} de {n_anu:,d} com rua")


def criar_tabela_comparacoes(conn: sqlite3.Connection) -> None:
    """`comparacoes` e `comparacoes_detalhe` (o schema mora em `migracoes`)."""
    migracoes.migrar(conn)


# ---------------------------------------------------------------------------
# Comparação
# ---------------------------------------------------------------------------
def _transacoes(conn: sqlite3.Connection, anuncio: sqlite3.Row,
                reaj: indices.Reajustador) -> tuple[list, str, bool]:
    """Acha as transações comparáveis, tentando a cascata.

    São dois passes:

      passe 1 — só transações FINANCIADAS (valor avaliado pelo banco)
      passe 2 — todas (financiadas + diretas), se o passe 1 não der base

    Dentro de cada passe, a cascata geográfica do mais preciso ao mais amplo.

    Devolve (transações, fonte, so_financiado).
    """
    area = anuncio["area_construida"]
    if not area or area <= 0:
        return [], "", False

    lo, hi = area * (1 - TOLERANCIA_AREA), area * (1 + TOLERANCIA_AREA)
    chave = anuncio["rua_chave"] or ""
    cep = anuncio["cep_norm"] or ""

    # (filtro geográfico, parâmetros, nome da fonte) — do mais preciso ao mais amplo
    geograficas = []
    if chave and cep:
        geograficas.append(("rua_chave=? AND cep_norm=?", (chave, cep), "rua+cep"))
    if chave:
        geograficas.append(("rua_chave=?", (chave,), "rua"))
    if cep:
        geograficas.append(("cep_norm=?", (cep,), "cep"))

    # os filtros de qualidade entram em TODOS os níveis da cascata, senão o
    # nível mais amplo (CEP) traria ainda mais lixo
    base = f"""
        SELECT id, logradouro, numero, bairro, cep, data_transacao,
               area_construida, area_terreno, valor_transacao_corrigido,
               financiamento, valor_financiado,
               CASE WHEN {_CLAUSULA_FINANCIADO} THEN 1 ELSE 0 END AS financiado
        FROM itbi
        WHERE {_QUALIDADE}
          AND area_construida BETWEEN ? AND ?
          AND {{filtro}}
          {{extra}}
        ORDER BY data_transacao DESC, id DESC
        LIMIT {LIMITE_TRANSACOES}
    """

    # passe 1 (financiadas) tem prioridade; passe 2 é o fallback
    passes = ((f"AND {_CLAUSULA_FINANCIADO}", True), ("", False))

    # A JANELA é ancorada na venda mais recente encontrada no nível: se a rua
    # só tem venda até 2014, usar "hoje - 10 anos" não acharia nada. O que
    # importa é comparar com a época mais recente que aquela rua tem.

    def _dentro(linhas: list) -> list:
        """Só as transações dentro da janela, ou tudo se a janela esvaziar."""
        if not linhas:
            return linhas
        mais_nova = max(t["data_transacao"] or "00000000" for t in linhas)
        if len(mais_nova) != 8:
            return linhas
        try:
            limite = int(mais_nova[:4]) - JANELA_ANOS
        except ValueError:
            return linhas
        recentes = [t for t in linhas
                    if (t["data_transacao"] or "") >= f"{limite:04d}0000"]
        # poucas transações recentes: estender é melhor que perder a base
        if len(recentes) < MIN_DENTRO_JANELA:
            return linhas
        return recentes

    melhor = ([], "", False)
    for extra, so_fin in passes:
        for filtro, params, fonte in geograficas:
            sql = base.format(filtro=filtro, extra=extra)
            linhas = _dentro(conn.execute(sql, (lo, hi, *params)).fetchall())
            if len(linhas) >= MIN_TRANSACOES:
                return linhas, fonte, so_fin
            if linhas and not melhor[0]:
                melhor = (linhas, fonte, so_fin)

    # nenhum nível atingiu o mínimo: devolve o melhor que houver (para não
    # perder o dado, mas com confiança 'baixa')
    return melhor


def _mediana(valores: list[float]) -> float:
    s = sorted(valores)
    n = len(s)
    if not n:
        return 0.0
    meio = n // 2
    return s[meio] if n % 2 else (s[meio - 1] + s[meio]) / 2


def _mediana_robusta(transacoes: list[sqlite3.Row]) -> tuple[float, int, str]:
    """Mediana de R$/m² resistente a empreendimentos que dominam a rua.

    PROBLEMA REAL encontrado nos dados — Rua Marco Aurélio (Vila Romana):

        11 das 18 transações são no nº 55 (R$ 10.000 a 14.600/m²)
        as outras são casas espalhadas (R$ 3.500 a 8.000/m²)

    O nº 55 não é "o mercado da rua": é um empreendimento cujas unidades
    foram vendidas em série, provavelmente na planta. Como o nº 55 vendeu mais
    que todos os outros imóveis juntos, a mediana simples da rua passa a ser
    um preço de lançamento — e QUALQUER casa na rua parece barata.

    Correções testadas:
      - mediana por transação : 7.567/m²   <- nº 55 domina
      - mediana por imóvel    : 6.262/m²   <- -17% , muito mais plausível

    A segunda funciona assim: tira a mediana DENTRO de cada número (o nº 55
    vira um único valor) e depois a mediana ENTRE os números. Assim cada
    endereço pesa 1, não importa quantas unidades vendeu.

    Devolve (mediana, n_numeros_distintos, metodo).
    """
    por_numero: dict[str, list[float]] = {}
    todos: list[float] = []
    for t in transacoes:
        area = t["area_construida"]
        if not area:
            continue
        m2 = t["valor_transacao_corrigido"] / area
        todos.append(m2)
        numero = (t["numero"] or "?").strip() or "?"
        por_numero.setdefault(numero, []).append(m2)

    if len(por_numero) >= MIN_NUMEROS:
        # uma média por endereço, depois a mediana entre endereços
        med = _mediana([_mediana(v) for v in por_numero.values()])
        return med, len(por_numero), "mediana por numero"

    # poucos endereços distintos: não há como diluir, usa tudo
    return _mediana(todos), len(por_numero), "mediana simples"


def comparar_anuncio(conn: sqlite3.Connection, anuncio: sqlite3.Row,
                     reaj: indices.Reajustador) -> dict | None:
    """Calcula a comparação de um anúncio. None se não houver base."""
    transacoes, fonte, so_financiado = _transacoes(conn, anuncio, reaj)
    if not transacoes:
        return None

    area = anuncio["area_construida"]
    # trabalha em R$/m²: normaliza o tamanho do imóvel antes de comparar
    med, n_numeros, metodo = _mediana_robusta(transacoes)
    if not med:
        return None

    m2s = [t["valor_transacao_corrigido"] / t["area_construida"]
           for t in transacoes if t["area_construida"]]
    pedido = anuncio["preco"]
    razao = (pedido / (med * area)) if (pedido and med) else None

    # quantas das transações usadas eram financiadas (transparência)
    n_fin = sum(1 for t in transacoes if t["financiado"])

    # CONFIABILIDADE, em ordem de importância:
    #   1. as transações são FINANCIADAS? (valor passou por avaliação bancária)
    #   2. quantas transações? (amostra pequena oscila muito)
    #   3. quantos endereços distintos? (1 só endereço = talvez um lançamento)
    #   4. a cascata achou a rua, ou caiu para o CEP inteiro?
    tem_rua = fonte in ("rua+cep", "rua")
    amostra_boa = len(m2s) >= 8 and n_numeros >= 5 and tem_rua

    if so_financiado and len(m2s) >= 5 and n_numeros >= 4 and tem_rua:
        confianca = "alta"
    elif so_financiado or amostra_boa:
        confianca = "media"
    else:
        confianca = "baixa"

    return {
        "anuncio_url": anuncio["url"],
        "fonte": fonte,
        "n_transacoes": len(m2s),
        "n_numeros": n_numeros,
        "n_financiadas": n_fin,
        "so_financiado": so_financiado,
        "metodo": metodo,
        "area_ref": area,
        "mediana": med * area,
        "media": (sum(m2s) / len(m2s)) * area,
        "minimo": min(m2s) * area,
        "maximo": max(m2s) * area,
        "preco_pedido": pedido,
        "razao": razao,
        "preco_m2_medio": med,
        "confianca": confianca,
        # idade das vendas: a interface usa para avisar quando a base é antiga
        # em vez de apresentar preço de 2007 como se fosse de hoje
        "ano_mais_antigo": ano_de(min(t["data_transacao"] for t in transacoes
                                      if t["data_transacao"])),
        "ano_mais_novo": ano_de(max(t["data_transacao"] for t in transacoes
                                    if t["data_transacao"])),
        "transacoes": transacoes,
    }


def ano_de(data: str | None) -> int | None:
    """'20170628' -> 2017. None se a data não servir."""
    if data and len(str(data)) == 8 and str(data).isdigit():
        return int(str(data)[:4])
    return None


def esta_atualizada(conn: sqlite3.Connection, reaj: indices.Reajustador) -> tuple[bool, str]:
    """Diz se as comparações já refletem o índice e a referência atuais.

    Por que existe: a referência se move TODO MÊS (sai IGP-M novo). Sem esta
    checagem não havia como saber se a base estava corrigida para agosto ou
    para janeiro — o número na tela parecia igual. Agora a própria tabela diz
    com que índice e com que mês ela foi feita, e comparar isso com a série
    atual responde "precisa recalcular?" em uma consulta.

    Devolve (atualizada, motivo).
    """
    linhas = conn.execute(
        """SELECT indice_reajuste, data_referencia, data_referencia AS ref,
                  COUNT(*) AS n
           FROM comparacoes
           WHERE data_referencia IS NOT NULL AND data_referencia <> ''
           GROUP BY indice_reajuste, data_referencia
           ORDER BY n DESC""").fetchall()

    if not linhas:
        return False, "nenhuma comparação com registro de reajuste"

    (indice, ref, _, n) = linhas[0]
    if indice != reaj.nome:
        return False, (f"corrigido pelo {indice or '(sem registro)'}, "
                       f"agora o índice é {reaj.nome}")
    if ref != reaj.referencia:
        return False, (f"referência {ref} (o mês mais recente é "
                       f"{reaj.referencia}) — saiu dado novo")
    return True, f"em dia: {reaj.nome} até {ref}"


def atualizar_se_precisar(conn: sqlite3.Connection, reaj: indices.Reajustador,
                          forcar: bool = False,
                          verbose: bool = True) -> int:
    """Recalcula SÓ se o índice mudou ou saiu um mês novo de IGP-M.

    É o gatilho que o usuário pediu ("recalcular tudo quando sair um novo dado
    de igpm"): roda sem custo quase nenhum no caso comum, e faz o trabalho
    completo quando o índice realmente muda.

    Custo medido: os 537 mil valores do ITBI levam ~10 s e as 3.156 comparações
    ~3 s — barato o bastante para rodar a cada saída mensal do índice.
    """
    # garante que a tabela e as colunas novas existam ANTES de consultá-las:
    # sem isso, a checagem quebra numa base que ainda não foi migrada
    criar_tabela_comparacoes(conn)
    ok, motivo = esta_atualizada(conn, reaj)
    if ok and not forcar:
        if verbose:
            print(f"nada a fazer — {motivo}")
        return 0
    if verbose:
        print(f"recalculando: {motivo}")

    n_corrigidos = reingestar_valores(conn, reaj, verbose=verbose)
    n_comp = calcular(conn, reaj, refazer=True, verbose=verbose)
    if verbose:
        print(f"\n{n_corrigidos:,d} valores do ITBI e {n_comp:,d} comparações "
              f"refeitos com o {reaj.nome} até {reaj.referencia}")
    return n_comp


def reingestar_valores(conn: sqlite3.Connection, reaj: indices.Reajustador,
                       verbose: bool = True) -> int:
    """Recorrige os valores do ITBI com o índice em vigor.

    537 mil linhas em ~10 s. O fator é calculado por DATA (só ~250 meses
    distintos), não por linha: sem esse cache seriam 537 mil buscas.
    """
    ref = reaj.referencia
    linhas = conn.execute(
        "SELECT id, valor_transacao, valor_venal, data_transacao FROM itbi"
    ).fetchall()
    cache: dict[str, float] = {}
    lote = []
    gravados = 0
    for rid, v, vv, data in linhas:
        if data not in cache:
            cache[data] = reaj.fator(data) if data else 1.0
        f = cache[data]
        lote.append((round(v * f, 2) if v is not None else None,
                     round(vv * f, 2) if vv is not None else None,
                     round(f, 6), ref, rid))
        if len(lote) >= 50000:
            conn.executemany(
                """UPDATE itbi SET valor_transacao_corrigido=?,
                   valor_venal_corrigido=?, fator_reajuste=?,
                   data_referencia=? WHERE id=?""", lote)
            conn.commit()
            gravados += len(lote)
            lote = []
    if lote:
        conn.executemany(
            """UPDATE itbi SET valor_transacao_corrigido=?,
               valor_venal_corrigido=?, fator_reajuste=?,
               data_referencia=? WHERE id=?""", lote)
        conn.commit()
        gravados += len(lote)
    if verbose:
        print(f"  valores do ITBI recorrigidos: {gravados:,d} "
              f"({len(cache):,d} datas distintas)")
    return gravados


def calcular(conn: sqlite3.Connection, reaj: indices.Reajustador,
             refazer: bool = False, limite: int | None = None,
             verbose: bool = True) -> int:
    """Calcula e grava as comparações de todos os anúncios com rua."""
    criar_tabela_comparacoes(conn)

    if refazer:
        conn.execute("DELETE FROM comparacoes")
        conn.execute("DELETE FROM comparacoes_detalhe")
        conn.commit()

    # já comparado E com o mesmo preço: anúncio que mudou de preço é refeito
    # (antes ficava com a razão calculada sobre o preço antigo para sempre)
    ja = {r[0] for r in conn.execute(
        """SELECT c.anuncio_url FROM comparacoes c
           JOIN anuncios a ON a.url = c.anuncio_url
           WHERE ABS(COALESCE(c.preco_pedido, 0) - COALESCE(a.preco, 0)) < 1""")}
    sql = """SELECT url, preco, area_construida, rua, rua_chave, cep_norm,
                    bairro, titulo, portal
             FROM anuncios
             WHERE area_construida > 0
               AND COALESCE(rua,'') <> ''
               AND COALESCE(rua_chave,'') <> ''"""
    anuncios = [r for r in conn.execute(sql) if r["url"] not in ja]
    if limite:
        anuncios = anuncios[:limite]

    if verbose:
        print(f"anúncios a comparar: {len(anuncios):,d}")

    feitos = sem_base = 0
    t0 = time.time()
    for a in anuncios:
        try:
            res = comparar_anuncio(conn, a, reaj)
        except Exception as e:  # noqa: BLE001
            log.warning("erro tratado, a execução segue: %s", e, exc_info=True)
            if verbose:
                print(f"  [erro] {a['url'][-40:]}: {str(e)[:70]}")
            continue

        # um anúncio refeito (mudou de preço) não pode acumular as vendas da
        # comparação anterior, nem manter uma comparação que deixou de existir
        conn.execute("DELETE FROM comparacoes_detalhe WHERE anuncio_url = ?", (a["url"],))
        if res is None:
            conn.execute("DELETE FROM comparacoes WHERE anuncio_url = ?", (a["url"],))
            sem_base += 1
            continue

        conn.execute(
            """INSERT OR REPLACE INTO comparacoes
               (anuncio_url, fonte, n_transacoes, n_numeros, n_financiadas,
                metodo, area_ref, mediana, media, minimo, maximo, preco_pedido,
                razao, preco_m2_medio, confianca, calculado_em,
                indice_reajuste, data_referencia,
                ano_mais_antigo, ano_mais_novo)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,datetime('now'),?,?,?,?)""",
            (res["anuncio_url"], res["fonte"], res["n_transacoes"],
             res["n_numeros"], res["n_financiadas"], res["metodo"],
             res["area_ref"], res["mediana"], res["media"], res["minimo"],
             res["maximo"], res["preco_pedido"], res["razao"],
             res["preco_m2_medio"], res["confianca"],
             reaj.nome, reaj.referencia,
             res["ano_mais_antigo"], res["ano_mais_novo"]),
        )
        conn.executemany(
            """INSERT INTO comparacoes_detalhe
               (anuncio_url, itbi_id, logradouro, numero, bairro, cep,
                data_transacao, area, area_terreno, valor_corrigido, preco_m2)
               VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
            [
                (res["anuncio_url"], t["id"], t["logradouro"], t["numero"],
                 t["bairro"], t["cep"], t["data_transacao"],
                 t["area_construida"], t["area_terreno"],
                 t["valor_transacao_corrigido"],
                 t["valor_transacao_corrigido"] / t["area_construida"])
                for t in res["transacoes"]
            ],
        )
        feitos += 1
        if feitos % 250 == 0:
            conn.commit()
            if verbose:
                print(f"  {feitos:,d} comparados... ({time.time()-t0:.0f}s)")

    conn.commit()
    if verbose:
        print(f"\ncom comparação : {feitos:,d}")
        print(f"sem base       : {sem_base:,d} (rua/área sem transação parecida)")
        print(f"tempo          : {time.time()-t0:.0f}s")
    return feitos


# ---------------------------------------------------------------------------
# Relatórios
# ---------------------------------------------------------------------------
def por_bairro(conn: sqlite3.Connection, minimo: int = 5) -> None:
    """Resume as comparações por BAIRRO.

    Por que separar: a média geral (razão 1,23) mistura mercados diferentes.
    Medido em 2026-10: bairros centrais (Lapa, Vila Romana, Vila Leopoldina,
    R$/m² 6,2–6,8 mil) pedem praticamente o valor de mercado — 52–56% dos
    anúncios abaixo da mediana. Já os periféricos (Vila Mangalot, Pirituba,
    Barra Funda) pedem 33–53% acima. Um único número esconde isso.

    A coluna 'fin%' importa: bairro com pouca transação financiada tem mediana
    puxada para baixo por subdeclaração, então a razão sai otimista.
    """
    linhas = conn.execute(
        """
        SELECT COALESCE(NULLIF(TRIM(a.bairro),''),'(sem bairro)') AS bairro,
               COUNT(*) AS n,
               AVG(c.razao) AS media,
               SUM(CASE WHEN c.razao < 1 THEN 1 ELSE 0 END) AS abaixo,
               AVG(c.preco_m2_medio) AS m2,
               100.0*SUM(CASE WHEN COALESCE(c.n_financiadas,0) > 0
                              THEN 1 ELSE 0 END)/COUNT(*) AS fin
        FROM comparacoes c JOIN anuncios a ON a.url = c.anuncio_url
        WHERE c.razao IS NOT NULL
        GROUP BY 1 HAVING n >= ? ORDER BY media
        """,
        (minimo,),
    ).fetchall()

    if not linhas:
        print("Nenhuma comparação com esse mínimo de anúncios.")
        return

    print(f"=== por BAIRRO (mínimo {minimo} anúncios) ===")
    print(f"{'bairro':<26} {'n':>5} {'razão':>6} {'abaixo':>7} {'R$/m2':>7} {'fin%':>5}")
    print("-" * 62)
    for r in linhas:
        print(f"{r[0][:24]:<26} {r[1]:>5d} {r[2]:>6.2f} "
              f"{100*r[3]/r[1]:>6.0f}% {r[4]:>7,.0f} {r[5]:>4.0f}%")
    print("\nrazão = preço pedido / mediana do ITBI  ·  abaixo = % de anúncios"
          "\nabaixo da mediana  ·  fin% = % de comparações com base financiada"
          "\n(quanto menor o fin%, mais otimista a razão tende a ser)")


def _limpar_rua(nome: str | None) -> str:
    """Tira o número do fim do logradouro, para exibir.

    O campo `rua` do anúncio às vezes traz o número colado
    (`Rua Teeré, 1051`, `Rua Teeré, 772`). Sem limpar, a mesma rua aparece
    várias vezes no resumo — ver `por_rua()`.
    """
    if not nome:
        return "(sem rua)"
    texto = nome.strip()
    # remove ' , 1234' / ' 1234' / ', 1234A' do final
    texto = re.sub(r"[,\s]+\d{1,5}\s*[A-Za-z]?\s*$", "", texto)
    return texto.strip(" ,-") or nome.strip()


def por_rua(conn: sqlite3.Connection, bairro: str | None = None,
            minimo: int = 5) -> None:
    """Resume as comparações por RUA, opcionalmente dentro de um bairro.

    Agrupa por `rua_chave` (logradouro normalizado, SEM número) e por bairro:
    sem isso a mesma rua virava várias linhas — havia 'Rua Teeré' com os
    números 258, 623, 772 e 1051 todas separadas. Bairro entra na chave porque
    ruas homônimas em bairros diferentes são imóveis diferentes.
    """
    sql = """
        SELECT a.rua_chave, MIN(a.bairro) AS bairro, COUNT(*) AS n,
               AVG(c.razao) AS media,
               SUM(CASE WHEN c.razao < 1 THEN 1 ELSE 0 END) AS abaixo,
               AVG(c.preco_m2_medio) AS m2,
               MIN(a.rua) AS exemplo
        FROM comparacoes c JOIN anuncios a ON a.url = c.anuncio_url
        WHERE c.razao IS NOT NULL AND COALESCE(a.rua_chave,'') <> ''
    """
    params: list = []
    if bairro:
        sql += " AND a.bairro LIKE ?"
        params.append(f"%{bairro}%")
    sql += " GROUP BY a.rua_chave, a.bairro HAVING n >= ? ORDER BY media"
    params.append(minimo)

    linhas = conn.execute(sql, params).fetchall()
    if not linhas:
        print("Nenhuma rua com esse mínimo de anúncios.")
        return

    titulo = f" por RUA em {bairro}" if bairro else " por RUA"
    print(f"==={titulo} (mínimo {minimo} anúncios) ===")
    print(f"{'rua':<34} {'n':>4} {'razão':>6} {'abaixo':>7} {'R$/m2':>7}  bairro")
    print("-" * 78)
    for r in linhas:
        print(f"{_limpar_rua(r['exemplo'])[:32]:<34} {r['n']:>4d} {r['media']:>6.2f} "
              f"{100*r['abaixo']/r['n']:>6.0f}% {r['m2']:>7,.0f}  "
              f"{str(r['bairro'] or '')[:18]}")


def relatorio(conn: sqlite3.Connection, so_abaixo: bool = False,
              limite: int = 20, bairro: str | None = None) -> None:
    sql = """
        SELECT c.*, a.titulo, a.bairro, a.rua, a.portal, a.url
        FROM comparacoes c JOIN anuncios a ON a.url = c.anuncio_url
        WHERE c.razao IS NOT NULL
    """
    params: list = []
    if so_abaixo:
        sql += " AND c.razao < 1"
    if bairro:
        sql += " AND a.bairro LIKE ?"
        params.append(f"%{bairro}%")
    sql += " ORDER BY c.razao ASC LIMIT ?"
    params.append(limite)

    linhas = conn.execute(sql, params).fetchall()
    if not linhas:
        print("Nenhuma comparação. Rode --preparar e depois sem argumento.")
        return

    if bairro:
        print(f"(filtrado para bairros que contenham {bairro!r})\n")
    print(f"{'razão':>6s} {'n':>3s} {'fonte':>8s} {'conf':>6s} "
          f"{'pedido':>12s} {'mediana':>12s}  anúncio")
    print("-" * 112)
    for r in linhas:
        seta = "ABAIXO" if r["razao"] < 1 else "      "
        print(f"{r['razao']:>6.2f} {r['n_transacoes']:>3d} {r['fonte']:>8s} "
              f"{r['confianca']:>6s} {r['preco_pedido'] or 0:>12,.0f} "
              f"{r['mediana']:>12,.0f}  {seta} {(r['titulo'] or '')[:34]}")
        print(f"{'':>6s} {'':>3s} {'':>8s} {'':>6s} {'':>12s} {'':>12s}  "
              f"{r['bairro'][:22]} · {r['rua'][:34]}")


def detalhar(conn: sqlite3.Connection, alvo: str) -> int:
    """Mostra a comparação de um anúncio, com as transações usadas."""
    row = conn.execute(
        """SELECT c.*, a.titulo, a.bairro, a.rua, a.area_construida,
                  a.preco, a.portal, a.url
           FROM comparacoes c JOIN anuncios a ON a.url = c.anuncio_url
           WHERE a.url LIKE ? OR c.anuncio_url LIKE ?""",
        (f"%{alvo}%", f"%{alvo}%"),
    ).fetchone()
    if row is None:
        print(f"Nenhuma comparação para {alvo!r}.")
        print("Dica: rode o comparador primeiro (sem --detalhar).")
        return 1

    print("=" * 78)
    print(f"{row['titulo'][:70]}")
    print("=" * 78)
    print(f"portal      : {row['portal']}")
    print(f"local       : {row['bairro']} · {row['rua']}")
    print(f"área        : {row['area_construida']:.0f} m²")
    print(f"preço pedido: R$ {row['preco']:,.0f}")
    print()
    print(f"base de comparação: {row['n_transacoes']} transações em "
          f"{row['n_numeros']} endereços ({row['fonte']}) · "
          f"confiança {row['confianca']}")
    print(f"método             : {row['metodo']}")
    financiadas = row["n_financiadas"] or 0
    if financiadas:
        print(f"financiadas        : {financiadas} de {row['n_transacoes']} "
              f"({100*financiadas/row['n_transacoes']:.0f}%) "
              f"-> valor avaliado pelo banco")
    else:
        print("financiadas        : nenhuma nesta base "
              "(valores podem estar subdeclarados)")
    print(f"filtros aplicados  : {descrever_filtros()}")
    print(f"R$/m² praticado    : R$ {row['preco_m2_medio']:,.0f}")
    print(f"mediana p/ {row['area_ref']:.0f} m²: R$ {row['mediana']:,.0f}")
    print(f"faixa              : R$ {row['minimo']:,.0f} – R$ {row['maximo']:,.0f}")
    print()
    if row["razao"]:
        pct = (1 - row["razao"]) * 100
        if row["razao"] < 1:
            print(f">>> pedido está {pct:.0f}% ABAIXO da mediana da região")
        else:
            print(f">>> pedido está {abs(pct):.0f}% ACIMA da mediana da região")

    print("\n--- transações usadas (valores corrigidos pelo IPCA) ---")
    print(f"{'data':>10s} {'área':>7s} {'valor':>14s} {'R$/m²':>11s}  endereço")
    print("-" * 92)
    for t in conn.execute(
        """SELECT data_transacao, area, valor_corrigido, preco_m2,
                  logradouro, numero, cep
           FROM comparacoes_detalhe WHERE anuncio_url = ?
           ORDER BY preco_m2""",
        (row["url"],),
    ):
        d = t["data_transacao"] or ""
        data = f"{d[6:8]}/{d[4:6]}/{d[0:4]}" if len(d) == 8 else "?"
        print(f"{data:>10s} {t['area']:>6.0f}m² {t['valor_corrigido']:>14,.0f} "
              f"{t['preco_m2']:>11,.0f}  {t['logradouro'][:26]} "
              f"{t['numero'] or ''} (cep {t['cep']})")
    print("\nRESSALVA: o índice de correção é uma média da cidade (FipeZap SP: preço")
    print("pedido de apartamentos); cada casa pode ter valorizado mais ou menos que")
    print("isso, então trate a mediana como referência, não como preço de mercado.")
    return 0


def main() -> int:
    p = argparse.ArgumentParser(description="Compara anúncios com o ITBI")
    p.add_argument("--preparar", action="store_true",
                   help="cria/preenche as colunas normalizadas e sai")
    p.add_argument("--refazer", action="store_true", help="recalcula tudo")
    p.add_argument("--atualizar", action="store_true",
                   help="recalcula SÓ se o índice mudou ou saiu mês novo "
                        "(para agendar após a publicação do IGP-M)")
    p.add_argument("--indice", choices=indices.INDICES, default=None,
                   help="índice de correção (padrão: configurado em indices.py)")
    p.add_argument("--forcar", action="store_true",
                   help="com --atualizar, refaz mesmo se já estiver em dia")
    p.add_argument("--limite", type=int, default=None, help="máx. de anúncios")
    p.add_argument("--amostra", type=int, default=20, metavar="N",
                   help="quantas comparações listar")
    p.add_argument("--abaixo", action="store_true",
                   help="só os que estão abaixo da mediana")
    p.add_argument("--bairro", metavar="NOME",
                   help="filtra o relatório por bairro (busca parcial)")
    p.add_argument("--por-bairro", action="store_true",
                   help="resume as comparações por bairro")
    p.add_argument("--por-rua", action="store_true",
                   help="resume as comparações por rua")
    p.add_argument("--minimo", type=int, default=5, metavar="N",
                   help="mínimo de anúncios para entrar no resumo (padrão 5)")
    p.add_argument("--detalhar", metavar="URL", help="detalha um anúncio")
    args = p.parse_args()

    conn = sqlite3.connect(config.DB_PATH)
    conn.row_factory = sqlite3.Row

    if args.preparar:
        preparar(conn)
        conn.close()
        return 0

    if args.detalhar:
        rc = detalhar(conn, args.detalhar)
        conn.close()
        return rc

    # os resumos só leem o que já foi calculado — não recalculam nada
    if args.por_bairro or args.por_rua:
        if args.por_bairro:
            por_bairro(conn, minimo=args.minimo)
        if args.por_rua:
            if args.por_bairro:
                print()
            por_rua(conn, bairro=args.bairro, minimo=args.minimo)
        conn.close()
        return 0

    preparar(conn, verbose=False)
    reaj = indices.Reajustador(args.indice or indices.INDICE_PADRAO, verbose=False)
    print(f"referência de reajuste: {reaj.referencia} ({reaj.nome.upper()})")
    print(f"filtros de qualidade  : {descrever_filtros()}\n")

    if args.atualizar:
        # o gatilho mensal: sem custo quando nada mudou, completo quando muda
        atualizar_se_precisar(conn, reaj, forcar=args.forcar)
        conn.close()
        return 0

    calcular(conn, reaj, refazer=args.refazer, limite=args.limite)
    if args.amostra:
        print()
        relatorio(conn, so_abaixo=args.abaixo, limite=args.amostra,
                  bairro=args.bairro)
    conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
