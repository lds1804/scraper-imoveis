"""Login do Claude Code para a análise das fotos (`caca-login`).

Por que existe: a análise das fotos usa o Claude Code instalado pelo app
desktop, que fica numa pasta com a versão no nome
(`%APPDATA%\\Claude\\claude-code\\2.1.288\\...\\claude.exe`) e muda a cada
atualização. O login é interativo (abre o navegador), então só a própria
pessoa pode fazê-lo; este comando poupa de procurar o caminho e confere, ao
sair, se o login pegou.

Uso:
    caca-login
"""

from __future__ import annotations

import subprocess
import sys

from cacaimoveis.visao import achar_claude, verificar_login_claude


def main() -> int:
    binario = achar_claude()
    if not binario:
        print("Claude Code não encontrado. Defina CACA_CLAUDE_BIN com o caminho do claude.exe.")
        return 1

    problema = verificar_login_claude()
    if problema is None:
        print("O Claude Code já está logado. Nada a fazer.")
        return 0

    print(f"Claude Code: {binario}")
    print()
    print("Vai abrir o Claude. Nele:")
    print("  1. digite  /login  e conclua no navegador")
    print("  2. volte aqui e digite  /exit")
    print()
    input("Pressione Enter para abrir... ")
    # herda o terminal: é uma sessão interativa de verdade
    subprocess.call([binario])

    print()
    print("Conferindo o login...")
    problema = verificar_login_claude()
    if problema:
        print(f"Ainda não está logado: {problema}")
        return 1
    print("Login confirmado. Agora é só rodar:  caca-visao")
    return 0


if __name__ == "__main__":
    sys.exit(main())
