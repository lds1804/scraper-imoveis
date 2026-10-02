"""Varre o projeto em busca de segredos que poderiam ir para o repositório.

Por que existe: publicar uma chave de API no GitHub é praticamente
irreversível — o histórico fica indexado por bots que varrem o serviço em
segundos, e apagar o commit depois não adianta. Vale conferir ANTES.

Além do formato óbvio (`sk-...`), aqui também se procura o VALOR real da
chave configurada no `.env`, caso ela tenha vazado para algum script de
debug ou log.

Uso:
    .venv\\Scripts\\python.exe conferir_segredos.py
"""

from __future__ import annotations


from _bootstrap import iniciar
iniciar()  # poe src/ no sys.path e fixa a raiz como diretorio de trabalho

import os
import re
import sys

RAIZ = os.path.dirname(os.path.abspath(__file__))

# Pastas que nunca entram no repositório (espelham o .gitignore).
IGNORAR_DIRS = {
    ".git", ".venv", "venv", "__pycache__", "node_modules",
    "fotos", "html_ponte", "debug_html", "playwright-profile",
    ".pytest_cache", ".mypy_cache", ".ruff_cache",
}

# Extensões que valem a pena ler. Binários são pulados.
IGNORAR_EXT = {
    ".db", ".sqlite", ".sqlite3", ".jpg", ".jpeg", ".png", ".gif",
    ".webp", ".svg", ".ico", ".pdf", ".zip", ".woff", ".woff2", ".ttf",
}

# Padrões de segredo: (nome, expressão regular)
PADROES = [
    ("chave DeepSeek/OpenAI", re.compile(r"\bsk-[A-Za-z0-9_\-]{16,}")),
    ("chave AWS", re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b")),
    ("token GitHub", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}\b")),
    ("token Slack", re.compile(r"\bxox[baprs]-[A-Za-z0-9\-]{10,}")),
    ("chave privada", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
    ("Bearer literal", re.compile(r"Bearer\s+[A-Za-z0-9_\-\.]{24,}")),
    ("senha em URL", re.compile(r"://[^/\s:@]{3,}:[^/\s:@]{3,}@")),
]

# Nomes de arquivo que NÃO devem ir para o repositório, mesmo sem segredo
# (dados gerados, perfis de navegador, resultados de execução local).
SUSPEITOS = re.compile(
    r"^(proc|visao|duplicatas|teste_playwright)\.|"
    r"^debug_|\.log$|^\.env$|^imoveis.*\.db$"
)


def _valor_da_chave_env() -> str:
    """Lê o valor real da chave do `.env`, para procurar vazamento dela."""
    caminho = os.path.join(RAIZ, ".env")
    if not os.path.exists(caminho):
        return ""
    try:
        with open(caminho, encoding="utf-8") as f:
            for linha in f:
                linha = linha.strip()
                if linha.startswith("DEEPSEEK_API_KEY") and "=" in linha:
                    return linha.partition("=")[2].strip().strip("\"'")
    except OSError:
        pass
    return ""


def _arquivos():
    for pasta, subpastas, arquivos in os.walk(RAIZ):
        subpastas[:] = [d for d in subpastas if d not in IGNORAR_DIRS]
        for nome in arquivos:
            if os.path.splitext(nome)[1].lower() in IGNORAR_EXT:
                continue
            yield os.path.join(pasta, nome)


def _mascarar(trecho: str) -> str:
    """Esconde o miolo do segredo, para não imprimi-lo no terminal."""
    if len(trecho) <= 12:
        return trecho[:4] + "…"
    return trecho[:8] + "…" + trecho[-4:]


def _varrer_index_git() -> tuple[list[str], list[str]]:
    """Varre exatamente o que o git vai publicar (o índice, não o disco).

    Esta é a checagem que vale: um arquivo pode estar no disco e corretamente
    ignorado, mas o que importa é o que foi para o índice. Também pega o caso
    inverso — alguém deu `git add -f` e furou o .gitignore.

    Usa `git ls-files` (arquivos REALMENTE versionados) e não
    `git diff --cached` — este último lista também as REMOÇÕES, o que faria
    um arquivo recém-excluído aparecer como se ainda estivesse publicado.

    Devolve (achados, lista_de_arquivos_indexados).
    """
    import subprocess

    try:
        r = subprocess.run(
            ["git", "ls-files", "--cached"],
            cwd=RAIZ, capture_output=True, text=True, timeout=60,
        )
    except (OSError, subprocess.SubprocessError):
        return [], []

    if r.returncode != 0:
        return [], []  # não é um repositório git (ou git ausente)

    arquivos = [linha.strip() for linha in r.stdout.splitlines() if linha.strip()]
    achados: list[str] = []

    for nome in arquivos:
        blob = subprocess.run(
            ["git", "show", f":{nome}"],
            cwd=RAIZ, capture_output=True, timeout=60,
        )
        if blob.returncode != 0:
            continue
        try:
            texto = blob.stdout.decode("utf-8")
        except UnicodeDecodeError:
            continue

        for rotulo, regex in PADROES:
            for m in regex.finditer(texto):
                achados.append(
                    f"[{rotulo}] (no git) {nome}:"
                    f"{texto[: m.start()].count(chr(10)) + 1}  {_mascarar(m.group(0))}"
                )

    return achados, arquivos


def main() -> int:
    chave_real = _valor_da_chave_env()
    print(f"\nChave configurada no .env: {'SIM' if chave_real else 'NAO'}")
    if chave_real:
        print(f"  valor: {_mascarar(chave_real)}  ({len(chave_real)} caracteres)")
    print("\nVarrendo arquivos (exceto "
          f"{', '.join(sorted(IGNORAR_DIRS))})...\n")

    achados: list[str] = []
    lidos = 0

    for caminho in _arquivos():
        rel = os.path.relpath(caminho, RAIZ)

        # O `.env` É o lugar legítimo da chave e está no .gitignore. Achá-la
        # aqui não é vazamento — o que importa é que ela não exista em
        # NENHUM outro arquivo, e que o .env não esteja no índice do git.
        eh_env = rel in (".env", ".env.local")

        try:
            with open(caminho, encoding="utf-8", errors="strict") as f:
                texto = f.read()
        except (OSError, UnicodeDecodeError):
            continue  # binário ou ilegível: não é onde chave de API mora

        lidos += 1

        if eh_env:
            continue

        for nome, regex in PADROES:
            for m in regex.finditer(texto):
                linha = texto[: m.start()].count("\n") + 1
                achados.append(
                    f"[{nome}] {rel}:{linha}\n      {_mascarar(m.group(0))}"
                )

        # a chave exata do .env copiada para outro arquivo
        if chave_real and chave_real in texto:
            linha = texto.index(chave_real)
            achados.append(
                f"[CHAVE REAL DO .env] {rel}:"
                f"{texto[:linha].count(chr(10)) + 1}"
            )

    print(f"Arquivos de texto lidos no disco: {lidos}")
    print("  (o proprio .env e pulado: e o local legitimo da chave)\n")

    # ---- a checagem que decide: o índice do git ----
    print("=" * 62)
    print("O QUE O GIT VAI PUBLICAR (git ls-files --cached)")
    print("=" * 62)
    achados_git, arquivos_git = _varrer_index_git()

    if not arquivos_git:
        print("  (nenhum arquivo no índice — rode `git add -A` antes)")
    else:
        print(f"  {len(arquivos_git)} arquivo(s) no índice\n")
        proibidos = [
            a for a in arquivos_git
            if re.search(r"(^|/)\.env$|playwright-profile|imoveis.*\.db$|^fotos/", a)
        ]
        if proibidos:
            print("  !!! ARQUIVO PROIBIDO NO ÍNDICE:")
            for p in proibidos:
                print(f"      {p}")
            achados_git.append(f"arquivo proibido no índice: {proibidos}")
        else:
            print("  OK: nenhum .env, perfil de navegador, banco ou pasta de fotos")

        # Arquivos GERADOS que não deveriam ser versionados. Não são segredo,
        # mas poluem o repositório e podem carregar dado de sessão ou de uso.
        # Esta checagem já pegou uma vez a pasta `dados/` inteira publicada.
        gerados = [
            a for a in arquivos_git
            if re.search(
                r"\.log$|(^|/)proc.*\.txt$|(^|/)visao.*\.txt$|"
                r"duplicatas\.(csv|txt)$|(^|/)debug_|\.png$|"
                r"^dados/|^html_ponte/|_alvos_.*\.json$|\.xlsx?$|\.csv$",
                a,
            )
        ]
        if gerados:
            print("\n  !!! ARQUIVO GERADO VERSIONADO "
                  "(deveria estar no .gitignore):")
            for g in gerados:
                print(f"      {g}")
            achados_git.append(
                f"arquivo gerado versionado ({len(gerados)}): {gerados[:5]}"
            )
        else:
            print("  OK: nenhum log, captura de tela, dump ou lista de alvos")

    achados.extend(achados_git)

    print("\n" + "=" * 62)
    if achados:
        print(f">>> {len(achados)} ACHADO(S) PARA REVISAR <<<\n")
        for a in achados:
            print(f"  {a}")
        print("\nNAO publique antes de resolver o que estiver acima.")
        return 1

    print("Nenhum segredo encontrado. Pode publicar.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
