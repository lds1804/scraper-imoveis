"""Armazenamento local: SQLite para anúncios + download de fotos."""

from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC

from cacaimoveis import config, logs, migracoes
from cacaimoveis.scraper_browser import Anuncio

log = logs.obter(__name__)


class DB:
    """Gerencia o banco SQLite."""

    def __init__(self, path: str = config.DB_PATH, migrar: bool = True):
        """Abre o banco.

        `migrar=False` pula a criação/migração de colunas — usado pelas
        threads de trabalho (a migração já rodou uma vez na conexão
        principal, e rodá-la em paralelo só gera disputa desnecessária).

        O `timeout`/`busy_timeout` de 30s evita "database is locked" quando
        várias threads gravam ao mesmo tempo.
        """
        self.path = path
        self.conn = sqlite3.connect(path, timeout=30)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA busy_timeout = 30000")
        if migrar:
            self._criar_tabelas()

    def _criar_tabelas(self) -> None:
        """Cria/atualiza o schema (ver `migracoes.py`)."""
        migracoes.migrar(self.conn)

    def marcar_duplicatas(self, grupos: list[dict]) -> int:
        """Grava os grupos de duplicatas encontrados pelo pHash.

        `grupos` é uma lista de dicts:
            {"membros": [url, ...], "principal": url, "menor_preco": float}

        Marca todos os membros e escolhe o "principal" (o que serve de
        referência: mais fotos e, em empate, menor preço).

        Além das marcações, grava em `dup_detalhes` (JSON) a lista COMPLETA dos
        anúncios do grupo, cada um com **preço e link**. Isso é o que o usuário
        pediu: agrupar sob um link, mas sem perder quanto cada imobiliária está
        pedindo — a diferença de preço entre cópias do mesmo imóvel é
        informação útil, não ruído.

        Nada é removido.
        """
        # limpa marcações anteriores
        self.conn.execute(
            "UPDATE anuncios SET dup_grupo=NULL, dup_qtd=NULL, "
            "dup_melhor=NULL, dup_n_fotos=NULL, dup_menor_preco=NULL, "
            "dup_detalhes=NULL"
        )

        n = 0
        for idx, g in enumerate(grupos, 1):
            membros = g["membros"]
            principal = g.get("principal") or membros[0]
            menor = g.get("menor_preco")
            n_fotos_principal = self.conn.execute(
                "SELECT COUNT(*) FROM fotos WHERE anuncio_url = ?", (principal,)
            ).fetchone()[0]

            # Ficha de cada anúncio do grupo: preço, link, portal e nº de fotos.
            # Fica em JSON para o grupo inteiro caber numa linha só.
            detalhes = self.detalhes_do_grupo(membros)
            json_detalhes = json.dumps(detalhes, ensure_ascii=False)

            for url in membros:
                self.conn.execute(
                    "UPDATE anuncios SET dup_grupo=?, dup_qtd=?, dup_melhor=?, "
                    "dup_n_fotos=?, dup_menor_preco=?, dup_detalhes=? WHERE url=?",
                    (idx, len(membros), int(url == principal),
                     n_fotos_principal, menor, json_detalhes, url),
                )
                n += 1
        self.conn.commit()
        return n

    def detalhes_do_grupo(self, urls: list[str]) -> list[dict]:
        """Ficha de cada anúncio do grupo (preço, link, portal, nº de fotos).

        Ordenado do menor para o maior preço: assim a primeira linha é sempre
        a oferta mais barata do mesmo imóvel.
        """
        if not urls:
            return []
        marcadores = ",".join("?" * len(urls))
        linhas = self.conn.execute(
            f"""
            SELECT a.url, a.titulo, a.preco, a.portal, a.bairro, a.rua,
                   a.area_construida, a.quartos,
                   (SELECT COUNT(*) FROM fotos f WHERE f.anuncio_url = a.url) n_fotos
            FROM anuncios a
            WHERE a.url IN ({marcadores})
            ORDER BY (a.preco IS NULL), a.preco ASC
            """,
            urls,
        ).fetchall()
        return [
            {
                "url": r["url"],
                "titulo": (r["titulo"] or "")[:120],
                "preco": r["preco"],
                "portal": r["portal"],
                "bairro": r["bairro"],
                "rua": r["rua"],
                "area": r["area_construida"],
                "quartos": r["quartos"],
                "fotos": r["n_fotos"],
            }
            for r in linhas
        ]

    def grupos_publicados(self) -> list[dict]:
        """Grupos de duplicatas com preços e links de cada membro.

        É a visão que a interface usa: um grupo = um imóvel, com a lista de
        quem anuncia e por quanto.
        """
        grupos: dict[int, dict] = {}
        for r in self.conn.execute(
            """
            SELECT url, dup_grupo, dup_melhor, dup_qtd, dup_menor_preco,
                   dup_detalhes, titulo, bairro, preco, portal
            FROM anuncios
            WHERE dup_grupo IS NOT NULL
            ORDER BY dup_grupo, dup_melhor DESC, preco
            """
        ):
            g = grupos.setdefault(
                r["dup_grupo"],
                {
                    "grupo": r["dup_grupo"],
                    "qtd": r["dup_qtd"],
                    "menor_preco": r["dup_menor_preco"],
                    "principal": None,
                    "membros": [],
                    "detalhes": [],
                },
            )
            if r["dup_melhor"]:
                g["principal"] = r["url"]
            g["membros"].append(r["url"])
            if not g["detalhes"] and r["dup_detalhes"]:
                try:
                    g["detalhes"] = json.loads(r["dup_detalhes"])
                except (json.JSONDecodeError, TypeError):
                    g["detalhes"] = []

        lista = sorted(grupos.values(), key=lambda x: x["grupo"])
        for g in lista:
            # faixa de preço do grupo: o quanto a mesma casa varia entre sites
            precos = [d["preco"] for d in g["detalhes"] if d.get("preco")]
            g["preco_min"] = min(precos) if precos else None
            g["preco_max"] = max(precos) if precos else None
            g["economia"] = (
                g["preco_max"] - g["preco_min"] if len(precos) > 1 else None
            )
        return lista

    def duplicatas(self) -> list[dict]:
        """Lista os grupos de duplicatas marcados, com os dados de cada membro."""
        grupos: dict[int, list] = {}
        for r in self.conn.execute(
            """
            SELECT a.url, a.titulo, a.bairro, a.rua, a.preco, a.dup_grupo,
                   a.dup_melhor, a.dup_n_fotos, a.dup_menor_preco,
                   (SELECT COUNT(*) FROM fotos f WHERE f.anuncio_url = a.url) n_fotos
            FROM anuncios a
            WHERE a.dup_grupo IS NOT NULL
            ORDER BY a.dup_grupo, a.dup_melhor DESC, a.preco
            """
        ):
            grupos.setdefault(r["dup_grupo"], []).append(dict(r))
        return [{"grupo": k, "membros": v} for k, v in sorted(grupos.items())]

    def existe(self, url: str) -> bool:
        cur = self.conn.execute("SELECT 1 FROM anuncios WHERE url = ?", (url,))
        return cur.fetchone() is not None

    def salvar_anuncio(self, a: Anuncio) -> None:
        self.conn.execute(
            """
            INSERT OR REPLACE INTO anuncios (
                url, titulo, bairro, endereco, rua, cep, preco,
                area_construida, area_terreno, quartos, banheiros, vagas,
                descricao, portal, fotos_urls, match_quintal, score_quintal,
                aceita_financiamento
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (*a.to_row(), None if a.aceita_financiamento is None else int(a.aceita_financiamento)),
        )
        self.conn.commit()

    def salvar_foto(self, anuncio_url: str, foto_url: str, arquivo_local: str) -> None:
        self.conn.execute(
            "INSERT INTO fotos (anuncio_url, foto_url, arquivo_local) VALUES (?,?,?)",
            (anuncio_url, foto_url, arquivo_local),
        )
        self.conn.commit()

    def salvar_foto_sem_commit(self, anuncio_url: str, foto_url: str,
                               arquivo_local: str) -> None:
        """Insere uma foto SEM commit (para inserir muitas de uma vez).

        `salvar_foto` faz commit a cada chamada. Com uma galeria de 50 fotos
        são 50 commits por anúncio — e como o mesmo imóvel aparece em vários
        portais, isso chega a milhares de commits. Cada commit força um fsync
        e esse custo passa a dominar o tempo de download. Aqui o commit fica
        por conta de `commit()`.
        """
        self.conn.execute(
            "INSERT INTO fotos (anuncio_url, foto_url, arquivo_local) VALUES (?,?,?)",
            (anuncio_url, foto_url, arquivo_local),
        )

    def commit(self) -> None:
        """Grava o que estiver pendente (usado após os inserts em lote)."""
        self.conn.commit()

    def fotos_registradas(self, anuncio_url: str) -> set[str]:
        """URLs de fotos que já estão no banco para este anúncio."""
        return {
            r["foto_url"]
            for r in self.conn.execute(
                "SELECT foto_url FROM fotos WHERE anuncio_url = ?", (anuncio_url,)
            )
        }

    def limpar_fotos(self, anuncio_url: str) -> int:
        """Remove os registros (e os arquivos) das fotos de um anúncio.

        Usado quando a galeria é substituída pela versão completa: os nomes
        antigos (`00.jpg`) podem não corresponder às mesmas imagens, então
        recomeçar evita misturar ordem e conteúdo.
        """
        rows = self.conn.execute(
            "SELECT arquivo_local FROM fotos WHERE anuncio_url = ?", (anuncio_url,)
        ).fetchall()
        for r in rows:
            try:
                if os.path.exists(r["arquivo_local"]):
                    os.remove(r["arquivo_local"])
            except OSError:
                pass
        self.conn.execute("DELETE FROM fotos WHERE anuncio_url = ?", (anuncio_url,))
        self.conn.commit()
        return len(rows)

    def atualizar_detalhe(self, a: Anuncio) -> None:
        """Atualiza um anúncio com os dados enriquecidos da página individual.

        Preserva o preço (esse só existe na listagem). A galeria é substituída
        apenas quando a versão do detalhe é MAIOR que a atual, porque a
        listagem publica só 1 foto de capa enquanto o detalhe traz a galeria
        completa (até 50). Usa COALESCE para preservar valores já existentes
        quando o novo é NULL.
        """
        n_novas = len(a.fotos_urls or [])
        self.conn.execute(
            """
            UPDATE anuncios SET
                titulo          = COALESCE(NULLIF(?, ''), titulo),
                endereco        = COALESCE(NULLIF(?, ''), endereco),
                rua             = COALESCE(NULLIF(?, ''), rua),
                cep             = COALESCE(NULLIF(?, ''), cep),
                area_construida = COALESCE(?, area_construida),
                area_terreno    = COALESCE(?, area_terreno),
                quartos         = COALESCE(?, quartos),
                banheiros       = COALESCE(?, banheiros),
                vagas           = COALESCE(?, vagas),
                descricao       = CASE
                                    WHEN LENGTH(COALESCE(?, '')) > LENGTH(COALESCE(descricao, ''))
                                    THEN ? ELSE descricao
                                  END,
                fotos_urls      = CASE
                                    WHEN ? > (LENGTH(COALESCE(fotos_urls, ''))
                                              - LENGTH(REPLACE(COALESCE(fotos_urls, ''), ',', ''))
                                              + CASE WHEN COALESCE(fotos_urls, '') = '' THEN 0 ELSE 1 END)
                                    THEN ? ELSE fotos_urls
                                  END,
                aceita_financiamento = COALESCE(?, aceita_financiamento),
                detalhe_ok      = 1
            WHERE url = ?
            """,
            (
                a.titulo,
                a.endereco,
                a.rua,
                a.cep,
                a.area_construida,
                a.area_terreno,
                a.quartos,
                a.banheiros,
                a.vagas,
                a.descricao,
                a.descricao,
                n_novas,
                ",".join(a.fotos_urls or []),
                None if a.aceita_financiamento is None else int(a.aceita_financiamento),
                a.url,
            ),
        )
        self.conn.commit()

    def marcar_detalhe(self, url: str) -> None:
        """Marca como visitada mesmo quando nada novo foi extraído."""
        self.conn.execute(
            "UPDATE anuncios SET detalhe_ok = 1 WHERE url = ? AND detalhe_ok = 0",
            (url,),
        )
        self.conn.commit()

    # ------------------------------------------------------------------
    # Análise visual das fotos
    # ------------------------------------------------------------------
    def salvar_analise_visual(self, a) -> None:
        """Grava o resultado da análise visual (objeto `AnaliseFoto`)."""
        from datetime import datetime

        def i(v) -> int | None:
            return None if v is None else int(bool(v))

        self.conn.execute(
            """
            UPDATE anuncios SET
                foto_ok             = ?,
                foto_tem_quintal    = ?,
                foto_piso_quintal   = ?,
                foto_quintal_terra  = ?,
                foto_cimentado      = ?,
                foto_arvores        = ?,
                foto_area_externa   = ?,
                foto_vegetacao      = ?,
                foto_iluminacao     = ?,
                foto_arejamento     = ?,
                foto_cuidado        = ?,
                foto_janelas_grandes= ?,
                foto_reformado      = ?,
                foto_planta_baixa   = ?,
                foto_fachada        = ?,
                foto_piso           = ?,
                foto_comodos        = ?,
                foto_extras         = ?,
                foto_problemas      = ?,
                foto_resumo         = ?,
                foto_confianca      = ?,
                foto_analisada_em   = ?,
                foto_modelo         = ?
            WHERE url = ?
            """,
            (
                1 if a.ok else 0,
                i(a.tem_quintal),
                a.piso_quintal or "",
                i(a.quintal_terra),
                i(a.parece_cimentado),
                i(a.arvores),
                i(a.area_externa),
                a.vegetacao_nota,
                a.iluminacao_nota,
                a.arejamento_nota,
                a.cuidado_nota,
                i(a.janelas_grandes),
                i(a.parece_reformado),
                i(a.tem_planta_baixa),
                a.fachada or "",
                a.piso or "",
                ",".join(a.comodos),
                ",".join(a.extras),
                ",".join(a.problemas),
                a.resumo or "",
                a.confianca or "",
                datetime.now(UTC).isoformat(timespec="seconds"),
                getattr(a, "modelo", "") or None,
                a.url,
            ),
        )
        self.conn.commit()

    def fotos_do_anuncio(self, url: str) -> list[str]:
        """Caminhos locais das fotos de um anúncio, em ordem."""
        return [
            r["arquivo_local"]
            for r in self.conn.execute(
                "SELECT arquivo_local FROM fotos WHERE anuncio_url = ? ORDER BY id",
                (url,),
            )
        ]

    def sem_analise_visual(self, incluir_falhas: bool = False) -> list:
        """Anúncios que ainda não tiveram as fotos analisadas.

        Com `incluir_falhas=True`, reprocessa também os que falharam antes
        (foto_ok = 0), útil depois de corrigir algum problema.
        """
        sql = "SELECT * FROM anuncios WHERE removido_em IS NULL AND foto_ok IS NULL"
        if incluir_falhas:
            sql = ("SELECT * FROM anuncios WHERE removido_em IS NULL "
                   "AND (foto_ok IS NULL OR foto_ok = 0)")
        sql += " ORDER BY rowid"
        return self.conn.execute(sql).fetchall()

    def pendentes_detalhe(self, somente_sem_terreno: bool = True,
                          portal: str | None = None) -> list[sqlite3.Row]:
        """Anúncios que ainda não tiveram a página individual visitada.

        `portal` restringe a um portal. Sem isso a etapa de detalhes
        (que só sabe ler as páginas do Imovelweb) tentaria visitar os ~4.400
        anúncios de OLX, ZAP e QuintoAndar, que já vêm completos da API.
        Anúncios que saíram do ar ficam de fora.
        """
        sql = ("SELECT * FROM anuncios WHERE COALESCE(detalhe_ok, 0) = 0 "
               "AND removido_em IS NULL")
        params: list = []
        if somente_sem_terreno:
            sql += " AND area_terreno IS NULL"
        if portal:
            sql += " AND portal = ?"
            params.append(portal)
        sql += " ORDER BY rowid"
        return self.conn.execute(sql, params).fetchall()

    def total(self) -> int:
        cur = self.conn.execute("SELECT COUNT(*) FROM anuncios")
        return cur.fetchone()[0]

    def por_portal(self) -> dict[str, int]:
        """Quantos anúncios por portal ('imovelweb', 'olx', ...)."""
        return {
            (r["portal"] or "?"): r["n"]
            for r in self.conn.execute(
                "SELECT portal, COUNT(*) n FROM anuncios GROUP BY portal"
            )
        }

    def close(self) -> None:
        self.conn.close()


def _baixar_uma(foto_url: str) -> bytes | None:
    """Baixa o conteúdo de uma foto do CDN (usado pelas threads)."""
    import requests

    r = requests.get(
        foto_url,
        headers={"User-Agent": config.USER_AGENT},
        timeout=config.TIMEOUT,
    )
    if r.status_code == 200 and r.content:
        return r.content
    return None


def _escolher_indices(total: int, limite: int) -> list[int]:
    """Escolhe até `limite` índices ESPALHADOS por uma galeria de `total` fotos.

    Por que não pegar os primeiros N: a ordem típica de um anúncio é fachada,
    sala, cozinha, quartos, banheiro — e só no FIM o quintal, a edícula e a
    área externa. Cortar os primeiros 20 de uma galeria de 135 jogaria fora
    justamente as fotos que a análise visual mais precisa (o quintal é o
    critério central da busca).

    A seleção mantém a PRIMEIRA e a ÚLTIMA foto e distribui o resto em passos
    iguais — o mesmo critério que `visao._amostrar_fotos` usa, para o que foi
    baixado já cobrir toda a extensão da galeria.
    """
    if total <= limite:
        return list(range(total))
    if limite <= 1:
        return [0]

    passo = (total - 1) / (limite - 1)
    escolhidos = sorted({round(i * passo) for i in range(limite)})

    # arredondamento pode repetir ou faltar; a primeira e a última são garantidas
    if escolhidos[0] != 0:
        escolhidos.insert(0, 0)
    if escolhidos[-1] != total - 1:
        escolhidos[-1] = total - 1
    return escolhidos[:limite]


def baixar_fotos(anuncio: Anuncio, request_ctx, db: DB,
                 avisar=None) -> int:
    """Baixa as fotos de um anúncio para a pasta local.

    `request_ctx` pode ser o `context.request` do Playwright (APIRequestContext)
    ou None — nesse caso usa `requests`, que funciona porque o CDN das imagens
    não passa pelo desafio do Cloudflare.

    O download é PARALELO (threads) porque cada foto leva ~0,5-1s de latência
    e um anúncio chega a ter mais de 100 fotos — em série, as galerias
    completas levariam horas. A rede é o gargalo, então as threads ajudam.

    Baixa no máximo `config.FOTOS_MAX_POR_ANUNCIO` fotos (0 = todas): a cauda
    da distribuição é longa e o excedente não é usado por ninguém.

    `avisar(n)` é chamado a cada foto baixada (n = total até agora). Serve para
    quem tem uma barra de progresso mostrar que o trabalho continua mesmo
    quando o anúncio é grande.

    Retorna a quantidade de fotos baixadas.
    """
    if not anuncio.fotos_urls:
        return 0

    pular = db.fotos_registradas(anuncio.url)

    pasta = os.path.join(config.FOTOS_DIR, _slug(anuncio.url))
    os.makedirs(pasta, exist_ok=True)

    # Teto de fotos por anúncio. A cauda da distribuição é longa (há anúncio
    # com 135 fotos) e a análise usa no máximo 12 — o resto é banda e disco
    # gastos à toa. Os índices são ESPALHADOS (não os primeiros N), senão o
    # fim da galeria — onde fica o quintal — seria descartado.
    # Ver `config.FOTOS_MAX_POR_ANUNCIO` e `_escolher_indices`.
    limite = config.FOTOS_MAX_POR_ANUNCIO or len(anuncio.fotos_urls)
    indices = _escolher_indices(len(anuncio.fotos_urls), limite)

    # monta a lista de tarefas (url -> caminho) ignorando o que já existe.
    # O nome do arquivo usa o índice ORIGINAL da galeria, para a ordem das
    # fotos na interface continuar a do anúncio.
    tarefas: list[tuple[str, str]] = []
    for i in indices:
        foto_url = anuncio.fotos_urls[i]
        if foto_url in pular:
            continue
        ext = os.path.splitext(foto_url.split("?")[0])[1] or ".jpg"
        if len(ext) > 5:
            ext = ".jpg"
        tarefas.append((foto_url, os.path.join(pasta, f"{i:02d}{ext}")))

    if not tarefas:
        return 0

    def _trabalho(item: tuple[str, str]):
        foto_url, caminho = item
        try:
            dados = (
                _baixar_bytes(foto_url, request_ctx)
                if request_ctx is not None
                else _baixar_uma(foto_url)
            )
            if not dados:
                return None
            with open(caminho, "wb") as f:
                f.write(dados)
            return (foto_url, caminho)
        except Exception as e:  # noqa: BLE001
            log.warning("erro tratado, a execução segue: %s", e, exc_info=True)
            print(f"  [aviso] falha ao baixar foto {foto_url}: {str(e)[:80]}")
            return None

    baixadas = 0
    workers = max(1, min(config.FOTOS_PARALELO, len(tarefas)))
    if workers == 1:
        for item in tarefas:
            r = _trabalho(item)
            if r:
                db.salvar_foto_sem_commit(anuncio.url, r[0], r[1])
                baixadas += 1
                if avisar:
                    avisar(baixadas)
        db.commit()
        return baixadas

    with ThreadPoolExecutor(max_workers=workers) as pool:
        for r in pool.map(_trabalho, tarefas):
            if r:
                db.salvar_foto_sem_commit(anuncio.url, r[0], r[1])
                baixadas += 1
                if avisar:
                    avisar(baixadas)
    db.commit()

    return baixadas


def _baixar_bytes(url: str, request_ctx=None) -> bytes | None:
    """Baixa o conteúdo de uma URL de imagem."""
    if request_ctx is not None:
        resp = request_ctx.get(url)
        if resp.ok:
            return resp.body()
        return None

    import requests

    r = requests.get(url, headers={"User-Agent": config.USER_AGENT}, timeout=config.TIMEOUT)
    if r.status_code == 200 and r.content:
        return r.content
    return None


def _slug(url: str) -> str:
    """Gera um nome de pasta curto e único a partir da URL."""
    base = re.sub(r"[^a-zA-Z0-9]+", "_", url)[-60:]
    h = hashlib.md5(url.encode()).hexdigest()[:8]
    return f"{base}_{h}"




def baixar_fotos_pendentes(db: DB, limite: int = 0, avisar=None) -> tuple[int, int]:
    """Baixa as fotos dos anúncios que têm a lista de URLs mas nada em disco.

    Por que existe: a coleta baixa as fotos no mesmo passo em que grava o
    anúncio, e uma coleta interrompida deixa anúncios sem foto nenhuma.
    Medido em 2026-10-06: 257 anúncios (237 do QuintoAndar) estavam assim — e
    sem foto não há análise visual, então eles sumiam dos filtros de quintal.

    Usa `requests` direto no CDN (sem navegador). Devolve (anúncios, fotos).

    SAIU DO AR: medido em 2026-10-06, 225 desses anúncios (quase todos do
    QuintoAndar) tinham sido retirados — a página do portal vira
    "indisponível" e o CDN passa a responder 404 para as fotos. Quando a
    primeira foto dá 404, o anúncio é marcado em `removido_em` (a listagem
    deixa de mostrá-lo) em vez de ser tentado a cada rodada.
    """
    import requests

    linhas = db.conn.execute(
        """SELECT url, fotos_urls FROM anuncios a
           WHERE COALESCE(fotos_urls, '') <> '' AND removido_em IS NULL
             AND NOT EXISTS (SELECT 1 FROM fotos f WHERE f.anuncio_url = a.url)
           ORDER BY rowid"""
    ).fetchall()
    if limite:
        linhas = linhas[:limite]
    n_anuncios = n_fotos = 0
    for linha in linhas:
        urls = [u for u in linha["fotos_urls"].split(",") if u.strip()]
        try:
            status = requests.head(urls[0], headers={"User-Agent": config.USER_AGENT},
                                   timeout=config.TIMEOUT, allow_redirects=True).status_code
        except requests.RequestException:
            status = 0     # rede: tenta de novo na próxima rodada, sem concluir nada
        if status in (404, 410):
            db.conn.execute(
                "UPDATE anuncios SET removido_em = datetime('now') WHERE url = ?",
                (linha["url"],))
            db.conn.commit()
            if avisar:
                avisar(linha["url"], -1)
            continue
        baixadas = baixar_fotos(Anuncio(url=linha["url"], fotos_urls=urls), None, db)
        if baixadas:
            n_anuncios += 1
            n_fotos += baixadas
        if avisar:
            avisar(linha["url"], baixadas)
    return n_anuncios, n_fotos
