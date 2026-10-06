"""Filtros da listagem: o que vem na URL e a consulta SQL que sai dele.

Separado de `webapp.py` para poder ser testado sem Flask e porque é aqui que
moram os defeitos silenciosos — um filtro errado não quebra a página, devolve
a lista errada. Cada armadilha já encontrada está comentada no ponto em que
foi corrigida.
"""

from __future__ import annotations

import sqlite3
import unicodedata
from dataclasses import dataclass, field

from cacaimoveis import config

POR_PAGINA = 60
ORDEM_PADRAO = "encaixe"


def numero_da_url(valor: str, tipo=float):
    """Converte um filtro numérico da URL; valor inválido vira "sem filtro".

    `?preco_max=abc` derrubava a página com erro 500. Ignorar o valor é o
    comportamento esperado de um campo de busca.
    """
    try:
        return tipo(valor.replace(",", ".")) if valor else None
    except (TypeError, ValueError):
        return None


def sem_acento(s: str) -> str:
    """Minúsculas e sem acento, para comparar nomes de bairro entre si.

    Existe porque o `LOWER()` do SQLite não baixa letra acentuada maiúscula —
    comparar acento no banco dá resultado errado. Aqui a comparação é em
    Python, que trata 'Á' -> 'á' corretamente.
    """
    s = unicodedata.normalize("NFKD", str(s).strip().lower())
    return "".join(c for c in s if not unicodedata.combining(c))


def resolver_bairros(conn: sqlite3.Connection, valores: list[str]) -> list[str]:
    """Converte o que veio na URL no nome EXATO guardado na coluna `bairro`.

    Aceita três formas, porque as três aparecem na prática:
      - o nome como está no banco  ("Vila Mangalot", "Água Branca")
      - o slug do config           ("vila-mangalot", "agua-branca")
      - variação de acento/caixa   ("agua branca", "VILA MANGALOT")

    Devolve só o que EXISTE na base: bairro configurado sem anúncio (Jaguara,
    Parque da Lapa, Vila Ipê) não entra no `IN`, senão o filtro não traria
    nada e pareceria quebrado.
    """
    existentes = [r[0] for r in conn.execute(
        "SELECT DISTINCT bairro FROM anuncios WHERE COALESCE(bairro,'')<>''")]
    # índice: "agua branca" -> "Água Branca"
    por_chave = {sem_acento(b): b for b in existentes}
    saida: list[str] = []
    for v in valores:
        # tenta como veio, depois como slug (vira espaço), depois sem acento
        for candidato in (v, str(v).replace("-", " "), sem_acento(v)):
            achado = por_chave.get(sem_acento(candidato))
            if achado and achado not in saida:
                saida.append(achado)
                break
    return saida


def nota_encaixe(a: dict, razao: float | None,
                 confianca: str | None = None) -> float | None:
    """Nota de encaixe: desconto + conservação, medidos nas fotos.

    Por que somar em vez de escolher um: medido em 1.384 anúncios, a
    correlação entre conservação e razão de preço é **+0,032** — ou seja,
    ZERO. São dois eixos independentes: existe imóvel barato e malconservado,
    e imóvel conservado e no preço. Usar um no lugar do outro jogaria
    informação fora.

    Devolve `None` quando não há como avaliar (sem comparação de preço OU sem
    análise visual). Nesse caso o anúncio vai para o fim da ordem: não dá para
    dizer que é um bom encaixe sem ter olhado.

    Faixas (ver `config.ENCAIXE_*`):
      desconto   -> 0 a 0,5 da nota; 50% abaixo do mercado já satura
      conservação-> 0 a 0,5; `foto_cuidado` 1..5 vira 0..1
      problema   -> desconta 0,15 (mofo, infiltração, entulho, obra inacabada)
    """
    if razao is None:
        return None
    cuidado = a.get("foto_cuidado")
    if cuidado is None:
        return None

    # desconto: 0,5 de nota para quem está 50%+ abaixo; acima disso não conta
    # (razão 0,3 costuma ser erro de dado ou imóvel em péssimo estado, não
    # oportunidade — e já está marcado como divergente na conferência de área)
    desc = max(0.0, min(config.ENCAIXE_DESCONTO_MAX, 1.0 - razao))
    # desconto apoiado em poucas vendas vale menos (ver ENCAIXE_PESO_CONFIANCA)
    desc *= config.ENCAIXE_PESO_CONFIANCA.get(confianca or "", 1.0)
    # conservação: 1 -> 0,00 · 2 -> 0,25 · 3 -> 0,50 · 4 -> 0,75 · 5 -> 1,00
    cons = max(0.0, min(1.0, (float(cuidado) - 1) / 4.0))

    nota = (config.ENCAIXE_PESO_DESCONTO * (desc / config.ENCAIXE_DESCONTO_MAX)
            + config.ENCAIXE_PESO_CONSERVACAO * cons)
    if a.get("foto_problemas"):
        nota -= config.ENCAIXE_PENAL_PROBLEMA
    return round(max(0.0, nota), 4)


def registrar_funcoes(conn: sqlite3.Connection) -> None:
    """Expõe `nota_encaixe` ao SQL da conexão.

    A ordem padrão ("encaixe") antes buscava o conjunto filtrado INTEIRO,
    ordenava em Python e só então cortava a página — para 4.793 anúncios,
    todas as linhas a cada clique. Como função do SQLite, a MESMA fórmula
    (uma só, em Python) roda dentro do `ORDER BY`, e o `LIMIT` volta a valer.
    """
    conn.create_function(
        "nota_encaixe", 4,
        lambda razao, cuidado, problemas, confianca: nota_encaixe(
            {"foto_cuidado": cuidado, "foto_problemas": problemas}, razao, confianca),
        deterministic=True,
    )


@dataclass
class Filtros:
    """O estado da busca, lido da URL (a única fonte de verdade dos filtros)."""

    bairro: list[str] = field(default_factory=list)
    piso_quintal: list[str] = field(default_factory=list)
    preco_max: str = ""
    terreno_min: str = ""
    quartos_min: str = ""
    so_quintal: bool = False
    so_financiamento: bool = False
    sem_financiamento: bool = False
    so_arvores: bool = False
    so_abaixo: bool = False
    todas: bool = False
    ordem: str = ORDEM_PADRAO

    @classmethod
    def da_url(cls, args) -> Filtros:
        """`args` é o `request.args` do Flask (um MultiDict)."""
        def lista(nome: str) -> list[str]:
            # MÚLTIPLA escolha: o formulário manda o mesmo nome várias vezes
            # (?bairro=Lapa&bairro=Pirituba) e `getlist` devolve todos. O
            # código antigo usava `get` e ficava só com o PRIMEIRO.
            return [v.strip() for v in args.getlist(nome) if v.strip()]

        def numero(nome: str, tipo=float) -> str:
            # valor que não é número é descartado aqui, antes de chegar ao SQL
            # e aos chips
            v = args.get(nome, "").strip()
            return v if numero_da_url(v, tipo) is not None else ""

        def marcado(nome: str) -> bool:
            return args.get(nome) == "1"

        return cls(
            bairro=lista("bairro"),
            piso_quintal=lista("piso_quintal"),
            preco_max=numero("preco_max"),
            terreno_min=numero("terreno_min"),
            quartos_min=numero("quartos_min", int),
            so_quintal=marcado("so_quintal"),
            so_financiamento=marcado("so_financiamento"),
            sem_financiamento=marcado("sem_financiamento"),
            # depende da análise visual das fotos (IA)
            so_arvores=marcado("so_arvores"),
            # comparação com o preço praticado (ITBI)
            so_abaixo=marcado("so_abaixo"),
            # Mostrar só UMA linha por imóvel. A mesma casa é anunciada por
            # várias imobiliárias (medido: 2.857 anúncios em 1.000 grupos; um
            # sobrado apareceu 25 vezes). `?todas=1` desliga.
            todas=marcado("todas"),
            # ORDEM PADRÃO: "encaixe" — desconto + conservação. O usuário
            # pediu peso para imóvel bem conservado, e na ordem antiga (só
            # desconto) o topo tinha imóvel com mofo/infiltração nas fotos.
            ordem=args.get("ordem", ORDEM_PADRAO),
        )

    def como_dict(self) -> dict:
        return dict(self.__dict__)


def montar_consulta(conn: sqlite3.Connection, f: Filtros, tem_comp: bool,
                    tem_dup: bool) -> tuple[str, list]:
    """SQL (com ORDER BY, sem LIMIT) e parâmetros da listagem filtrada.

    Todas as colunas do WHERE têm o prefixo `a.` e o LEFT JOIN entra sempre
    que a tabela de comparações existe. O LEFT JOIN é necessário (não INNER)
    para que quem NÃO tem comparação continue aparecendo — só vai para o fim.
    """
    razao, confianca = ("c.razao", "c.confianca") if tem_comp else ("NULL", "NULL")
    sql = (f"SELECT a.*, nota_encaixe({razao}, a.foto_cuidado, a.foto_problemas, "
           f"{confianca}) AS _nota FROM anuncios a ")
    if tem_comp:
        sql += "LEFT JOIN comparacoes c ON c.anuncio_url = a.url "
    # anúncio que saiu do ar não está à venda: fica fora da listagem
    sql += "WHERE a.removido_em IS NULL"
    params: list = []

    if f.bairro:
        # Resolve o que veio na URL para o NOME EXATO guardado na coluna.
        #
        # ARMADILHA CORRIGIDA (2026-10-06): antes isto era
        #     AND LOWER(a.bairro) IN (lower(nome), ...)
        # e o `LOWER()` do SQLite **não baixa letra acentuada maiúscula**:
        # "Água Branca" (44 anúncios) e "Jardim Íris" (5) devolviam ZERO.
        nomes = resolver_bairros(conn, f.bairro)
        if nomes:
            sql += f" AND a.bairro IN ({','.join('?' * len(nomes))})"
            params.extend(nomes)
        else:
            # Pediram um bairro que NÃO existe na base. Sem isto o filtro
            # sumia em silêncio e a lista vinha COMPLETA. Zero é a resposta
            # honesta: nada casa com o que foi pedido.
            sql += " AND 1=0"
    if f.preco_max:
        sql += " AND a.preco IS NOT NULL AND a.preco <= ?"
        params.append(numero_da_url(f.preco_max))
    if f.terreno_min:
        sql += " AND a.area_terreno IS NOT NULL AND a.area_terreno >= ?"
        params.append(numero_da_url(f.terreno_min))
    if f.quartos_min:
        sql += " AND a.quartos IS NOT NULL AND a.quartos >= ?"
        params.append(numero_da_url(f.quartos_min, int))
    if f.so_quintal:
        sql += " AND a.match_quintal = 1"
    if f.so_financiamento:
        sql += " AND a.aceita_financiamento = 1"
    if f.sem_financiamento:
        sql += " AND (a.aceita_financiamento IS NULL OR a.aceita_financiamento = 0)"
    if f.piso_quintal:
        # OR entre os pisos escolhidos: marcar "terra" e "grama" significa
        # "tem terra OU é gramado". Um AND devolveria sempre zero.
        #
        # "terra" é especial: quase nenhum quintal é terra PURA (o normal é
        # terra + um canto cimentado, que o modelo classifica como "misto").
        # A pergunta aqui é "tem terra?", não o piso predominante.
        partes = []
        for p in f.piso_quintal:
            if p == "terra":
                partes.append("a.foto_quintal_terra = 1")
            else:
                partes.append("a.foto_piso_quintal = ?")
                params.append(p)
        sql += " AND (" + " OR ".join(partes) + ")"
    if f.so_arvores:
        sql += " AND a.foto_arvores = 1"

    # UMA linha por imóvel: só o principal do grupo de duplicatas. Quem não
    # está em grupo nenhum continua aparecendo. As cópias aparecem na página
    # do anúncio, numa tabela que compara os preços.
    if tem_dup and not f.todas:
        sql += " AND (a.dup_grupo IS NULL OR a.dup_melhor = 1)"

    # "abaixo da mediana do ITBI": razão < 1 = abaixo do preço praticado
    if f.so_abaixo and tem_comp:
        sql += " AND c.razao IS NOT NULL AND c.razao < 1"

    # `c.razao IS NULL` vai para o fim: sem comparação não é "bom negócio",
    # é ausência de informação.
    por_preco = {
        "preco_asc": "a.preco ASC",
        "preco_desc": "a.preco DESC",
        "terreno": "a.area_terreno DESC",
        "recentes": "a.rowid DESC",
    }
    if f.ordem in por_preco:
        ordem_sql = por_preco[f.ordem]
    elif f.ordem == "abaixo" and tem_comp:
        ordem_sql = "c.razao IS NULL, c.razao ASC, a.score_quintal DESC, a.preco ASC"
    elif f.ordem in ("score", "abaixo"):
        ordem_sql = "a.score_quintal DESC, a.preco ASC"
    else:
        # "encaixe" (padrão): sem nota (sem preço comparável ou sem foto
        # analisada) vai para o fim; o desempate é o menor preço
        # (preço 0 conta como "sem preço", como `a["preco"] or 9e12` fazia)
        ordem_sql = "_nota IS NULL, _nota DESC, COALESCE(NULLIF(a.preco, 0), 9e12) ASC"

    # Desempate final: o principal do grupo antes, depois a ordem de entrada.
    # Sem isto dois anúncios empatados mudariam de posição entre as páginas.
    # (No "encaixe" o desempate sempre foi só a ordem de entrada.)
    if ordem_sql.startswith("_nota"):
        sql += f" ORDER BY {ordem_sql}, a.rowid"
    else:
        sql += f" ORDER BY {ordem_sql}, a.dup_melhor DESC, a.rowid"
    return sql, params
