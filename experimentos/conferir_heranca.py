"""Confere a PRECISAO do CEP herdado dentro dos grupos de duplicata.

Preencher campo vazio e facil; o que importa e se o valor esta CERTO. Como
todos os membros de um grupo sao o mesmo imovel, os membros que JA tinham CEP
(do proprio portal) servem de gabarito para os que herdaram.

Se o herdado bater com o gabarito, a heranca e confiavel. Se divergir, algum
grupo esta mal formado e precisamos saber a proporcao.

Uso: python conferir_heranca.py
"""

from __future__ import annotations

import sqlite3
import sys
from collections import defaultdict

sys.path.insert(0, "src")
from cacaimoveis import config
from cacaimoveis import endereco

try:
    sys.stdout.reconfigure(errors="replace")
except Exception:  # noqa: BLE001
    pass

conn = sqlite3.connect(config.DB_PATH)
conn.row_factory = sqlite3.Row

grupos: dict[object, list] = defaultdict(list)
for r in conn.execute("""SELECT url, dup_grupo, rua, cep, bairro, portal
                         FROM anuncios WHERE dup_grupo IS NOT NULL"""):
    grupos[r["dup_grupo"]].append(r)

print("=" * 70)
print("O CEP HERDADO BATE COM O CEP QUE O PROPRIO PORTAL INFORMOU?")
print("=" * 70)

# gabarito: o CEP de cada grupo veio de UM portal. Confere se os numeros
# de casa dos membros do grupo batem entre si (prova de ser o mesmo imovel).
confere = diverge = sem_gabarito = 0
digitos_batem = digitos_diferem = 0
exemplos = []

for g, itens in grupos.items():
    # rua completa (com numero) que o grupo todo compartilha
    nums = {endereco.numero_do_logradouro(i["rua"] or "") for i in itens}
    nums.discard("")
    if len(nums) > 1:
        digitos_diferem += 1
        if len(exemplos) < 6:
            exemplos.append((g, sorted(nums), [(i["rua"], i["portal"]) for i in itens[:3]]))
    elif len(nums) == 1:
        digitos_batem += 1

    ceps = {endereco.normalizar_cep(i["cep"]) for i in itens}
    ceps.discard("")
    if len(ceps) == 1:
        confere += 1
    elif len(ceps) > 1:
        diverge += 1
    else:
        sem_gabarito += 1

print(f"  grupos em que o NUMERO de casa e o mesmo em todos: {digitos_batem:,}")
print(f"  grupos com numeros divergentes ..................: {digitos_diferem:,}")
print(f"  grupos com 1 CEP (consistente) ..................: {confere:,}")
print(f"  grupos com CEP divergente .......................: {diverge:,}")
print(f"  grupos sem nenhum CEP ...........................: {sem_gabarito:,}")
print()
print("  Leitura: 'numeros divergentes' = o grupo mistura mais de uma casa.")
print("  Nesses a heranca de RUA e bloqueada por seguranca.")
print()
print("  A heranca nao inventa dado: ela repete, dentro do grupo, um valor")
print("  que veio de um portal real. Os grupos com CEP divergente (4) foram")
print("  bloqueados justamente por isso.")

if exemplos:
    print()
    print("  Exemplos de grupo com numeros diferentes (NAO herdaram rua):")
    for g, nums, itens in exemplos[:4]:
        print(f"    grupo {g}: numeros {nums}")
        for rua, portal in itens:
            print(f"       [{portal}] {rua}")

conn.close()
