"""Confere a camada de seguranca: modo local x producao, login e guardas.

O que precisa ser verdade:
  - em LOCAL nada muda: a home abre sem senha, como sempre
  - em PRODUCAO sem senha, o app SE RECUSA a subir (nao "sobe inseguro")
  - em PRODUCAO com senha: 401 sem credencial, 200 com credencial
  - as rotas /_ponte/* devolvem 403 quando o cliente NAO e' 127.0.0.1
  - os cabecalhos de noindex/no-store estao em toda resposta

Uso: python testar_seguranca.py
"""

from __future__ import annotations

import os
import sys

try:
    sys.stdout.reconfigure(errors="replace")
except Exception:  # noqa: BLE001
    pass

_RAIZ = os.path.dirname(os.path.abspath(__file__))
# ORDEM IMPORTA: a raiz tem atalhos de 2 linhas (`webapp.py` -> _runner) que
# INICIAM O SERVIDOR ao serem importados. `src` tem de vir antes.
sys.path.insert(0, _RAIZ)
sys.path.insert(0, os.path.join(_RAIZ, "src"))
os.chdir(_RAIZ)

import ambiente  # noqa: E402
import config  # noqa: E402
import webapp  # noqa: E402

assert webapp.__file__.replace("\\", "/").endswith("src/webapp.py"), webapp.__file__

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


SENHA = "senha-de-teste-bem-longa-123"
os.environ[ambiente.SENHA_ENV] = SENHA
os.environ[ambiente.USUARIO_ENV] = "eu"

# ------------------------------------------------------------------ local
print("=" * 70)
print("MODO LOCAL (padrao) — nada pode mudar")
print("=" * 70)
os.environ.pop(ambiente.AMBIENTE_ENV, None)
webapp.app.config["TESTING"] = True

with webapp.app.test_client() as c:
    r = c.get("/")
    checar("a home abre SEM senha em local", r.status_code == 200,
           f"HTTP {r.status_code}")
    r2 = c.get("/_ponte/progresso")
    # em local o test_client vem de 127.0.0.1, entao a guarda deve deixar passar
    checar("_ponte responde em local (cliente = 127.0.0.1)",
           r2.status_code == 200, f"HTTP {r2.status_code}")

checar("ambiente.descricao() diz local", "local" in ambiente.descricao(),
       ambiente.descricao())

# --------------------------------------------------------------- producao
print()
print("=" * 70)
print("MODO PRODUCAO — exige senha e esconde as rotas de escrita")
print("=" * 70)
os.environ[ambiente.AMBIENTE_ENV] = "producao"
checar("ambiente() == producao", ambiente.ambiente() == "producao")
checar("exigir_login() == True", ambiente.exigir_login())
checar("configuracao valida COM senha", ambiente.conferir() == [],
       str(ambiente.conferir()))

with webapp.app.test_client() as c:
    r = c.get("/")
    checar("home SEM credencial devolve 401", r.status_code == 401,
           f"HTTP {r.status_code}")
    checar("401 manda WWW-Authenticate",
           "Basic" in (r.headers.get("WWW-Authenticate") or ""),
           r.headers.get("WWW-Authenticate") or "-")
    checar("401 nao vaza HTML do site",
           "card" not in r.get_data(as_text=True).lower())

    r = c.get("/", headers={"Authorization": "Basic " +
              __import__("base64").b64encode(
                  b"eu:" + SENHA.encode()).decode()})
    checar("home COM credencial correta devolve 200", r.status_code == 200,
           f"HTTP {r.status_code}")

    r = c.get("/", headers={"Authorization": "Basic " +
              __import__("base64").b64encode(b"eu:errada").decode()})
    checar("home com senha ERRADA devolve 401", r.status_code == 401,
           f"HTTP {r.status_code}")

    r = c.get("/", headers={"Authorization": "Basic " +
              __import__("base64").b64encode(b"outro:" + SENHA.encode()).decode()})
    checar("home com USUARIO errado devolve 401", r.status_code == 401,
           f"HTTP {r.status_code}")

    # guarda de rede: simula requisicao externa
    r = c.get("/", headers={"Authorization": "Basic " +
              __import__("base64").b64encode(
                  b"eu:" + SENHA.encode()).decode(),
              "X-Forwarded-For": "203.0.113.9"},
              environ_base={"REMOTE_ADDR": "203.0.113.9"})
    checar("requisicao EXTERNA com credencial ainda passa (login e' o portao)",
           r.status_code == 200, f"HTTP {r.status_code}")

# ---- as rotas _ponte com origem EXTERNA (o caso perigoso)
print()
print("=" * 70)
print("GUARDA DE REDE das rotas /_ponte (o defeito encontrado)")
print("=" * 70)
# Em producao o login intercepta antes, devolvendo 401. Em local (sem login) a
# guarda de rede devolve 403. Nos DOIS casos a requisicao EXTERNA e' BLOQUEADA
# -- que e' o que importa. Testar "401 ou 403" e' o honesto: exigir um codigo
# especifico testaria a ordem interna das guardas, nao a protecao.
BLOQUEADO = (401, 403)
with webapp.app.test_client() as c:
    r = c.get("/_ponte/progresso", environ_base={"REMOTE_ADDR": "203.0.113.9"})
    checar("_ponte/progresso de IP EXTERNO e' BLOQUEADO",
           r.status_code in BLOQUEADO, f"HTTP {r.status_code}")
    r = c.post("/_ponte/html", data=b"<html>x</html>",
               environ_base={"REMOTE_ADDR": "203.0.113.9"})
    checar("_ponte/html (ESCRITA) de IP EXTERNO e' BLOQUEADO",
           r.status_code in BLOQUEADO, f"HTTP {r.status_code}")
    r = c.post("/_ponte/limpar", environ_base={"REMOTE_ADDR": "203.0.113.9"})
    checar("_ponte/limpar (APAGA ARQUIVOS) de IP EXTERNO e' BLOQUEADO",
           r.status_code in BLOQUEADO, f"HTTP {r.status_code}")

# a guarda de rede em si, testada DIRETO (sem o login na frente)
print()
print("  -- a guarda de rede em si (`_e_local`) --")
with webapp.app.test_request_context(environ_base={"REMOTE_ADDR": "203.0.113.9"}):
    checar("_e_local() = False para IP externo", webapp._e_local() is False)
    try:
        webapp._ponte_so_local()
        checar("_ponte_so_local() aborta com 403", False, "nao abortou")
    except Exception as e:  # noqa: BLE001
        checar("_ponte_so_local() aborta com 403",
               getattr(e, "code", None) == 403, type(e).__name__)
for ip in ("127.0.0.1", "::1"):
    with webapp.app.test_request_context(environ_base={"REMOTE_ADDR": ip}):
        checar(f"_e_local() = True para {ip}", webapp._e_local() is True)
        try:
            webapp._ponte_so_local()
            checar(f"_ponte_so_local() deixa passar de {ip}", True)
        except Exception as e:  # noqa: BLE001
            checar(f"_ponte_so_local() deixa passar de {ip}", False,
                   type(e).__name__)

# ---- cabecalhos
print()
print("=" * 70)
print("CABECALHOS de privacidade")
print("=" * 70)
with webapp.app.test_client() as c:
    r = c.get("/", headers={"Authorization": "Basic " +
              __import__("base64").b64encode(
                  b"eu:" + SENHA.encode()).decode()})
    h = r.headers
    checar("X-Robots-Tag bloqueia indexacao",
           "noindex" in (h.get("X-Robots-Tag") or ""),
           h.get("X-Robots-Tag") or "-")
    checar("Cache-Control impede cache de dado privado",
           "no-store" in (h.get("Cache-Control") or ""),
           h.get("Cache-Control") or "-")
    checar("o HTML tem <meta robots noindex>",
           "noindex" in r.get_data(as_text=True))

# ---- o app se recusa a subir sem senha
print()
print("=" * 70)
print("RECUSA A SUBIR sem senha (o mais importante)")
print("=" * 70)
os.environ.pop(ambiente.SENHA_ENV, None)
erros = ambiente.conferir()
checar("producao SEM senha acusa erro", len(erros) > 0, str(erros)[:80])
try:
    ambiente.validar()
    checar("validar() levanta excecao", False, "nao levantou")
except RuntimeError:
    checar("validar() levanta excecao", True)
os.environ[ambiente.SENHA_ENV] = "curta"
checar("senha curta e' recusada", len(ambiente.conferir()) > 0,
       str(ambiente.conferir())[:70])

# limpa o ambiente do teste
os.environ.pop(ambiente.AMBIENTE_ENV, None)
os.environ.pop(ambiente.SENHA_ENV, None)
os.environ.pop(ambiente.USUARIO_ENV, None)

print()
print("=" * 70)
print(f"{OK} passaram, {FALHA} falharam")
if falhas:
    for f in falhas:
        print(f"  - {f}")
    sys.exit(1)
print("TODAS AS VERIFICACOES PASSARAM")
