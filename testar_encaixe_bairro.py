"""Testa a nota de encaixe (conservacao + desconto) e o filtro de bairro.

PARTE 1 — ENCAIXE
  O usuario pediu peso para imovel bem conservado. A nota soma desconto e
  conservacao porque foi MEDIDO que sao independentes (correlacao +0,032).

PARTE 2 — FILTRO DE BAIRRO
  Defeito encontrado: `LOWER()` do SQLite NAO baixa letra acentuada maiuscula
  (`LOWER('Água Branca')` -> `'Água branca'`). Comparar com o nome em
  minusculas vindo do Python nunca casava, entao "Água Branca" (44 anuncios)
  e "Jardim Íris" (5) devolviam ZERO, e os outros bairros vinham menores que o
  real.

Uso: python testar_encaixe_bairro.py
"""

from __future__ import annotations

import os
import re
import sqlite3
import sys

try:
    sys.stdout.reconfigure(errors="replace")
except Exception:  # noqa: BLE001
    pass

_RAIZ = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _RAIZ)
sys.path.insert(0, os.path.join(_RAIZ, "src"))
os.chdir(_RAIZ)

import config  # noqa: E402
import webapp  # noqa: E402

OK = FALHA = 0
falhas: list[str] = []


def checar(nome: str, cond: bool, extra: str = "") -> None:
    global OK, FALHA
    if cond:
        OK += 1
        print(f"  PASSA  {nome}" + (f"   {extra}" if extra else ""))
    else:
        FALHA += 1
        falhas.append(nome)
        print(f"  FALHA  {nome}   {extra}")


def total(html: str) -> int:
    m = re.search(r"<b>([\d.]+)</b>\s*imóve", html)
    return int(m.group(1).replace(".", "")) if m else -1


def get(**kw):
    with webapp.app.test_client() as c:
        r = c.get("/", query_string=kw)
        return r.status_code, r.get_data(as_text=True)


print("=" * 74)
print("1. A NOTA DE ENCAIXE: conservacao e desconto SOMAM")
print("=" * 74)
N = webapp._nota_encaixe
casos = [
    # (cuidado, problema, razao, o que se espera)
    (5, None, 0.50, "MAXIMO: impecavel e 50% abaixo"),
    (1, None, 1.00, "MINIMO: degradado e no preco"),
    (5, None, 1.00, "conservado mas no preco: metade da nota"),
    (1, None, 0.50, "barato mas degradado: metade da nota"),
]
for cuidado, prob, razao, desc in casos:
    a = {"foto_cuidado": cuidado, "foto_problemas": prob}
    print(f"  cuidado={cuidado} problema={prob} razao={razao} -> "
          f"nota={N(a, razao)}   ({desc})")

nota_max = N({"foto_cuidado": 5}, 0.50)
checar("conservado E barato tira a nota maxima", nota_max == 1.0, str(nota_max))
nota_min = N({"foto_cuidado": 1}, 1.00)
checar("degradado E no preco tira zero", nota_min == 0.0, str(nota_min))

# o ponto do pedido: conservacao PESA, entao um conservado no preco deve
# ganhar de um degradado bem mais barato
bom = N({"foto_cuidado": 5}, 0.95)     # 5% abaixo, impecavel
ruim = N({"foto_cuidado": 1}, 0.70)    # 30% abaixo, degradado
print(f"\n  conservado 5% abaixo  (cuidado 5): {bom}")
print(f"  degradado 30% abaixo  (cuidado 1): {ruim}")
checar("conservado ganha de degradado bem mais barato", bom > ruim)

print()
print("  a penalidade de problema visivel:")
sem = N({"foto_cuidado": 4}, 0.85)
com = N({"foto_cuidado": 4, "foto_problemas": "mofo,infiltracao"}, 0.85)
print(f"     sem problema: {sem}   com problema: {com}")
checar("problema visivel desconta",
       com == round(sem - config.ENCAIXE_PENAL_PROBLEMA, 4), f"{sem} -> {com}")
checar("a nota nunca fica negativa",
       N({"foto_cuidado": 1, "foto_problemas": "mofo"}, 1.0) >= 0.0)

print()
print("  sem dado NAO inventa nota:")
checar("sem analise de foto -> None",
       N({"foto_cuidado": None}, 0.9) is None)
checar("sem comparacao de preco -> None",
       N({"foto_cuidado": 5}, None) is None)

print()
print("=" * 74)
print("2. A ORDEM 'encaixe' e' o PADRAO e ordena de verdade")
print("=" * 74)
code, home = get()
checar("a home abre", code == 200, f"HTTP {code}")
m = re.search(r'<option value="encaixe"\s+selected>', home)
checar("'Melhor encaixe' vem marcado por padrao", m is not None)

# extrai a nota do primeiro card e confere que e' a maior
def notas(html):
    return [int(x) for x in re.findall(
        r'encaixe-texto">\s*encaixe (\d+)', html)]

ns = notas(home)
checar("os cards mostram a nota", len(ns) > 0, f"{len(ns)} notas")
if ns:
    checar("a pagina 1 vem da MAIOR para a menor",
           all(ns[i] >= ns[i + 1] for i in range(len(ns) - 1)),
           f"topo={ns[:5]} fim={ns[-3:]}")
    checar("a melhor nota da primeira pagina e' alta",
           ns[0] >= 70, f"{ns[0]}")

# a ordem antiga (so desconto) tem de dar resultado DIFERENTE
_, abaixo = get(ordem="abaixo")
na = notas(abaixo)
checar("a ordem antiga 'abaixo' existe e difere",
       na != ns, f"encaixe={ns[:3]} vs abaixo={na[:3]}")

print()
print("  o bug que motivou o pedido: na ordem antiga o topo tinha problema")
com_prob_encaixe = len(re.findall(r'encaixe-problema', home))
com_prob_abaixo = len(re.findall(r'encaixe-problema', abaixo))
print(f"     cards com problema visivel — encaixe: {com_prob_encaixe}  "
      f"abaixo: {com_prob_abaixo}")
checar("a ordem 'encaixe' traz menos problema no topo",
       com_prob_encaixe <= com_prob_abaixo,
       f"{com_prob_encaixe} <= {com_prob_abaixo}")

print()
print("=" * 74)
print("3. FILTRO DE BAIRRO: o LOWER() do SQLite nao serve para acento")
print("=" * 74)
# o defeito em si, documentado como teste
conn = sqlite3.connect(config.DB_PATH)
no_sqlite = list(conn.execute("SELECT LOWER('Água Branca')"))[0][0]
em_python = "Água Branca".lower()
checar("o LOWER() do SQLite NAO baixa acento (a causa do bug)",
       no_sqlite != em_python, f"sqlite={no_sqlite!r} python={em_python!r}")
checar("a comparacao em Python funciona",
       webapp._sem_acento("Água Branca") == webapp._sem_acento("agua branca"))

conn.row_factory = sqlite3.Row
bairros = [r[0] for r in conn.execute(
    "SELECT DISTINCT bairro FROM anuncios ORDER BY bairro")]
conn.close()

print()
print(f"  {'bairro':<24} {'no banco':>9} {'filtro':>8}  ok?")
ruins = []
for b in bairros:
    conn2 = sqlite3.connect(config.DB_PATH)
    real = list(conn2.execute(
        "SELECT COUNT(*) FROM anuncios WHERE bairro=?", (b,)))[0][0]
    conn2.close()
    _, h = get(bairro=b, todas="1")
    got = total(h)
    if got != real:
        ruins.append((b, real, got))
    print(f"  {b:<24} {real:>9} {got:>8}  {'ok' if got == real else '*** BUG'}")

checar("TODOS os bairros do filtro devolvem o que prometem",
       len(ruins) == 0, f"{len(ruins)} quebrados: {ruins[:3]}")

print()
print("  as variacoes de escrita do mesmo bairro:")
variacoes = ["Água Branca", "agua-branca", "agua branca", "AGUA BRANCA",
             "ÁGUA BRANCA"]
for v in variacoes:
    _, h = get(bairro=v, todas="1")
    got = total(h)
    checar(f"{v!r} -> 44", got == 44, f"obtido {got}")

# valor que NAO existe tem de devolver ZERO, nao a lista inteira. Sem isto o
# filtro é ignorado em silêncio e o usuário vê 2.938 anúncios achando que o
# bairro não tem nada ou que o filtro quebrou.
for v in ("agua", "Bairro Que Nao Existe", "xxxxx"):
    _, h = get(bairro=v, todas="1")
    got = total(h)
    checar(f"{v!r} (inexistente) -> 0, nao a lista inteira", got == 0,
           f"obtido {got}")

print()
print("  multi-bairro (o caso do getlist):")
with webapp.app.test_client() as c:
    h = c.get("/", query_string=[("bairro", "Lapa"), ("bairro", "Água Branca"),
                                 ("todas", "1")]).get_data(as_text=True)
checar("Lapa + Água Branca = 409", total(h) == 409, f"{total(h)}")

with webapp.app.test_client() as c:
    h = c.get("/", query_string=[("bairro", "Lapa"), ("bairro", "Lapa"),
                                 ("todas", "1")]).get_data(as_text=True)
checar("o mesmo bairro duas vezes nao soma", total(h) == 365, f"{total(h)}")

_, h = get(bairro="Jaguara")
checar("bairro configurado SEM anuncio devolve 0 (nao quebra)",
       total(h) == 0, f"{total(h)}")
print()
print("=" * 74)
print(f"{OK} passaram, {FALHA} falharam")
if falhas:
    for f in falhas:
        print(f"  - {f}")
    sys.exit(1)
print("TODAS AS VERIFICACOES PASSARAM")
