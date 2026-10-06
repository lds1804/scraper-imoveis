"""Análise das fotos dos anúncios por um modelo de visão.

Dois provedores (`config.VISAO_PROVEDOR`): o Claude Code CLI (`claude -p`,
padrão, usa a assinatura de quem está logado) e a API da DeepSeek. O prompt,
o formato da resposta e o que vai para o banco são os MESMOS nos dois — só
muda o transporte.

Por que existe: a listagem do Imovelweb publica só 1 foto e trunca a descrição.
Mesmo depois de enriquecer, apenas uma minoria dos anúncios informa coisas como
"quintal com terra" ou "bem cuidada" — porque o anunciante simplesmente não
escreveu. A foto mostra o que existe, independente do que foi dito.

Como funciona: as fotos de UM anúncio vão juntas na mesma requisição. Isso é
de propósito — o modelo consegue raciocinar sobre o conjunto ("o quintal
aparece nas fotos 3 e 5") e gasta menos tokens de prompt do que enviar uma
a uma. Todas as imagens vão em mensagem `user` (a API rejeita imagens em
`system` com erro 400).

Custo: cada imagem é redimensionada pela API e limitada a ~1024 tokens
(off-peak, ~US$ 0,0002 por foto). O banco inteiro sai por centavos.
"""

from __future__ import annotations

import base64
import glob
import io
import json
import os
import re
import shutil
import subprocess
import tempfile
import time
from dataclasses import dataclass, field
from typing import Any

from cacaimoveis import config, logs

log = logs.obter(__name__)

# ---------------------------------------------------------------------------
# Chave da API (nunca no código)
# ---------------------------------------------------------------------------
_ENV_ALTERNATIVAS = (config.DEEPSEEK_ENV_VAR, "DEEPSEEK_KEY", "DEEPSEEK_API_KEY")
_ARQUIVO_ENV = ".env"


def _ler_env_arquivo(chave: str) -> str | None:
    """Lê uma chave de um `.env` simples (KEY=valor) na raiz do projeto.

    A raiz vem do `config`, não do diretório deste arquivo: ele vive em
    `src/`, mas o `.env` fica ao lado do `imoveis.db`, na raiz.
    """
    caminho = config.caminho(_ARQUIVO_ENV)
    if not os.path.exists(caminho):
        return None
    try:
        with open(caminho, encoding="utf-8") as f:
            for linha in f:
                linha = linha.strip()
                if not linha or linha.startswith("#") or "=" not in linha:
                    continue
                k, _, v = linha.partition("=")
                if k.strip() == chave:
                    return v.strip().strip('"').strip("'")
    except OSError:
        pass
    return None


def obter_api_key() -> str:
    """Busca a chave da DeepSeek no ambiente (com fallback para `.env`).

    Ordem: variável de ambiente -> arquivo `.env`.
    O `.env` existe porque no Windows a variável de sessão se perde ao abrir
    um terminal novo — o arquivo é mais conveniente e continua fora do código.
    """
    for nome in _ENV_ALTERNATIVAS:
        valor = os.environ.get(nome)
        if valor:
            return valor.strip()

    for nome in _ENV_ALTERNATIVAS:
        valor = _ler_env_arquivo(nome)
        if valor:
            return valor

    raise RuntimeError(
        "Chave da DeepSeek não encontrada.\n\n"
        "Defina a variável de ambiente antes de rodar:\n"
        '  PowerShell:  $env:DEEPSEEK_API_KEY = "sk-..."\n'
        "  Permanente:  setx DEEPSEEK_API_KEY \"sk-...\"  (reabra o terminal)\n"
        "  Ou crie um arquivo .env com:  DEEPSEEK_API_KEY=sk-...\n"
    )


def tem_api_key() -> bool:
    try:
        obter_api_key()
        return True
    except RuntimeError:
        return False


# ---------------------------------------------------------------------------
# Preparação das imagens
# ---------------------------------------------------------------------------
def _jpeg_redimensionado(caminho: str, lado_max: int) -> bytes | None:
    """Lê a imagem e devolve um JPEG com no máximo `lado_max` px no lado maior.

    Redimensionar antes de enviar reduz upload e tempo de resposta. Os modelos
    redimensionariam de qualquer forma, então não há perda de qualidade útil.
    """
    if not os.path.exists(caminho):
        return None

    try:
        from PIL import Image

        with Image.open(caminho) as img:
            if img.mode not in ("RGB", "L"):
                img = img.convert("RGB")
            elif img.mode == "L":
                img = img.convert("RGB")

            largura, altura = img.size
            if max(largura, altura) > lado_max:
                escala = lado_max / max(largura, altura)
                img = img.resize(
                    (max(1, int(largura * escala)), max(1, int(altura * escala))),
                    Image.LANCZOS,
                )

            buffer = io.BytesIO()
            img.save(buffer, format="JPEG", quality=85, optimize=True)
            dados = buffer.getvalue()

    except ImportError:
        # sem Pillow: envia o arquivo como está
        with open(caminho, "rb") as f:
            dados = f.read()
    except Exception:  # noqa: BLE001
        return None
    return dados


def _preparar_imagem(caminho: str, lado_max: int) -> str | None:
    """A imagem redimensionada como data URL base64 (formato da DeepSeek)."""
    dados = _jpeg_redimensionado(caminho, lado_max)
    if dados is None:
        return None
    return "data:image/jpeg;base64," + base64.b64encode(dados).decode("ascii")


# ---------------------------------------------------------------------------
# O prompt
# ---------------------------------------------------------------------------
# O esquema pedido ao modelo. Cada campo tem um motivo:
#  - tem_quintal/piso_quintal/arvores: o critério central da busca
#  - quintal_terra: derivado de piso_quintal (compatibilidade com o banco)
#  - cuidado/arejamento/iluminacao: juízos que só a visão resolve
#  - problemas: obra/mofo/abandono — ninguém escreve isso no anúncio
#  - extras: amenidades que valorizam o imóvel
#
# NOTA sobre o piso do quintal: a versão anterior deste prompt pedia
# "quintal_terra" e dizia ao mesmo tempo "chão de TERRA BATIDA" e "grama
# aparada conta como SIM" — contradição que fez todo quintal gramado ser
# marcado como terra. Agora o piso é um campo próprio (piso_quintal) e
# `quintal_terra` é só terra mesmo.
PROMPT = """Você é um avaliador imobiliário experiente. Analise as {n} fotos deste \
anúncio de imóvel (todas são do MESMO imóvel, em São Paulo).

Responda APENAS com um objeto JSON válido, sem texto antes ou depois, sem \
blocos de código markdown. Use exatamente estas chaves:

{{
  "area_externa": "sim" | "nao" | "incerto",
  "tem_quintal": "sim" | "nao" | "incerto",
  "piso_quintal": "terra" | "grama" | "cimento" | "misto" | "incerto",
  "quintal_terra": "sim" | "nao" | "incerto",
  "parece_cimentado": "sim" | "nao" | "incerto",
  "arvores": "sim" | "nao" | "incerto",
  "vegetacao_nota": 0-5,
  "iluminacao_nota": 0-5,
  "arejamento_nota": 0-5,
  "cuidado_nota": 0-5,
  "janelas_grandes": "sim" | "nao" | "incerto",
  "fachada": "terrea" | "sobrado" | "incerto",
  "parece_reformado": "sim" | "nao" | "incerto",
  "piso": "porcelanato" | "madeira" | "ceramica" | "cimento" | "misto" | "incerto",
  "comodos": ["lista", "de", "comodos", "visiveis"],
  "extras": ["piscina", "churrasqueira", "edicula", "varanda", "area_gourmet", \
"quintal_grande", "garagem_coberta", "muro_alto", "portao_automatico"],
  "problemas": ["mofo", "obra_inacabada", "abandono", "infiltracao", "entulho"],
  "tem_planta_baixa": "sim" | "nao",
  "resumo": "uma frase curta e objetiva sobre o imóvel",
  "confianca": "alta" | "media" | "baixa"
}}

Regras importantes:
- "tem_quintal" = SIM se houver área externa descoberta nos fundos, na \
lateral ou na frente do imóvel (quintal, jardim, pátio). Varanda, sacada e \
área gourmet coberta NÃO contam como quintal. Use "incerto" se nenhuma foto \
mostrar essa área.
- "piso_quintal" = qual é o piso predominante do quintal: "terra" (terra \
batida, sem vegetação), "grama" (gramado), "cimento" (cimentado, ladrilhado, \
pedra ou deck), "misto" (mais de um tipo com presença real), "incerto" \
(quintal existe mas as fotos não mostram o chão de forma clara).
- "quintal_terra" = SIM APENAS se o piso do quintal for TERRA BATIDA \
predominante (sem grama). Gramado é NÃO neste campo — grama vai em \
"piso_quintal": "grama". Se o quintal não aparece ou não dá para ver o chão, \
use "incerto".
- "arvores" = SIM se houver qualquer árvore de porte visível (copa e/ou tronco), \
mesmo ao fundo. Arbustos e plantas em vaso não contam.
- Use "incerto" quando as fotos não mostrarem a área em questão. NÃO invente.
- "cuidado_nota": 5 = impecavelmente conservado/reformado; 1 = visivelmente \
degradado.
- "arejamento_nota": considere quantidade e tamanho das janelas, se há \
ventilação cruzada aparente, varanda ou área aberta. Lembre que fotos com \
grande angular podem exagerar a sensação de amplitude — seja criterioso.
- "iluminacao_nota": luz natural percebida nos ambientes internos.
- Em "extras" e "problemas", liste APENAS o que realmente aparece. Lista vazia \
é uma resposta válida e comum.
- "comodos" em português, minúsculas, sem acento.
"""


# ---------------------------------------------------------------------------
# Chamada da API
# ---------------------------------------------------------------------------
def _chamar_api(imagens: list[str], texto_prompt: str) -> str:
    """Envia as imagens + prompt e devolve o conteúdo textual da resposta."""
    import requests

    chave = obter_api_key()

    conteudo: list[dict[str, Any]] = [{"type": "text", "text": texto_prompt}]
    for data_url in imagens:
        conteudo.append(
            {
                "type": "image_url",
                "image_url": {"url": data_url, "detail": config.VISAO_DETALHE},
            }
        )

    corpo = {
        "model": config.VISAO_MODELO,
        "messages": [{"role": "user", "content": conteudo}],
        "temperature": 0.1,  # avaliação: queremos consistência, não criatividade
        "stream": False,
        # O deepseek-flash liga o "thinking" por padrão. Nesta tarefa
        # (olhar fotos e preencher campos) ele só custa tempo e tokens:
        # medido em ~50s por anúncio com thinking, contra poucos segundos sem.
        "thinking": {
            "type": "enabled" if config.VISAO_THINKING else "disabled"
        },
    }
    if config.VISAO_THINKING:
        corpo["reasoning_effort"] = config.VISAO_REASONING_EFFORT

    r = requests.post(
        f"{config.VISAO_BASE_URL}/chat/completions",
        headers={
            "Authorization": f"Bearer {chave}",
            "Content-Type": "application/json",
        },
        json=corpo,
        timeout=180,
    )
    if r.status_code != 200:
        raise RuntimeError(f"HTTP {r.status_code}: {r.text[:400]}")

    dados = r.json()
    return dados["choices"][0]["message"]["content"]


# ---------------------------------------------------------------------------
# Claude Code CLI
# ---------------------------------------------------------------------------
# O CLI não recebe imagem por parâmetro: ele LÊ arquivos com a ferramenta
# Read. As fotos redimensionadas vão para uma pasta temporária, a única que
# o processo pode ler (`--add-dir`), e Read é a única ferramenta liberada —
# o modelo não consegue editar, executar nem navegar.
_INSTRUCAO_CLAUDE = """Use a ferramenta Read para abrir CADA uma destas {n} imagens \
(pode abrir várias de uma vez). Não use nenhuma outra ferramenta.

{lista}

Depois de ver todas, siga as instruções abaixo.

"""


def _sem_login(texto: str) -> bool:
    """A mensagem do Claude Code diz que não há login?"""
    t = texto.lower()
    return "logged in" in t or "login" in t


def achar_claude() -> str | None:
    """Caminho do executável do Claude Code, ou None se não houver.

    Ordem: `CACA_CLAUDE_BIN` -> PATH -> versão mais recente instalada pelo
    app desktop no Windows (a pasta muda a cada atualização do app).
    """
    if config.VISAO_CLAUDE_BIN:
        return config.VISAO_CLAUDE_BIN if os.path.exists(config.VISAO_CLAUDE_BIN) else None
    no_path = shutil.which("claude")
    if no_path:
        return no_path
    # O app desktop é empacotado (MSIX): DENTRO dele `%APPDATA%\Claude` é uma
    # pasta virtualizada e um terminal comum NÃO a enxerga; o arquivo de
    # verdade fica em `%LOCALAPPDATA%\Packages\Claude_*\LocalCache\Roaming`.
    # Procura nos dois: sem o segundo, rodar `caca-visao` num PowerShell normal
    # dizia "Claude Code não encontrado" (foi o que aconteceu).
    raizes = []
    if os.environ.get("APPDATA"):
        raizes.append(os.path.join(os.environ["APPDATA"], "Claude", "claude-code"))
    if os.environ.get("LOCALAPPDATA"):
        raizes += glob.glob(os.path.join(
            os.environ["LOCALAPPDATA"], "Packages", "Claude_*",
            "LocalCache", "Roaming", "Claude", "claude-code"))
    achados = [c for r in raizes for c in glob.glob(os.path.join(r, "*", "*", "claude.exe"))]
    if achados:
        return max(achados, key=os.path.getmtime)
    return None


def _chamar_claude(caminhos: list[str], texto_prompt: str) -> str:
    """Analisa as fotos com `claude -p` e devolve o texto da resposta."""
    binario = achar_claude()
    if not binario:
        raise RuntimeError(
            "Claude Code CLI não encontrado. Defina CACA_CLAUDE_BIN com o caminho "
            "do claude.exe, ou use a DeepSeek (--provedor deepseek).")

    with tempfile.TemporaryDirectory(prefix="caca_visao_") as pasta:
        arquivos = []
        for i, caminho in enumerate(caminhos):
            dados = _jpeg_redimensionado(caminho, config.VISAO_LADO_MAX_PX)
            if dados is None:
                continue
            destino = os.path.join(pasta, f"foto_{i:02d}.jpg")
            with open(destino, "wb") as f:
                f.write(dados)
            arquivos.append(destino)
        if not arquivos:
            raise RuntimeError("nenhuma foto pôde ser lida")

        lista = "\n".join(f"- {a}" for a in arquivos)
        prompt = (_INSTRUCAO_CLAUDE.format(n=len(arquivos), lista=lista)
                  + texto_prompt.replace("{n}", str(len(arquivos))))
        cmd = [
            binario, "-p", prompt,
            "--model", config.VISAO_CLAUDE_MODELO,
            "--tools", "Read",
            "--allowedTools", "Read",
            "--add-dir", pasta,
            "--output-format", "json",
            "--no-session-persistence",
            "--strict-mcp-config",
        ]
        # cwd na pasta temporária: o CLI não carrega o CLAUDE.md nem as
        # configurações deste projeto
        proc = subprocess.run(
            cmd, cwd=pasta, capture_output=True, text=True, encoding="utf-8",
            errors="replace", timeout=config.VISAO_CLAUDE_TIMEOUT_S,
        )

    try:
        saida = json.loads(proc.stdout)
    except json.JSONDecodeError:
        raise RuntimeError(
            f"resposta inesperada do claude (código {proc.returncode}): "
            f"{(proc.stdout or proc.stderr)[:300]}") from None
    if saida.get("is_error"):
        texto = str(saida.get("result") or saida.get("terminal_reason") or "erro")
        if _sem_login(texto):
            texto += " — abra um terminal e rode o claude.exe uma vez para fazer login"
        raise RuntimeError(f"claude: {texto[:300]}")
    return str(saida.get("result") or "")


def verificar_login_claude() -> str | None:
    """None se o Claude Code está logado e responde; senão, o que fazer.

    Uma chamada mínima, sem ferramentas e sem ler foto, ANTES de começar um
    lote. Sem isto, o lote inteiro falhava anúncio a anúncio com "Not logged
    in" (1.286 anúncios x 3 tentativas = ~2 horas) sem avisar que bastava
    fazer o login.
    """
    binario = achar_claude()
    if not binario:
        return "Claude Code CLI não encontrado (defina CACA_CLAUDE_BIN)."
    try:
        proc = subprocess.run(
            [binario, "-p", "Responda apenas: ok", "--model", config.VISAO_CLAUDE_MODELO,
             "--tools", "", "--output-format", "json", "--no-session-persistence",
             "--strict-mcp-config"],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=90, cwd=tempfile.gettempdir())
        saida = json.loads(proc.stdout)
    except (subprocess.TimeoutExpired, json.JSONDecodeError, OSError) as e:
        return f"o Claude Code não respondeu ({type(e).__name__})."
    if saida.get("is_error"):
        texto = str(saida.get("result") or "erro")
        if _sem_login(texto):
            return ("o Claude Code não está logado. Abra um terminal, rode `claude` "
                    "(o executável fica em %APPDATA%\\Claude\\claude-code\\...), "
                    "faça `/login` e rode de novo. Ou use `--provedor deepseek`.")
        return f"o Claude Code respondeu com erro: {texto[:200]}"
    return None


def provedor_pronto(provedor: str) -> str | None:
    """None se o provedor pode ser usado; senão, a explicação do que falta."""
    if provedor == "claude":
        if achar_claude():
            return None
        return ("Claude Code CLI não encontrado (defina CACA_CLAUDE_BIN ou "
                "instale o claude no PATH).")
    if provedor == "deepseek":
        try:
            obter_api_key()
            return None
        except RuntimeError as e:
            return str(e)
    return f"provedor desconhecido: {provedor!r} (use 'claude' ou 'deepseek')"


def _extrair_json(texto: str) -> dict:
    """Extrai o JSON da resposta, tolerando cercas de código ou texto em volta."""
    limpo = (texto or "").strip()

    # remove cerca ```json ... ```
    cerca = re.search(r"```(?:json)?\s*(.*?)```", limpo, re.DOTALL)
    if cerca:
        limpo = cerca.group(1).strip()

    try:
        return json.loads(limpo)
    except json.JSONDecodeError:
        pass

    # tenta isolar o primeiro objeto balanceado
    inicio = limpo.find("{")
    if inicio >= 0:
        profundidade = 0
        for i in range(inicio, len(limpo)):
            if limpo[i] == "{":
                profundidade += 1
            elif limpo[i] == "}":
                profundidade -= 1
                if profundidade == 0:
                    try:
                        return json.loads(limpo[inicio : i + 1])
                    except json.JSONDecodeError:
                        break
    raise ValueError(f"não consegui extrair JSON da resposta: {limpo[:200]!r}")


# ---------------------------------------------------------------------------
# Resultado
# ---------------------------------------------------------------------------
def _sim_nao(valor: Any) -> bool | None:
    """Converte 'sim'/'nao'/'incerto' em True/False/None."""
    if isinstance(valor, bool):
        return valor
    t = str(valor or "").strip().lower()
    if t in ("sim", "s", "true", "yes", "1"):
        return True
    if t in ("nao", "não", "n", "false", "no", "0"):
        return False
    return None


def _nota(valor: Any) -> int | None:
    try:
        n = int(round(float(valor)))
    except (TypeError, ValueError):
        return None
    return max(0, min(5, n))


def _lista(valor: Any) -> list[str]:
    if isinstance(valor, str):
        valor = [p.strip() for p in valor.split(",")]
    if not isinstance(valor, (list, tuple)):
        return []
    return [str(x).strip().lower() for x in valor if str(x).strip()]


@dataclass
class AnaliseFoto:
    """Resultado da análise visual de um anúncio."""

    url: str
    n_fotos: int = 0
    ok: bool = False
    erro: str = ""
    resumo: str = ""
    confianca: str = ""
    # True quando o resultado veio de um duplicado (não gastou tokens)
    analise_reaproveitada: bool = False
    # localização / quintal
    area_externa: bool | None = None
    tem_quintal: bool | None = None
    piso_quintal: str = ""
    quintal_terra: bool | None = None
    parece_cimentado: bool | None = None
    arvores: bool | None = None
    # notas 0-5
    vegetacao_nota: int | None = None
    iluminacao_nota: int | None = None
    arejamento_nota: int | None = None
    cuidado_nota: int | None = None
    # características
    janelas_grandes: bool | None = None
    fachada: str = ""
    parece_reformado: bool | None = None
    piso: str = ""
    comodos: list[str] = field(default_factory=list)
    extras: list[str] = field(default_factory=list)
    problemas: list[str] = field(default_factory=list)
    tem_planta_baixa: bool | None = None
    bruto: dict = field(default_factory=dict)
    # quem analisou ("claude:sonnet", "deepseek-flash"): permite comparar
    # provedores e refazer só o que veio de um deles
    modelo: str = ""
    # a falha foi do TRANSPORTE (login, rede, timeout, chave), não do anúncio:
    # não deve ser gravada, para a próxima rodada tentar de novo
    transitorio: bool = False

    _PISO_QUINTAL_VALIDOS = ("terra", "grama", "cimento", "misto", "incerto")

    @classmethod
    def do_json(cls, url: str, n_fotos: int, dados: dict) -> AnaliseFoto:
        piso_q = str(dados.get("piso_quintal", "")).lower().strip()[:10]
        if piso_q not in cls._PISO_QUINTAL_VALIDOS:
            piso_q = ""
        return cls(
            url=url,
            n_fotos=n_fotos,
            ok=True,
            resumo=str(dados.get("resumo", ""))[:400],
            confianca=str(dados.get("confianca", "")).lower()[:10],
            area_externa=_sim_nao(dados.get("area_externa")),
            tem_quintal=_sim_nao(dados.get("tem_quintal")),
            piso_quintal=piso_q,
            quintal_terra=_sim_nao(dados.get("quintal_terra")),
            parece_cimentado=_sim_nao(dados.get("parece_cimentado")),
            arvores=_sim_nao(dados.get("arvores")),
            vegetacao_nota=_nota(dados.get("vegetacao_nota")),
            iluminacao_nota=_nota(dados.get("iluminacao_nota")),
            arejamento_nota=_nota(dados.get("arejamento_nota")),
            cuidado_nota=_nota(dados.get("cuidado_nota")),
            janelas_grandes=_sim_nao(dados.get("janelas_grandes")),
            fachada=str(dados.get("fachada", "")).lower()[:12],
            parece_reformado=_sim_nao(dados.get("parece_reformado")),
            piso=str(dados.get("piso", "")).lower()[:14],
            comodos=_lista(dados.get("comodos")),
            extras=_lista(dados.get("extras")),
            problemas=_lista(dados.get("problemas")),
            tem_planta_baixa=_sim_nao(dados.get("tem_planta_baixa")),
            bruto=dados,
        )


# ---------------------------------------------------------------------------
# API pública do módulo
# ---------------------------------------------------------------------------
def _amostrar_fotos(caminhos: list[str], limite: int) -> list[str]:
    """Escolhe até `limite` fotos espalhadas pela galeria.

    Por que não pegar as N primeiras: a ordem típica de um anúncio é fachada,
    sala, cozinha, quartos, banheiro e só no FIM o quintal/fundos. Com 50
    fotos, as 8 primeiras seriam quase todas de ambientes internos e o
    quintal nunca apareceria — justamente o campo mais importante da busca.

    A estratégia mantém a foto 0 (fachada, sempre informativa) e distribui
    o restante em passos iguais pelo intervalo todo, garantindo que o fim
    da galeria (quintal, edícula, área gourmet) seja visto.

    `limite <= 0` significa TODAS: devolve a lista inteira sem mexer.
    """
    n = len(caminhos)
    if limite <= 0 or n <= limite:
        return list(caminhos)
    if limite == 1:
        return [caminhos[0]]

    # passo fracionário: cobre de 0 até n-1 de forma uniforme
    passo = (n - 1) / (limite - 1)
    escolhidos = [caminhos[round(i * passo)] for i in range(limite)]

    # remove repetições mantendo a ordem original
    vistos: set[str] = set()
    saida: list[str] = []
    for c in escolhidos:
        if c not in vistos:
            vistos.add(c)
            saida.append(c)
    return saida


def analisar_anuncio(
    url: str,
    caminhos_fotos: list[str],
    *,
    max_fotos: int | None = None,
    verbose: bool = True,
    provedor: str | None = None,
) -> AnaliseFoto:
    """Analisa as fotos de um anúncio e devolve os parâmetros extraídos.

    As fotos vão juntas na mesma chamada, o que permite ao modelo raciocinar
    sobre o conjunto do imóvel.
    """
    provedor = provedor or config.VISAO_PROVEDOR
    if max_fotos is not None:
        limite = max_fotos
    elif provedor == "claude":
        limite = config.VISAO_CLAUDE_MAX_FOTOS
    else:
        limite = config.VISAO_MAX_FOTOS
    fotos = [c for c in caminhos_fotos if c]
    # limite <= 0 = todas as fotos; a amostragem só se aplica quando há teto
    if limite > 0 and config.VISAO_AMOSTRAGEM:
        fotos = _amostrar_fotos(fotos, limite)
    elif limite > 0:
        fotos = fotos[:limite]

    if not fotos:
        return AnaliseFoto(url=url, ok=False, erro="sem fotos")

    if provedor == "claude":
        n_fotos = sum(1 for c in fotos if os.path.exists(c))

        def chamar() -> str:
            return _chamar_claude(fotos, prompt)
    else:
        imagens = [d for d in (_preparar_imagem(c, config.VISAO_LADO_MAX_PX) for c in fotos) if d]
        n_fotos = len(imagens)

        def chamar() -> str:
            return _chamar_api(imagens, prompt)

    if not n_fotos:
        return AnaliseFoto(url=url, ok=False, erro="nenhuma foto pôde ser lida")

    prompt = PROMPT.format(n=n_fotos)

    ultimo_erro = ""
    transitorio = False
    for tentativa in range(1, config.VISAO_MAX_TENTATIVAS + 1):
        try:
            if verbose:
                print(f"    enviando {n_fotos} foto(s) a {provedor} (tent. {tentativa})...")
            dados = _extrair_json(chamar())
            a = AnaliseFoto.do_json(url, n_fotos, dados)
            a.modelo = (f"claude:{config.VISAO_CLAUDE_MODELO}" if provedor == "claude"
                        else config.VISAO_MODELO)
            return a

        except Exception as e:  # noqa: BLE001
            log.warning("erro tratado, a execução segue: %s", e, exc_info=True)
            ultimo_erro = str(e)
            # JSON ruim (ValueError) é problema da resposta para ESTE anúncio;
            # o resto (RuntimeError, timeout, rede) é do caminho até o modelo
            transitorio = not isinstance(e, ValueError)
            if verbose:
                print(f"    [erro] {ultimo_erro[:150]}")
            if tentativa < config.VISAO_MAX_TENTATIVAS:
                time.sleep(3 * tentativa)

    return AnaliseFoto(url=url, ok=False, erro=ultimo_erro[:300], n_fotos=n_fotos,
                       transitorio=transitorio)
