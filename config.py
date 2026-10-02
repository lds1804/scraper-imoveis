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

# Preço máximo (R$) -> filtro na própria busca
# Nota: City América é um bairro de alto padrão (mínimo ~R$ 1,75 mi). Com o teto
# antigo de R$ 900 mil, TODOS os anúncios de lá eram descartados.
PRECO_MAX = 10_000_000
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
# Rede / polite scraping
# ---------------------------------------------------------------------------
BASE_URL = "https://www.imovelweb.com.br"

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

# Número máximo de páginas de resultados por bairro (None = sem limite)
MAX_PAGINAS = 20

# ---------------------------------------------------------------------------
# Armazenamento local
# ---------------------------------------------------------------------------
DB_PATH = "imoveis.db"
FOTOS_DIR = "fotos"

# Quantas fotos baixar ao mesmo tempo. O gargalo do download é a latência da
# rede (cada foto leva ~0,5-1s), não a CPU — então threads ajudam muito.
# 8 é um meio-termo: derruba o tempo de ~1 foto/s para ~5-8 fotos/s sem
# sobrecarregar o CDN nem arriscar bloqueio.
FOTOS_PARALELO = 8

# ---------------------------------------------------------------------------
# Análise visual das fotos (DeepSeek)
# ---------------------------------------------------------------------------
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
# ~650 tokens — então mantemos um teto.
# Quantas fotos vão por requisição para o modelo de visão.
# Com a galeria completa o anúncio tem até 50 fotos; mandar todas custaria
# caro e não melhoraria a resposta. 12 cobre bem fachada, ambientes e quintal.
VISAO_MAX_FOTOS = 12
# Espalha as fotos escolhidas pela galeria em vez de pegar as N primeiras
# (o quintal costuma estar no FIM da galeria). Veja `_amostrar_fotos`.
VISAO_AMOSTRAGEM = True

# Quantos anúncios analisar ao mesmo tempo. O gargalo é a espera da API
# (~3-4s por anúncio), então threads ajudam muito: 6 em paralelo derruba
# ~12 min de fila para ~2 min. Não subir demais para não esbarrar no
# limite de requisições por minuto da DeepSeek.
VISAO_PARALELO = 6

# "low" reduz para 512x512 (~180 tokens/foto): suficiente para "tem quintal
# com terra?". Use "original" se precisar ler texto na imagem (placa, planta).
VISAO_DETALHE = "low"

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

