"""Quantos anúncios perdem a comparação com o ITBI por causa de abreviação?

A prefeitura (ITBI e IPTU) escreve "R DR JOSE ELIAS", "R S CAETANO", "R CEL
JOAO"; o anúncio escreve "Rua Doutor José Elias", "Rua São Caetano". A chave
`endereco.chave_rua` não une as duas formas. Este script mede quantos
anúncios SEM comparação passariam a ter rua correspondente no ITBI se as
abreviações fossem expandidas dos dois lados.

Uso: python experimentos/medir_abreviacoes.py
"""

import sqlite3
import sys

sys.path.insert(0, "src")
from cacaimoveis import config, endereco  # noqa: E402

conn = sqlite3.connect(config.DB_PATH)

itbi_antiga = {r[0] for r in conn.execute("SELECT DISTINCT rua_chave FROM itbi")}
itbi_nova = {endereco.chave_canonica(r[0]) for r in conn.execute(
    "SELECT DISTINCT logradouro FROM itbi WHERE COALESCE(logradouro,'')<>''")}

sem = conn.execute(
    """SELECT url, rua, rua_chave FROM anuncios
       WHERE COALESCE(rua,'')<>'' AND area_construida > 0
         AND url NOT IN (SELECT anuncio_url FROM comparacoes)""").fetchall()
com = conn.execute(
    """SELECT url, rua, rua_chave FROM anuncios
       WHERE url IN (SELECT anuncio_url FROM comparacoes)""").fetchall()

antes = sum(1 for _, _, k in sem if k in itbi_antiga)
depois = sum(1 for _, r, _ in sem if endereco.chave_canonica(r) in itbi_nova)
perdidos = sum(1 for _, r, _ in com if endereco.chave_canonica(r) not in itbi_nova)

print(f"anúncios com rua e sem comparação   : {len(sem):,}")
print(f"  rua achada no ITBI (chave antiga) : {antes:,}")
print(f"  rua achada no ITBI (chave nova)   : {depois:,}")
print(f"anúncios COM comparação que a chave nova perderia: {perdidos:,}")
exemplos = [(r, endereco.chave_canonica(r)) for _, r, k in sem
            if k not in itbi_antiga and endereco.chave_canonica(r) in itbi_nova][:12]
for r, k in exemplos:
    print(f"   {r[:45]:47s} -> {k}")
