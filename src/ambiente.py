"""Segredos e MODO DE EXECUÇÃO (local x produção).

POR QUE ISTO EXISTE
-------------------
O `webapp.py` rodava assim, sem alternativa:

    app.run(host="127.0.0.1", port=5000, debug=True)

Isso é correto para uso local e **perigoso em servidor**:

1. **`debug=True` é execução remota de código.** O depurador interativo do
   Flask, quando exposto, permite rodar comandos no servidor — é o vetor de
   ataque mais conhecido do Flask. Quem alcança o site lê o `.env` (e a chave
   da DeepSeek) ou apaga o banco.
2. **`host="127.0.0.1"`** só escuta na própria máquina — em container na AWS
   isso significa que **ninguém** alcança, nem o balanceador. Precisa de
   `0.0.0.0` com servidor WSGI de produção.

Então o modo não pode ser fixo no código: tem de ser decidido pelo ambiente.

    SITE_AMBIENTE=local       -> debug ligado, 127.0.0.1 (o padrão de sempre)
    SITE_AMBIENTE=producao    -> debug DESLIGADO, 0.0.0.0, exige senha

AS SENHAS
---------
Sem login, "só eu tenho o link" não é controle de acesso — URL não é cadeado, e
um robô de busca indexa. Em produção o site exige autenticação básica:

    SITE_SENHA=<algo longo e aleatório>
    SITE_USUARIO=eu            (opcional, o padrão é "eu")

Se `SITE_AMBIENTE=producao` e **não** houver senha, o app **se recusa a subir**.
É de propósito: um site que acha que está protegido e não está é pior que um
site que não sobe.

O QUE NÃO ENTRA AQUI
--------------------
A `app.secret_key` do Flask não é usada: não há sessão, cookie nem formulário
com CSRF. As rotas são somente leitura (menos as `_ponte`, que ficam guardadas —
ver `webapp.py`). Sem `secret_key`, não há assinatura de cookie para forjar.
"""

from __future__ import annotations

import os
from typing import Optional

import config

AMBIENTE_ENV = "SITE_AMBIENTE"
SENHA_ENV = "SITE_SENHA"
USUARIO_ENV = "SITE_USUARIO"

USUARIO_PADRAO = "eu"

# palavras que significam "estou em servidor"
_PRODUCAO = {"producao", "produção", "production", "prod", "aws", "servidor"}


def _do_env(nome: str) -> Optional[str]:
    """Lê de variável de ambiente, com fallback para o `.env` da raiz.

    Mesma ordem usada pela chave da DeepSeek em `visao.py`: variável de
    ambiente primeiro (é o que a AWS vai usar), arquivo depois (conveniência
    no Windows, onde a variável de sessão se perde ao abrir outro terminal).
    """
    valor = os.environ.get(nome)
    if valor:
        return valor.strip()

    caminho = config.caminho(".env")
    if not os.path.exists(caminho):
        return None
    try:
        with open(caminho, encoding="utf-8") as f:
            for linha in f:
                linha = linha.strip()
                if not linha or linha.startswith("#") or "=" not in linha:
                    continue
                k, _, v = linha.partition("=")
                if k.strip() == nome:
                    return v.strip().strip('"').strip("'")
    except OSError:
        pass
    return None


def ambiente() -> str:
    """'local' (padrão) ou 'producao'."""
    bruto = (_do_env(AMBIENTE_ENV) or "local").lower()
    return "producao" if bruto in _PRODUCAO else "local"


def e_producao() -> bool:
    return ambiente() == "producao"


def senha() -> Optional[str]:
    return _do_env(SENHA_ENV) or None


def usuario() -> str:
    return _do_env(USUARIO_ENV) or USUARIO_PADRAO


def exigir_login() -> bool:
    """Em produção, autenticação é obrigatória quando há senha definida."""
    return e_producao()


def conferir() -> list[str]:
    """Valida a configuração e devolve a lista de ERROS (vazia = ok).

    Separado de `validar()` para poder ser testado sem derrubar o processo.
    """
    erros: list[str] = []
    if e_producao():
        if not senha():
            erros.append(
                f"defina {SENHA_ENV} — em produção o site precisa de senha. "
                "Sem ela, qualquer pessoa que alcance a URL vê os dados."
            )
        elif len(senha()) < 12:
            erros.append(
                f"{SENHA_ENV} tem {len(senha())} caracteres; use pelo menos 12 "
                "(é a única barreira entre a internet e o seu banco)."
            )
    return erros


def validar() -> None:
    """Derruba o processo se a configuração for insegura. Chame no boot."""
    erros = conferir()
    if erros:
        raise RuntimeError(
            "Configuração de produção insegura:\n  - " + "\n  - ".join(erros)
            + f"\n\nDefina no .env da raiz ou como variável de ambiente."
        )


def descricao() -> str:
    """Resumo de uma linha, para o log de inicialização."""
    if not e_producao():
        return "ambiente=local (debug ligado, 127.0.0.1)"
    u = usuario()
    return (f"ambiente=producao (debug DESLIGADO, 0.0.0.0, "
            f"login exigido para '{u}')")
