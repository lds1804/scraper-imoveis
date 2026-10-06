"""Configurações do scraper do Imovelweb."""

from dataclasses import dataclass, field

# ---------------------------------------------------------------------------
# Alvos de busca
# ---------------------------------------------------------------------------
# O Imovelweb usa URLs no formato: tipo-operacao-BAIRRO-cidade-uf
#     /casas-venda-parque-sao-domingos-sao-paulo-sp.html
#
# ATENÇÃO — o título da página denuncia se o bairro foi reconhecido:
#   "Casas à venda NO Parque São Domingos, São Paulo"   -> OK (bairro filtrado)
#   "Casas à venda em São Paulo, SP OU Vila Mangalot"   -> bairro NÃO filtrado
#   "Casas à venda no Brasil"                           -> fallback nacional
#
# Armadilhas já confirmadas no site:
#   1. Grafia errada cai no fallback. Ex.: `parque-sao-domingo` (sem o "s")
#      devolve "1.672.720 Casas no Brasil"; o correto é `parque-sao-domingos`.
#   2. O formato `tipo-cidade-uf-bairro` (cidade ANTES do bairro) é aceito mas
#      NÃO filtra o bairro — devolve a cidade inteira (381.953 imóveis).
#
# `coletar_bairro` valida o título e testa as grafias alternativas de cada
# bairro, então erros de slug viram "bairro pulado" em vez de dados sujos.
CIDADE = "sao-paulo"
UF = "sp"
NOME_CIDADE = "São Paulo"
NOME_ESTADO = "São Paulo"   # a API do ZAP/VivaReal usa o nome por extenso

OPERACAO = "venda"
TIPO = "casas"  # casas | apartamentos | terrenos ...


@dataclass(frozen=True)
class Bairro:
    """Um bairro-alvo da coleta."""

    slug: str                     # como aparece na URL do site
    nome: str                     # como exibir na interface
    grupo: str = "principal"      # "principal" (foco) | "vizinho" (expansão)
    # grafias alternativas do MESMO bairro (o site às vezes só aceita uma)
    apelidos: tuple[str, ...] = field(default_factory=tuple)

    @property
    def todos_slugs(self) -> list[str]:
        return [self.slug, *self.apelidos]


# Bairros-alvo. Os dois primeiros são o foco original; o resto amplia a busca
# para a mesma região (zona oeste/norte de SP — Lapa, Pirituba-Jaraguá).
BAIRROS: list[Bairro] = [
    # --- foco original ---
    Bairro("vila-mangalot", "Vila Mangalot"),
    Bairro(
        "parque-sao-domingos",
        "Parque São Domingos",
        apelidos=("parque-sao-domingo",),  # grafia sem o "s"
    ),
    # --- vizinhos (mesma região) ---
    Bairro("pirituba", "Pirituba", grupo="vizinho"),
    Bairro("vila-jaguara", "Vila Jaguara", grupo="vizinho"),
    Bairro("jaguara", "Jaguara", grupo="vizinho"),
    Bairro("city-america", "City América", grupo="vizinho"),
    Bairro("parque-maria-domitila", "Parque Maria Domitila", grupo="vizinho"),
    Bairro("vila-bonilha", "Vila Bonilha", grupo="vizinho"),
    Bairro("jardim-sao-domingos", "Jardim São Domingos", grupo="vizinho"),
    Bairro("vila-leopoldina", "Vila Leopoldina", grupo="vizinho"),
    Bairro("vila-anastacio", "Vila Anastácio", grupo="vizinho"),
    Bairro("lapa", "Lapa", grupo="vizinho"),
    Bairro("vila-romana", "Vila Romana", grupo="vizinho"),
    Bairro("agua-branca", "Água Branca", grupo="vizinho"),
    Bairro("barra-funda", "Barra Funda", grupo="vizinho"),
    Bairro("parque-da-lapa", "Parque da Lapa", grupo="vizinho"),
    Bairro("vila-ribeiro-de-barros", "Vila Ribeiro de Barros", grupo="vizinho"),
    Bairro("jardim-libano", "Jardim Líbano", grupo="vizinho"),
    Bairro("vila-ipe", "Vila Ipê", grupo="vizinho"),
    Bairro("jardim-iris", "Jardim Íris", grupo="vizinho"),
    Bairro("vila-pereira-barreto", "Vila Pereira Barreto", grupo="vizinho"),
]

# Cidade/UF esperados: tudo que não for daqui é descartado.
CIDADES_ACEITAS = ("sao paulo",)
UFS_ACEITAS = ("sp", "sao paulo")

# Preço máximo (R$) — teto aplicado ao COLETAR.
#
# ATENÇÃO: bairro de alto padrão tem o estoque inteiro acima do teto, e o
# descarte acontece em SILÊNCIO (o log só diz "acima do preço"). City América,
# por exemplo, começa em ~R$ 1,75 mi: com teto de R$ 900 mil, os 29 anúncios
# de lá sumiam sem nenhum aviso.
#
# O teto NÃO limita o que você vê: a interface tem filtro "Preço máx." livre,
# e o banco guarda o que foi coletado com este teto.
PRECO_MAX = 1_000_000
# Preço mínimo (opcional, None desativa)
PRECO_MIN = None

# ---------------------------------------------------------------------------
# Filtros aplicados DEPOIS do scraping (no texto/anúncio)
# ---------------------------------------------------------------------------
AREA_TERRENO_MIN = 200  # m² de terreno mínimo

# Palavras que sugerem "quintal com terra" / imóvel amplo
PALAVRAS_QUINTAL = [
    "quintal",
    "chão de terra",
    "chao de terra",
    "terra batida",
    "terreno amplo",
    "fundo amplo",
    "amplo quintal",
    "área externa",
    "area externa",
    "jardim",
]

# Palavras que indicam NÃO ter quintal com terra (ex: quintal 100% cimentado)
PALAVRAS_NEGATIVAS = [
    "sem quintal",
    "quintal cimentado",
    "quintal todo cimentado",
]

# ---------------------------------------------------------------------------
# Nota de encaixe (ordem "encaixe" da listagem)
# ---------------------------------------------------------------------------
# O usuário pediu: "na busca de um peso maior para imóveis bem conservados.
# alguns estão com desconto mas estão mais conservados."
#
# MEDIDO antes de escolher os pesos (1.384 anúncios com análise visual E
# comparação de preço):
#   correlação(conservação, razão de preço) = +0,032  -> praticamente ZERO
#   correlação(conservação, preço absoluto) = +0,349
# Conclusão: **conservação e desconto são independentes**. São dois eixos
# diferentes, então a nota SOMA os dois — usar um no lugar do outro perderia
# informação. (Se fossem correlacionados, somar contaria a mesma coisa duas
# vezes.)
#
# Efeito medido com 0,5/0,5: dos 60 primeiros, **44 mudam**, e os 44 que
# entram são TODOS bem conservados (≥4) e NENHUM tem problema visível. Na
# ordem atual (só desconto) o topo tinha imóvel com mofo/infiltração nas fotos
# e cuidado 2/5 — porque a lista só olhava o preço.
ENCAIXE_PESO_DESCONTO = 0.5      # quanto abaixo do mercado (0 a 1)
ENCAIXE_PESO_CONSERVACAO = 0.5   # bem conservado nas fotos (0 a 1)
# Problema visível (mofo, infiltração, entulho, obra inacabada) desconta.
# 0,15 é o tamanho de um degrau de conservação — o suficiente para o imóvel
# perder para um equivalente sem problema, sem sumir da lista.
ENCAIXE_PENAL_PROBLEMA = 0.15
# Sem análise visual, o anúncio NÃO é penalizado nem premiado: vai para o fim
# da ordem (não dá para julgar conservação sem ter olhado a foto).
ENCAIXE_DESCONTO_MAX = 0.5       # desconto acima disso não conta mais
# Quanto do desconto vale conforme a CONFIANÇA da comparação com o ITBI.
# Medido em 2026-10-06: 48% das comparações têm confiança "baixa" (~3
# vendas de base), e sem este peso elas lideravam a ordem padrão — o topo da
# lista era "65% abaixo · confiança baixa". Um desconto apoiado em poucas
# vendas é uma pista, não uma certeza.
ENCAIXE_PESO_CONFIANCA = {"alta": 1.0, "media": 0.8, "baixa": 0.5}

# ---------------------------------------------------------------------------
# Rede / polite scraping
# ---------------------------------------------------------------------------
BASE_URL = "https://www.imovelweb.com.br"

# ---------------------------------------------------------------------------
# OLX (segundo portal)
# ---------------------------------------------------------------------------
# ARMADILHA: a OLX não tem URL por bairro. Anexar o bairro no caminho
# (`/sao-paulo-e-regiao/vila-mangalot`) é ACEITO mas NÃO filtra — devolve a
# região inteira em silêncio. Verificado: veio São Caetano do Sul, Santana de
# Parnaíba, Santo Amaro, Vila Mariana…
#
# Quem filtra é a busca textual (`?q=`), e mesmo ela é tolerante: "pirituba"
# traz Jardim Íris, Vila Barreto e outros vizinhos. Por isso o fluxo é o MESMO
# do Imovelweb: buscar amplo e validar o bairro pelo endereço de cada anúncio
# (`e_bairro_alvo`). Isso é uma vantagem — uma busca por bairro acaba
# capturando anúncios de outros bairros-alvo da mesma região.
OLX_BASE = "https://www.olx.com.br"
OLX_REGIAO = "/imoveis/venda/estado-sp/sao-paulo-e-regiao"
# A paginação é `?o=N` (50 anúncios por página; o site aceita até 100).
OLX_MAX_PAGINAS = 10

# Quantas vezes tentar um bairro antes de desistir. Existe porque o perfil do
# Playwright é COMPARTILHADO: se outro processo abrir/fechar o navegador, este
# recebe "Target page, context or browser has been closed" no meio da coleta.
# Recriar a sessão resolve, e repetir é seguro — os anúncios já salvos são
# pulados por URL.
OLX_MAX_TENTATIVAS_BAIRRO = 3

# ---------------------------------------------------------------------------
# ZAP Imóveis + Viva Real (mesma API, mesmo inventário)
# ---------------------------------------------------------------------------
# Endpoint: GET https://glue-api.vivareal.com/v2/listings
# Header obrigatório: x-domain (www.zapimoveis.com.br ou www.vivareal.com.br)
# Responde a `requests` simples — não precisa de navegador.
#
# ATENÇÃO: o parâmetro `unitTypes=HOME` é aceito e IGNORADO pela API. A
# filtragem de tipo é feita no cliente, pelo campo `listing.unitTypes`.
#
# O `size` tem LIMITE: 30 funciona, 32 devolve HTTP 400. Verificado.
GLUE_PAGINA_TAMANHO = 30
GLUE_MAX_PAGINAS = 20
# O robots.txt do ZAP e do VivaReal declara `Crawl-delay: 10` para todos os
# robôs (conferido em 2026-10-05, ver docs/plano-deploy-aws.md §2). A API é o
# mesmo backend dos dois sites, então o pedido do dono vale para ela também.
# A coleta fica mais lenta (20 páginas = ~3 min por bairro), e é o preço.
GLUE_DELAY_S = 10.0

# ---------------------------------------------------------------------------
# QuintoAndar
# ---------------------------------------------------------------------------
# Endpoint: POST https://apigw.prod.quintoandar.com.br/house-listing-search/v3/search/list
# Body JSON com `slug` no formato <bairro>-<cidade>-<uf>-brasil.
# O filtro de tipo AQUI funciona: filters.houseSpecs.houseTypes = ["HOUSE"].
QUINTO_API = "https://apigw.prod.quintoandar.com.br/house-listing-search/v3/search/list"
QUINTO_PAGINA_TAMANHO = 50
QUINTO_MAX_PAGINAS = 10
QUINTO_DELAY_S = 1.0
# foto: a API devolve o nome do arquivo; a URL final é montada com este prefixo
QUINTO_FOTO_BASE = "https://www.quintoandar.com.br/img/crop/landscape/1200x800/"

# Centro aproximado da região de busca (Vila Mangalot / Pirituba). A API usa
# coordenada + raio, não nome de bairro.
QUINTO_CENTRO = (-23.501442, -46.745277)

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/122.0.0.0 Safari/537.36"
)

# Segundos de espera entre requisições (aleatorizado entre min e max)
DELAY_MIN = 4.0
DELAY_MAX = 8.0

# Número máximo de tentativas por requisição
MAX_RETRIES = 3

# Quantas aberturas de página seguidas bloqueadas pelo Cloudflare encerram a
# coleta do Imovelweb. Cada abertura já tenta MAX_RETRIES vezes (~4 min); sem
# este limite, um bloqueio rendia mais de uma hora de espera para 18 bairros
# sem coletar nada. A coleta para, guarda o que já veio e sai com código 3.
IMOVELWEB_MAX_BLOQUEIOS = 3

# Tempo máximo de espera para o desafio anti-bot (Cloudflare) resolver
CHALLENGE_TIMEOUT_MS = 25_000

# Se True, abre o navegador visível (ajuda a passar pelo Cloudflare)
HEADLESS = False

# Perfil persistente do Playwright (guarda cookies do Cloudflare entre execuções)
USER_DATA_DIR = "playwright-profile"

# Timeout das requisições (segundos)
TIMEOUT = 30

# Timeout de navegação (ms)
NAV_TIMEOUT_MS = 60_000

# Número máximo de páginas de resultados por bairro no Imovelweb.
# Medido em 2026-10-06: a busca vem em ordem de RELEVÂNCIA (o bairro aparece
# primeiro), as páginas 1 a 4 mantiveram 100% do bairro, e o Cloudflare barrou
# na página 5 (ele limita por ritmo: ~120 anúncios seguidos). Ordenar por
# "mais recentes" (`-ordem-publicado-maior`) NÃO serve: o filtro de bairro some
# e a lista vira a cidade inteira (371 mil casas).
MAX_PAGINAS = 4

# ---------------------------------------------------------------------------
# Caminhos
# ---------------------------------------------------------------------------
# O projeto tem `src/` (código), `web/` (interface) e pastas de dados. Todos
# os caminhos são resolvidos a partir da RAIZ do projeto, não do diretório
# atual — senão rodar `python src/main.py` de dentro de `src/` criaria um
# `imoveis.db` novo lá dentro, e o banco "sumiria" sem nenhum erro.
#
# Este arquivo mora em `<raiz>/src/cacaimoveis/config.py`: a raiz fica três
# níveis acima.
import os as _os

RAIZ = _os.path.dirname(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))

def caminho(*partes: str) -> str:
    """Monta um caminho absoluto a partir da raiz do projeto."""
    return _os.path.join(RAIZ, *partes)

# ---------------------------------------------------------------------------
# Ambiente
# ---------------------------------------------------------------------------
# "local" (padrão) é a máquina de trabalho: liga o modo debug do Flask e as
# rotas `/_ponte/*`, que gravam e APAGAM arquivos em disco a pedido do
# navegador. "producao" é o site publicado: nada disso pode estar exposto lá.
#   PowerShell:  $env:CACA_AMBIENTE = "producao"
AMBIENTE = _os.environ.get("CACA_AMBIENTE", "local").strip().lower()
EM_PRODUCAO = AMBIENTE == "producao"

# ---------------------------------------------------------------------------
# Armazenamento local
# ---------------------------------------------------------------------------
# `CACA_DB` troca o banco sem mexer no código: o site publicado lê o `site.db`
# (46 MB) em vez do `imoveis.db` de trabalho, e os testes usam um banco
# pequeno criado na hora.
DB_PATH = _os.environ.get("CACA_DB") or caminho("imoveis.db")
FOTOS_DIR = _os.environ.get("CACA_FOTOS") or caminho("fotos")

# Quantas fotos BAIXAR por anúncio (0 = todas).
#
# Medido: a mediana é 12 fotos, mas a cauda é longa (há anúncio com 135). A
# análise visual usa no máximo `VISAO_MAX_FOTOS` e as amostra espalhadas pela
# galeria — então baixar 135 imagens de 91 KB para analisar 12 é desperdício
# de banda, disco e tempo (é o gargalo da coleta: ~8 fotos/s no CDN).
#
# 20 preserva folga sobre o que a análise usa, mantém o carrossel da interface
# cheio e corta a cauda: 71% das fotos em vez de 100%.
FOTOS_MAX_POR_ANUNCIO = 20

# Onde a ponte do navegador grava o HTML capturado
PONTE_DIR = caminho("html_ponte")
# Onde ficam os HTMLs salvos quando o Cloudflare bloqueia
DEBUG_HTML_DIR = caminho("debug_html")
# Perfil persistente do Playwright (cookies de sessão)
USER_DATA_DIR = caminho("playwright-profile")

# Quantas fotos baixar ao mesmo tempo. O gargalo é a latência da rede (cada
# foto leva ~0,5-1s), não a CPU nem a banda — medido: 8 fotos/s é o teto, e
# ele é atingido já com 16 threads. Subir mais só aumenta o risco de o CDN
# começar a estrangular; 16 fica no joelho da curva.
FOTOS_PARALELO = 16

# ---------------------------------------------------------------------------
# Análise visual das fotos
# ---------------------------------------------------------------------------
# Quem olha as fotos:
#   "claude"   -> o Claude Code CLI (`claude -p`), com a assinatura de quem
#                 está logado. Não usa chave de API nem cobra por token.
#   "deepseek" -> a API da DeepSeek (precisa de DEEPSEEK_API_KEY, cobra por
#                 token; tem teto de gasto em `analisar_visao --teto`).
# Troque sem mexer no código:  $env:CACA_VISAO = "deepseek"
VISAO_PROVEDOR = _os.environ.get("CACA_VISAO", "claude").strip().lower()

# --- Claude Code CLI ---
# Caminho do executável. Vazio = procurar no PATH e, no Windows, na pasta do
# app desktop (%APPDATA%\Claude\claude-code\<versão>\...\claude.exe).
VISAO_CLAUDE_BIN = _os.environ.get("CACA_CLAUDE_BIN", "")
# "sonnet" lê imagem bem e é rápido; "opus" é mais criterioso e mais lento.
VISAO_CLAUDE_MODELO = _os.environ.get("CACA_CLAUDE_MODELO", "sonnet")
# Cada chamada sobe um processo do Claude Code e lê as fotos uma a uma: mais
# lento que a API, e o limite de uso é o da assinatura. Poucos em paralelo.
VISAO_CLAUDE_PARALELO = 3
# Fotos por anúncio no CLI. A DeepSeek recebe todas numa requisição; aqui
# cada foto é uma leitura de arquivo. 12 espalhadas pela galeria (a mediana
# é 12) cobrem fachada, cômodos e o fundo, onde fica o quintal.
VISAO_CLAUDE_MAX_FOTOS = 12
# Segundos até desistir de um anúncio
VISAO_CLAUDE_TIMEOUT_S = 300

# --- DeepSeek ---
# A chave NUNCA vai no código: exporte antes de rodar.
#   PowerShell (sessão atual):  $env:DEEPSEEK_API_KEY = "sk-..."
#   PowerShell (permanente):    setx DEEPSEEK_API_KEY "sk-..."
#   Alternativa: crie um arquivo .env neste diretório (veja .env.exemplo)
DEEPSEEK_ENV_VAR = "DEEPSEEK_API_KEY"

# IMPORTANTE: apenas `deepseek-flash` aceita imagens.
# O `deepseek-v4-pro` responde que vision não é suportado.
VISAO_MODELO = "deepseek-flash"
VISAO_BASE_URL = "https://api.deepseek.com"

# Quantas fotos por anúncio enviar. O modelo julga melhor vendo o conjunto
# (consegue dizer "o quintal aparece nas fotos 3 e 5"), mas cada imagem custa
# tokens — então mantemos um teto.
#
# MEDIDO em 2026-10-03 (não estimado): "low" = 203 tokens/imagem,
# "original" = 458. Com `detail = original`, enviar TODAS as fotos custa
# US$ 2,61 para os 1.849 anúncios dos 3 bairros-alvo — US$ 1,04 mais que
# enviar 12. É irrelevante perto do risco: teto de 12 fotos corta 35% das
# imagens e pode esconder mofo, rachadura ou entulho.
#
# 0 = TODAS as fotos baixadas (sem amostragem).
# ATENÇÃO: `FOTOS_MAX_POR_ANUNCIO` (acima) limita quantas foram BAIXADAS
# (20). Para a maioria (mediana 12) isso é a galeria inteira, mas anúncios
# com 135 fotos na galeria só têm 20 em disco — "todas" significa todas as
# baixadas.
VISAO_MAX_FOTOS = 0
# Com 0 (= todas), a amostragem não faz diferença; mantida para quando
# houver teto. Veja `_amostrar_fotos`.
VISAO_AMOSTRAGEM = True

# Quantos anúncios analisar ao mesmo tempo. O gargalo é a espera da API
# (~3-4s por anúncio), então threads ajudam muito: 6 em paralelo derruba
# ~12 min de fila para ~2 min. O limite de concorrência da DeepSeek para o
# flash é 2500, então 10 é conservador.
VISAO_PARALELO = 10

# "original" mantém a imagem como está (458 tokens/foto, medido).
#
# Foi "low" até 2026-10-04. O "low" reduz para 512x512, e o objetivo da
# análise é justamente ver detalhe fino — mofo, infiltração, rachadura,
# entulho. Nesses casos 512x512 pode perder o que motivou a análise, e
# NUNCA comparamos as duas qualidades no resultado (só no preço). Como a
# diferença para a base inteira é de ~US$ 2, o "original" remove a dúvida.
VISAO_DETALHE = "original"

# Teto de pixels no lado maior antes do envio (economia + velocidade).
# A API já redimensiona, mas mandar menor reduz upload e tempo.
VISAO_LADO_MAX_PX = 1024

# Segundos de espera entre anúncios (evita rajada na API)
VISAO_DELAY_S = 1.0

# Tentativas por anúncio em caso de erro de rede/limite
VISAO_MAX_TENTATIVAS = 3

# Thinking mode: LIGADO por padrão no deepseek-flash. Para extração
# estruturada ele só desperdiça tempo (~50s/anúncio!) e tokens de saída —
# a tarefa é "olhar e preencher campos", não raciocinar. Desligado.
VISAO_THINKING = False
# Esforço de raciocínio, quando o thinking estiver ligado
VISAO_REASONING_EFFORT = "low"

# ---------------------------------------------------------------------------
# Detecção de anúncios duplicados (fotos iguais de imobiliárias diferentes)
# ---------------------------------------------------------------------------
# Cada imobiliária sobe a PRÓPRIA cópia da foto, então comparar URL/ID do CDN
# não acusa nada. O hash perceptual compara a imagem em si.
# Limiar em bits (de 64): quanto maior, mais tolerante a recompressão/corte.
#   5-6  = quase idênticas (recomendado para "mesma foto")
#   10   = tolera leve recorte/brilho (padrão)
#   14+  = começa a dar falso positivo entre imóveis parecidos
DUP_LIMIAR_BITS = 10
# Tamanho do hash (8 => 64 bits; 16 seria mais preciso, porém mais lento)
DUP_HASH_SIZE = 8
# Hashes por anúncio (as primeiras fotos já identificam o imóvel)
DUP_MAX_HASHES_FOTO = 4

