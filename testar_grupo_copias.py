"""Testa o clique em "+N anuncios deste imovel".

O DEFEITO: o aviso no card levava para `?todas=1`, que mostra a listagem
INTEIRA (2.938 -> 4.793 anuncios em vez de 25). O usuario clicava em
"mais 24 anuncios deste imovel" e caia na mesma lista de antes, maior.

O ESPERADO: ver AS OUTRAS OFERTAS DAQUELE imovel (o grupo de duplicatas).

Uso: python testar_grupo_copias.py
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


def get(q: str = "") -> tuple[int, str]:
    with webapp.app.test_client() as c:
        r = c.get("/" + q)
        return r.status_code, r.get_data(as_text=True)


def total(html: str) -> int:
    m = re.search(r"<b>([\d.]+)</b>\s*imóve", html)
    return int(m.group(1).replace(".", "")) if m else -1


conn = sqlite3.connect(config.DB_PATH)
conn.row_factory = sqlite3.Row

# pega o maior grupo (o caso mais visivel do defeito)
g = conn.execute("""SELECT dup_grupo g, COUNT(*) n FROM anuncios
                    WHERE dup_grupo IS NOT NULL GROUP BY 1
                    ORDER BY n DESC LIMIT 1""").fetchone()
GRUPO, N_GRUPO = g["g"], g["n"]
print(f"grupo de teste: {GRUPO} com {N_GRUPO} anuncios")
conn.close()

print()
print("=" * 72)
print("1. O link do aviso aponta para o GRUPO (nao mais para todas=1)")
print("=" * 72)
_, home = get()
m = re.search(r'<a class="card-copias"\s+href="([^"]+)"', home)
checar("existe o aviso de copias na home", m is not None)
if m:
    link = m.group(1).replace("&amp;", "&")
    checar("o link NAO usa mais 'todas=1'", "todas=1" not in link, link[:80])
    checar("o link filtra por 'grupo='", "grupo=" in link, link[:80])

print()
print("=" * 72)
print("2. Clicar mostra SO as ofertas daquele imovel")
print("=" * 72)
code, html_grupo = get(f"?grupo={GRUPO}")
checar("a pagina do grupo abre", code == 200, f"HTTP {code}")
t_grupo = total(html_grupo)
checar(f"o total e' o do grupo ({N_GRUPO}), nao a base inteira",
       t_grupo == N_GRUPO, f"total={t_grupo}")
checar("nao mostra os 4.793 da base",
       t_grupo < 4793, f"total={t_grupo}")

# confere que todos os cards sao do grupo
_, home_completa = get("?todas=1")
checar("o grupo e' menor que a listagem completa",
       t_grupo < total(home_completa), f"{t_grupo} < {total(home_completa)}")

print()
print("=" * 72)
print("3. A pagina explica o contexto e oferece a saida")
print("=" * 72)
checar("avisa 'as ofertas deste mesmo imovel'",
       "ofertas deste mesmo imóvel" in html_grupo)
checar("tem o link 'ver a lista completa'",
       "ver a lista completa" in html_grupo)
checar("tem o chip de filtro ativo",
       "vendo as ofertas de um imóvel" in html_grupo)

# a URL de saida nao pode conter 'grupo='
m2 = re.search(r'class="aviso-grupo-voltar"[^>]*href="([^"]+)"', html_grupo)
if not m2:
    m2 = re.search(r'href="([^"]+)"[^>]*class="aviso-grupo-voltar"', html_grupo)
if not m2:
    m2 = re.search(r'aviso-grupo-voltar[^>]*href="([^"]+)"', html_grupo)
checar("achei o link de saida", m2 is not None)
if m2:
    saida = m2.group(1).replace("&amp;", "&")
    checar("o link de saida NAO tem 'grupo='", "grupo=" not in saida, saida[:70])
    code2, html_saida = get(saida.lstrip("/"))
    checar("o link de saida volta a lista cheia (2.938)",
           total(html_saida) > t_grupo, f"total={total(html_saida)}")

print()
print("=" * 72)
print("4. Os cards do grupo mostram qual e' a mais barata")
print("=" * 72)
checar("um card diz 'a mais barata'", "a mais barata" in html_grupo)
checar("outros dizem quanto estao acima dela",
       "acima da mais barata" in html_grupo
       or N_GRUPO == 1)
# nao deve haver o aviso clicavel de copias dentro da visao de grupo
n_link = len(re.findall(r'class="card-copias"', html_grupo))
checar("sem aviso de copias clicavel dentro do grupo",
       n_link == 0, f"{n_link} encontrados")
n_selo = len(re.findall(r'class="card-copias no-grupo"', html_grupo))
checar("com selo de grupo nos cards", n_selo > 0, f"{n_selo} selos")

print()
print("=" * 72)
print("5. Os filtros continuam funcionando junto com o grupo")
print("=" * 72)
# grupo + bairro: o filtro do grupo sobrevive
code3, html_f = get(f"?grupo={GRUPO}&bairro=Pirituba")
checar("grupo + bairro nao da erro", code3 == 200, f"HTTP {code3}")
checar("o grupo sobrevive ao filtro de bairro",
       "vendo as ofertas de um imóvel" in html_f)

# grupo invalido nao quebra
code4, html_x = get("?grupo=999999")
checar("grupo inexistente nao da erro", code4 == 200, f"HTTP {code4}")
checar("grupo inexistente mostra 0", total(html_x) == 0,
       f"total={total(html_x)}")
code5, _ = get("?grupo=abc")
checar("grupo nao numerico e' ignorado (nao quebra)", code5 == 200,
       f"HTTP {code5}")

print()
print("=" * 72)
print(f"{OK} passaram, {FALHA} falharam")
if falhas:
    for f in falhas:
        print(f"  - {f}")
    sys.exit(1)
print("TODAS AS VERIFICACOES PASSARAM")
