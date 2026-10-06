"""Coleta INCREMENTAL: parar de paginar quando só há anúncio já visto.

POR QUE EXISTE
--------------
A coleta paginava TODAS as páginas de TODOS os bairros, todo dia, e só depois
de baixar cada página perguntava "este anúncio já existe?". Medido em
2026-10-06, numa rodada que achou 360 anúncios novos:

    ZAP          26m52s
    OLX          27m52s
    QuintoAndar   5m11s
    total        59m56s, com 2.073 "já existiam" e 3.122 apartamentos
                 descartados: quase tudo releitura

Com os resultados em ordem de MAIS RECENTES primeiro (`sort=createdAt DESC` no
ZAP, `MOST_RECENT` no QuintoAndar, `sf=1` na OLX), o que é novo vem no começo.
Basta parar quando duas páginas seguidas não trazem nada que ainda não foi
visto — em vez de 20 páginas por bairro, 2 ou 3.

"VISTO", E NÃO "SALVO"
----------------------
O critério não pode ser `db.existe(url)`: anúncio descartado (apartamento, fora
do bairro, acima do preço) nunca entra em `anuncios`, então pareceria "novo"
em toda rodada e a coleta nunca pararia. A tabela `vistos` guarda todo
anúncio que passou pelo coletor, descartado ou não.

Uma URL só é registrada DEPOIS de a página inteira ter sido processada (o
gerador do portal chama `registrar` quando o consumidor volta a pedir o
próximo item). Se a coleta cair no meio de uma página, ela não é dada como
vista e a próxima rodada a refaz.

A PRIMEIRA RODADA depois de ligar isto ainda é completa: a tabela só conhece os
anúncios salvos, e os descartados vão sendo aprendidos. Da segunda em diante
as rodadas são curtas. `--completa` (e o `caca-atualizar --completo` mensal)
força a varredura inteira, que também reaprende o que mudou.
"""

from __future__ import annotations

import sqlite3

# Páginas seguidas sem nenhum anúncio inédito que encerram o bairro. 2 em vez
# de 1: custa uma requisição a mais por bairro e protege de uma página
# "embaralhada" no meio de uma ordenação por data.
PAGINAS_SEM_NOVOS = 2


class Incremental:
    def __init__(self, conn: sqlite3.Connection, portal: str, completa: bool = False,
                 paginas_sem_novos: int = PAGINAS_SEM_NOVOS):
        self.conn = conn
        self.portal = portal
        self.completa = completa
        self.paginas_sem_novos = paginas_sem_novos
        self.sem_novos = 0
        self.bairros = 0          # bairros iniciados
        self.parados = 0          # bairros encerrados antes do fim
        self.paginas = 0          # páginas processadas

    def novo_bairro(self) -> None:
        self.sem_novos = 0
        self.bairros += 1

    def novos(self, urls: list[str]) -> int:
        """Quantas dessas URLs ainda não foram vistas."""
        if not urls:
            return 0
        marcas = ",".join("?" * len(urls))
        vistas = self.conn.execute(
            f"SELECT COUNT(*) FROM vistos WHERE url IN ({marcas})", urls).fetchone()[0]
        return len(set(urls)) - vistas

    def registrar(self, urls: list[str]) -> None:
        self.conn.executemany(
            """INSERT INTO vistos (url, portal, primeira_vez, ultima_vez)
               VALUES (?, ?, datetime('now'), datetime('now'))
               ON CONFLICT(url) DO UPDATE SET ultima_vez = datetime('now')""",
            [(u, self.portal) for u in urls])
        self.conn.commit()

    def pode_parar(self, n_novos: int) -> bool:
        """Chamar depois de cada página; True = encerrar este bairro."""
        self.paginas += 1
        self.sem_novos = 0 if n_novos else self.sem_novos + 1
        if self.completa:
            return False
        if self.sem_novos >= self.paginas_sem_novos:
            self.parados += 1
            return True
        return False

    def resumo(self) -> str:
        if self.completa:
            return f"varredura completa ({self.paginas} páginas)"
        return (f"{self.parados} de {self.bairros} bairros pararam cedo "
                f"({self.paginas} páginas)")
