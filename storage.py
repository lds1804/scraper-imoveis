"""Armazenamento local: SQLite para anúncios + download de fotos."""

from __future__ import annotations

import hashlib
import os
import re
import sqlite3
from concurrent.futures import ThreadPoolExecutor

import config
from scraper_browser import Anuncio


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
        self.conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS anuncios (
                url TEXT PRIMARY KEY,
                titulo TEXT,
                bairro TEXT,
                endereco TEXT,
                rua TEXT,
                preco REAL,
                area_construida REAL,
                area_terreno REAL,
                quartos INTEGER,
                banheiros INTEGER,
                vagas INTEGER,
                descricao TEXT,
                portal TEXT,
                fotos_urls TEXT,
                match_quintal INTEGER,
                score_quintal INTEGER,
                aceita_financiamento INTEGER,
                -- --- análise visual das fotos (DeepSeek) ---
                foto_ok INTEGER,
                foto_tem_quintal INTEGER,
                foto_piso_quintal TEXT,
                foto_quintal_terra INTEGER,
                foto_cimentado INTEGER,
                foto_arvores INTEGER,
                foto_area_externa INTEGER,
                foto_vegetacao INTEGER,
                foto_iluminacao INTEGER,
                foto_arejamento INTEGER,
                foto_cuidado INTEGER,
                foto_janelas_grandes INTEGER,
                foto_reformado INTEGER,
                foto_planta_baixa INTEGER,
                foto_fachada TEXT,
                foto_piso TEXT,
                foto_comodos TEXT,
                foto_extras TEXT,
                foto_problemas TEXT,
                foto_resumo TEXT,
                foto_confianca TEXT,
                foto_analisada_em TEXT
            );

            CREATE TABLE IF NOT EXISTS fotos (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                anuncio_url TEXT,
                foto_url TEXT,
                arquivo_local TEXT,
                FOREIGN KEY (anuncio_url) REFERENCES anuncios(url)
            );
            """
        )
        self.conn.commit()
        self._migrar_colunas()

    def _migrar_colunas(self) -> None:
        """Adiciona colunas novas em bancos já existentes (idempotente)."""
        existentes = {
            r["name"] for r in self.conn.execute("PRAGMA table_info(anuncios)")
        }
        # detalhe_ok: 1 = a página individual já foi visitada/parseada
        if "detalhe_ok" not in existentes:
            self.conn.execute(
                "ALTER TABLE anuncios ADD COLUMN detalhe_ok INTEGER DEFAULT 0"
            )
            self.conn.commit()

        # aceita_financiamento: 1 = o anúncio diz aceitar financiamento
        if "aceita_financiamento" not in existentes:
            self.conn.execute(
                "ALTER TABLE anuncios ADD COLUMN aceita_financiamento INTEGER"
            )
            self.conn.commit()

        # rua: logradouro (campo de exibição; `endereco` tem bairro+cidade)
        if "rua" not in existentes:
            self.conn.execute("ALTER TABLE anuncios ADD COLUMN rua TEXT")
            self.conn.commit()

        # Colunas da análise visual (adicionadas em bancos já existentes).
        # Todas podem ficar NULL: NULL = anúncio ainda não analisado.
        colunas_visao = {
            "foto_ok": "INTEGER",
            "foto_tem_quintal": "INTEGER",
            "foto_piso_quintal": "TEXT",
            "foto_quintal_terra": "INTEGER",
            "foto_cimentado": "INTEGER",
            "foto_arvores": "INTEGER",
            "foto_area_externa": "INTEGER",
            "foto_vegetacao": "INTEGER",
            "foto_iluminacao": "INTEGER",
            "foto_arejamento": "INTEGER",
            "foto_cuidado": "INTEGER",
            "foto_janelas_grandes": "INTEGER",
            "foto_reformado": "INTEGER",
            "foto_planta_baixa": "INTEGER",
            "foto_fachada": "TEXT",
            "foto_piso": "TEXT",
            "foto_comodos": "TEXT",
            "foto_extras": "TEXT",
            "foto_problemas": "TEXT",
            "foto_resumo": "TEXT",
            "foto_confianca": "TEXT",
            "foto_analisada_em": "TEXT",
        }
        faltando = [c for c in colunas_visao if c not in existentes]
        for coluna in faltando:
            self.conn.execute(
                f"ALTER TABLE anuncios ADD COLUMN {coluna} {colunas_visao[coluna]}"
            )
        if faltando:
            self.conn.commit()

        # Colunas de agrupamento de duplicatas (pHash). MARCA, não remove:
        # o mesmo imóvel anunciado por duas imobiliárias pode ter preços
        # diferentes — isso é informação útil, não lixo.
        colunas_dup = {
            "dup_grupo": "INTEGER",       # id do grupo (NULL = sem duplicata)
            "dup_qtd": "INTEGER",         # quantos anúncios no grupo
            "dup_melhor": "INTEGER",      # 1 = é o anúncio principal do grupo
            "dup_n_fotos": "INTEGER",     # fotos do melhor (para comparar)
            "dup_menor_preco": "REAL",    # menor preço do grupo
        }
        faltando_dup = [c for c in colunas_dup if c not in existentes]
        for coluna in faltando_dup:
            self.conn.execute(
                f"ALTER TABLE anuncios ADD COLUMN {coluna} {colunas_dup[coluna]}"
            )
        if faltando_dup:
            self.conn.commit()

    def marcar_duplicatas(self, grupos: list[dict]) -> int:
        """Grava os grupos de duplicatas encontrados pelo pHash.

        `grupos` é uma lista de dicts:
            {"membros": [url, ...], "principal": url, "menor_preco": float}

        Marca todos os membros e escolhe o "principal" (o que serve de
        referência: mais fotos e, em empate, menor preço). Nada é removido.
        """
        # limpa marcações anteriores
        self.conn.execute(
            "UPDATE anuncios SET dup_grupo=NULL, dup_qtd=NULL, "
            "dup_melhor=NULL, dup_n_fotos=NULL, dup_menor_preco=NULL"
        )

        n = 0
        for idx, g in enumerate(grupos, 1):
            membros = g["membros"]
            principal = g.get("principal") or membros[0]
            menor = g.get("menor_preco")
            n_fotos_principal = self.conn.execute(
                "SELECT COUNT(*) FROM fotos WHERE anuncio_url = ?", (principal,)
            ).fetchone()[0]

            for url in membros:
                self.conn.execute(
                    "UPDATE anuncios SET dup_grupo=?, dup_qtd=?, dup_melhor=?, "
                    "dup_n_fotos=?, dup_menor_preco=? WHERE url=?",
                    (idx, len(membros), int(url == principal),
                     n_fotos_principal, menor, url),
                )
                n += 1
        self.conn.commit()
        return n

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
                url, titulo, bairro, endereco, rua, preco,
                area_construida, area_terreno, quartos, banheiros, vagas,
                descricao, portal, fotos_urls, match_quintal, score_quintal,
                aceita_financiamento
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
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
        from datetime import datetime, timezone

        def i(v) -> Optional[int]:
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
                foto_analisada_em   = ?
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
                datetime.now(timezone.utc).isoformat(timespec="seconds"),
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
        sql = "SELECT * FROM anuncios WHERE foto_ok IS NULL"
        if incluir_falhas:
            sql = "SELECT * FROM anuncios WHERE foto_ok IS NULL OR foto_ok = 0"
        sql += " ORDER BY rowid"
        return self.conn.execute(sql).fetchall()

    def pendentes_detalhe(self, somente_sem_terreno: bool = True) -> list[sqlite3.Row]:
        """Anúncios que ainda não tiveram a página individual visitada."""
        sql = "SELECT * FROM anuncios WHERE COALESCE(detalhe_ok, 0) = 0"
        if somente_sem_terreno:
            sql += " AND area_terreno IS NULL"
        sql += " ORDER BY rowid"
        return self.conn.execute(sql).fetchall()

    def total(self) -> int:
        cur = self.conn.execute("SELECT COUNT(*) FROM anuncios")
        return cur.fetchone()[0]

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


def baixar_fotos(anuncio: Anuncio, request_ctx, db: DB) -> int:
    """Baixa as fotos de um anúncio para a pasta local.

    `request_ctx` pode ser o `context.request` do Playwright (APIRequestContext)
    ou None — nesse caso usa `requests`, que funciona porque o CDN das imagens
    não passa pelo desafio do Cloudflare.

    O download é PARALELO (threads) porque cada foto leva ~0,5-1s de latência
    e um anúncio tem até 50 fotos — em série, as galerias completas levariam
    horas. A rede é o gargalo, então as threads ajudam bastante.

    Retorna a quantidade de fotos baixadas.
    """
    if not anuncio.fotos_urls:
        return 0

    pular = db.fotos_registradas(anuncio.url)

    pasta = os.path.join(config.FOTOS_DIR, _slug(anuncio.url))
    os.makedirs(pasta, exist_ok=True)

    # monta a lista de tarefas (url -> caminho) ignorando o que já existe
    tarefas: list[tuple[str, str]] = []
    for i, foto_url in enumerate(anuncio.fotos_urls):
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
            print(f"  [aviso] falha ao baixar foto {foto_url}: {e}")
            return None

    baixadas = 0
    workers = max(1, min(config.FOTOS_PARALELO, len(tarefas)))
    if workers == 1:
        for item in tarefas:
            r = _trabalho(item)
            if r:
                db.salvar_foto(anuncio.url, r[0], r[1])
                baixadas += 1
        return baixadas

    with ThreadPoolExecutor(max_workers=workers) as pool:
        for r in pool.map(_trabalho, tarefas):
            if r:
                db.salvar_foto(anuncio.url, r[0], r[1])
                baixadas += 1

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


