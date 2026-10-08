"""Scraper do Imovelweb usando Playwright (navegador real).

O site bloqueia requisições simples (requests) com HTTP 403.
Um Chromium real via Playwright passa e renderiza o DOM, dando
seletores confiáveis baseados em data-qa="POSTING_CARD_*".

Seletores descobertos (2025):
  card        -> [data-qa="posting PROPERTY"]
  link        -> card[data-to-posting]
  preço       -> [data-qa="POSTING_CARD_PRICE"]
  features     -> [data-qa="POSTING_CARD_FEATURES"]
  localização -> [data-qa="POSTING_CARD_LOCATION"]
  descrição   -> [data-qa="POSTING_CARD_DESCRIPTION"]
  fotos       -> [data-qa="POSTING_CARD_GALLERY"] img
"""

from __future__ import annotations

import json
import random
import re
import time
from collections.abc import Iterator
from dataclasses import dataclass, field

from bs4 import BeautifulSoup

from cacaimoveis import config, logs

log = logs.obter(__name__)

CARD_SELECTOR = '[data-qa="posting PROPERTY"]'

# Seletores da página individual do anúncio (descobertos em 2025)
DETALHE_DESCRICAO = '[class*="description-module__wrapper-description"]'
DETALHE_H1 = "h1"
DETALHE_ENDERECO = '[class*="location-address"]'

# Códigos de característica do Imovelweb (bloco `mainFeatures`)
CFT_AREA_TOTAL = "CFT100"      # "tot."  -> área total / construída
CFT_AREA_UTIL = "CFT101"       # "útil"  -> área útil
CFT_QUARTOS = "CFT2"
CFT_BANHEIROS = "CFT3"
CFT_SUITES = "CFT4"
CFT_VAGAS = "CFT7"

# Sinais de que o anúncio aceita financiamento.
# Na listagem o site mostra a pílula "Melhor financiamento"
# (classe `pills-module__pill-item-span`).
PILL_FINANCIAMENTO = "melhor financiamento"
PALAVRAS_FINANCIA = (
    "aceita financiamento",
    "financiamento",
    "financiável",
    "financiavel",
    "aceita financia",
)
# Sinais de que NÃO aceita (tem prioridade sobre os positivos)
PALAVRAS_SEM_FINANCIA = (
    "não aceita financiamento",
    "nao aceita financiamento",
    "sem financiamento",
    "não financia",
    "nao financia",
    "somente à vista",
    "somente a vista",
)


# ---------------------------------------------------------------------------
# Modelo
# ---------------------------------------------------------------------------
@dataclass
class Anuncio:
    url: str
    titulo: str = ""
    bairro: str = ""
    # `endereco` guarda "Bairro, Cidade" — é o que valida a localidade.
    endereco: str = ""
    # `rua` é o logradouro, quando o layout informa (campo só de exibição).
    rua: str = ""
    preco: float | None = None
    area_construida: float | None = None
    area_terreno: float | None = None
    quartos: int | None = None
    banheiros: int | None = None
    vagas: int | None = None
    descricao: str = ""
    portal: str = "imovelweb"
    fotos_urls: list[str] = field(default_factory=list)
    match_quintal: bool = False
    score_quintal: int = 0
    # True/False quando o anúncio deixa claro; None quando não informa
    aceita_financiamento: bool | None = None
    # CEP do imóvel. Só a OLX informa (no JSON-LD); útil para conferir a
    # localidade quando o texto do bairro é ambíguo.
    cep: str = ""
    # Quantos anúncios existem para este MESMO imóvel, segundo o próprio site
    # (campo `listingsCount` do ZAP/VivaReal). 1 = sem repetição conhecida.
    # É sinal direto para o agrupamento de duplicatas.
    listing_count: int = 1
    # Suítes (o ZAP/VivaReal não separa; o QuintoAndar informa)
    suites: int | None = None
    # Comodidades cruas como o portal informa (ex.: "POOL", "VISTA_LIVRE").
    # Ficam fora do banco por enquanto; a análise visual cobre o que importa.
    amenities: list[str] = field(default_factory=list)

    def to_row(self) -> tuple:
        return (
            self.url,
            self.titulo,
            self.bairro,
            self.endereco,
            self.rua,
            self.cep,
            self.preco,
            self.area_construida,
            self.area_terreno,
            self.quartos,
            self.banheiros,
            self.vagas,
            self.descricao,
            self.portal,
            ",".join(self.fotos_urls),
            int(self.match_quintal),
            self.score_quintal,
        )


# ---------------------------------------------------------------------------
# Helpers de parsing
# ---------------------------------------------------------------------------
def _para_float(valor: str | None) -> float | None:
    if not valor:
        return None
    m = re.search(r"([\d\.]+)(?:,(\d{2}))?", valor)
    if not m:
        return None
    inteiro = m.group(1).replace(".", "")
    centavos = m.group(2) or "00"
    try:
        return float(f"{inteiro}.{centavos}")
    except ValueError:
        return None


def _para_int(valor: str | None) -> int | None:
    f = _para_float(valor)
    return int(f) if f is not None else None


def montar_url(bairro: str, pagina: int = 1) -> str:
    """Monta a URL de busca no formato aceito pelo site.

    Formato correto (confirmado abrindo as URLs no navegador):
        /casas-venda-<bairro>-<cidade>-<uf>.html
        /casas-venda-parque-sao-domingos-sao-paulo-sp.html

    O título da página denuncia se o bairro foi reconhecido:
      "Casas à venda NO Parque São Domingos, São Paulo"  -> OK
      "Casas à venda em São Paulo, SP OU <bairro>"       -> bairro não reconhecido
      "Casas à venda no Brasil"                          -> fallback nacional

    ATENÇÃO ao formato `tipo-cidade-uf-bairro` (cidade antes do bairro): ele
    NÃO filtra o bairro — devolve a cidade inteira (381.953 imóveis).
    """
    slug = f"{config.TIPO}-{config.OPERACAO}-{bairro}-{config.CIDADE}-{config.UF}"
    if pagina > 1:
        slug += f"-pagina-{pagina}"
    return f"{config.BASE_URL}/{slug}.html"


# ---------------------------------------------------------------------------
# Validação de localidade
# ---------------------------------------------------------------------------
def _sem_acento(texto: str) -> str:
    import unicodedata

    nfkd = unicodedata.normalize("NFKD", texto or "")
    return "".join(c for c in nfkd if not unicodedata.combining(c))


def cidade_do_endereco(endereco: str) -> str:
    """Extrai a cidade do endereço ('Bairro, Cidade' -> 'Cidade')."""
    if not endereco:
        return ""
    return endereco.rsplit(",", 1)[-1].strip() if "," in endereco else endereco.strip()


def bairro_do_endereco(endereco: str) -> str:
    """Extrai o bairro real do endereço ('Bairro, Cidade' -> 'Bairro').

    O `bairro` do banco é o bairro que a BUSCA pedia — que pode estar errado
    quando o site cai em fallback. Este é o bairro que veio no anúncio.
    """
    if not endereco:
        return ""
    return endereco.split(",", 1)[0].strip()


# ---------------------------------------------------------------------------
# O título denuncia o "fallback nacional"; o bairro se confirma pelos anúncios
# ---------------------------------------------------------------------------
_RE_TITULO_QTD = re.compile(r"([\d.]+)\s*(?:Casas|Imóveis|Apartamentos)", re.I)
_RE_TITULO_CIDADE = re.compile(r"\b(?:em|no)\s+([^,]+?)(?:,\s*(?:SP|[A-Z]{2}))?\s*$", re.I)


def e_fallback_nacional(titulo: str) -> bool:
    """True quando o título é o fallback genérico do site.

    Ex.: "1.672.720 Casas à venda no Brasil - Imovelweb"

    Atenção: o título NÃO permite concluir que o bairro foi filtrado. O site
    escreve "em São Paulo, SP **ou** Vila Mangalot" mesmo quando os resultados
    são 100% do bairro (verificado: 29/29). O que importa é a ausência da
    cidade esperada — o resto se valida pelos próprios anúncios.
    """
    if not titulo:
        return False
    return not re.search(r"s[ãa]o paulo", titulo, re.I)


def _normalizar_busca(texto: str) -> str:
    """minúsculas, sem acento, sem pontuação e sem hífen — para comparar bairros.

    O hífen vira espaço ANTES da limpeza (senão 'vila-mangalot' viraria
    'vilamangalot' e não casaria com 'Vila Mangalot').
    """
    t = _sem_acento(texto).lower().replace("-", " ")
    t = re.sub(r"[^a-z0-9 ]", " ", t)
    return re.sub(r"\s+", " ", t).strip()


def bairro_confere(endereco: str, bairro_esperado: str) -> bool:
    """True se o bairro do endereço bate com o buscado (comparação tolerante).

    Aceita tanto o slug (`parque-sao-domingos`) quanto o nome
    (`Parque São Domingos`), ignorando acento e caixa, e tolera
    singular/plural (`domingo` x `domingos`).
    """
    real = _normalizar_busca(bairro_do_endereco(endereco))
    alvo = _normalizar_busca(bairro_esperado)
    if not real or not alvo:
        return True  # sem dado: não descarta
    if real == alvo:
        return True

    p_real, p_alvo = set(real.split()), set(alvo.split())

    # 1) subconjunto exato de palavras
    if p_alvo.issubset(p_real) or p_real.issubset(p_alvo):
        return True

    # 2) palavra a palavra, tolerando plural: 'domingo' casa com 'domingos'
    def casa(a: str, b: str) -> bool:
        return a.startswith(b) or b.startswith(a)

    if len(p_real) == len(p_alvo):
        return all(casa(r, a) for r, a in zip(sorted(p_real), sorted(p_alvo), strict=True))
    return False


def e_bairro_alvo(anuncio: Anuncio, alvos=None) -> tuple[bool, str]:
    """True se o anúncio é de algum bairro da lista-alvo (config.BAIRROS).

    Rede de segurança contra o que aconteceu com `parque-sao-domingo`: a URL
    errada devolvia anúncios de SP, mas de OUTROS bairros (Chácara Belenzinho,
    Jardim Gonzaga…). Filtram a cidade e mesmo assim entravam no banco.

    Retorna (é_alvo, nome_do_alvo_ou_bairro_encontrado).
    """
    lista = config.BAIRROS if alvos is None else alvos
    encontrado = bairro_do_endereco(anuncio.endereco)
    for b in lista:
        if bairro_confere(anuncio.endereco, b.slug) or bairro_confere(
            anuncio.endereco, b.nome
        ):
            return True, b.nome
    return False, encontrado


def e_de_sao_paulo(anuncio: Anuncio) -> bool:
    """True se o anúncio é da cidade/UF esperada (config).

    O site às vezes devolve resultados de outras cidades (fallback nacional),
    principalmente quando a URL do bairro não é reconhecida.
    """
    cidade = _sem_acento(cidade_do_endereco(anuncio.endereco)).lower()
    if not cidade:
        return True  # sem endereço: não descarta (usuário revisa)

    aceitas = [_sem_acento(c).lower() for c in config.CIDADES_ACEITAS]
    if cidade in aceitas:
        return True
    # aceita variações "sao paulo - sp", "sao paulo/sp"
    return any(cidade.startswith(a) for a in aceitas)


# ---------------------------------------------------------------------------
# Tipo do imóvel: descarta apartamentos
# ---------------------------------------------------------------------------
# O Imovelweb filtra o tipo pela própria URL (`/casas-venda-...`), mas a OLX
# NÃO tem filtro de tipo que funcione. Testado no site:
#   - `/imoveis/venda/estado-sp/sao-paulo-e-regiao/casas?q=...` -> ignorado
#     (544 resultados, os mesmos de antes, com 4 apartamentos na 1ª página)
#   - `?category=1002` / `?category=1020`            -> ignorado
#   - as abas "Casas"/"Apartamentos" da página        -> ignorado (só JS)
# Então a classificação é feita pelo TÍTULO do anúncio, que é confiável:
# "Apartamento à Venda - ...", "Casa para venda em ...", "Sobrado à venda ...".
#
# A URL NÃO serve como fonte primária: 749 dos 926 anúncios da OLX têm slug
# começando por palavra de marketing ("otimo-", "lindo-", "excelente-"), então
# o tipo só aparece no meio dela. O título acerta.
_TIPOS_APARTAMENTO = (
    "apartamento",
    "kitnet",
    "kit net",
    "studio",
    "stúdio",
    "loft",
    "flat",
    "cobertura",
    "garden",
    "quitnete",
)
_TIPOS_CASA = (
    "casa",
    "sobrado",
    "sobradinho",
    "térrea",
    "terrea",
    "casa de vila",
    "casa de condomínio",
    "casa de condominio",
    "chácara",
    "chacara",
)
# NOTA: "vila" sozinho NÃO entra na lista acima. Em São Paulo, "Vila X" é
# nome de bairro (Vila Mangalot, Vila Jaguara, Vila Leopoldina), então a
# palavra apareceria em todo anúncio desses bairros e classificaria
# apartamentos como casas. Só "casa de vila" (a expressão completa) conta.


def tipo_do_anuncio(anuncio: Anuncio) -> str:
    """Classifica o imóvel em 'casa', 'apartamento' ou 'incerto'.

    Olha o título e, se ele não disser, o slug da URL. Ordem importa: checa
    APARTAMENTO antes de CASA, porque "Apartamento em condomínio de casas"
    contém as duas palavras e o apartamento é o tipo real.

    Devolve 'incerto' quando nada é reconhecido — quem chama decide o que
    fazer (aqui, mantém o anúncio, para não perder uma casa por engano).
    """
    texto = _normalizar_busca(f"{anuncio.titulo or ''} {anuncio.url or ''}")

    for pista in _TIPOS_APARTAMENTO:
        if _normalizar_busca(pista) in texto:
            return "apartamento"
    for pista in _TIPOS_CASA:
        if _normalizar_busca(pista) in texto:
            return "casa"
    return "incerto"


def e_casa(anuncio: Anuncio) -> bool:
    """True se o anúncio NÃO é apartamento (casa ou tipo desconhecido)."""
    return tipo_do_anuncio(anuncio) != "apartamento"


# Alguns alts começam com um hash + separador: "1aa4c134...a8 · Título"
# O separador pode ser '·' (U+00B7), '—', '-' ou espaço.
_RE_HASH_TITULO = re.compile(r"^[0-9a-f]{16,}[^0-9a-zA-Z]*")


def _limpar_titulo(alt: str | None) -> str:
    """Limpa o alt de uma imagem para virar título legível."""
    if not alt:
        return ""
    t = _RE_HASH_TITULO.sub("", alt).strip()
    return re.sub(r"\s+", " ", t)


def _normalizar_numeros(texto: str) -> str:
    """Corrige separador com espaço: '166, 32' -> '166,32'."""
    return re.sub(r"(\d),\s+(\d)", r"\1,\2", texto)


def _area_terreno_da_descricao(texto: str | None) -> float | None:
    """Extrai a área do terreno do texto do anúncio.

    Cobre as variações mais comuns nos anúncios do Imovelweb:
      "terreno de 350m²"        "um terreno generoso de 350 m2"
      "O terreno possui 220 m²" "terreno com 308m²"
      "520 mts de terreno"      "250 metros de terreno"
      "Terreno: 330 m²"         "166, 32 m² de terreno"
      "lote de 300 m²"
      "10 metros de frente por 22 metros de profundidade"  -> 220 m²
      "12 x 25 metros"                                     -> 300 m²
    """
    if not texto:
        return None
    t = _normalizar_numeros(texto)
    num = r"(\d+(?:[.,]\d+)*)"
    unim = r"(?:m²|m2|mts|metros?)"
    padroes = (
        # "terreno ... 350m²"  /  "terreno: 330 m²"
        rf"terreno[^\d]{{0,30}}?{num}\s*{unim}",
        # "520 mts de terreno" / "250 m² de terreno"
        rf"{num}\s*{unim}\s*(?:d[eoa]s?\s*)?terreno",
        # "lote de 300 m²"  /  "lote: 360m2"
        rf"lote[^\d]{{0,20}}?{num}\s*{unim}",
        # "12 x 25 metros" / "10x25m"
        rf"{num}\s*[x×]\s*(\d+(?:[.,]\d+)*)\s*{unim}",
    )
    for p in padroes:
        m = re.search(p, t, re.IGNORECASE)
        if m:
            if m.lastindex and m.lastindex >= 2 and m.group(2):
                # padrão "frente x fundo" -> multiplica
                frente = _para_float(m.group(1))
                fundo = _para_float(m.group(2))
                if frente and fundo:
                    valor = frente * fundo
                    if 20 <= valor <= 100_000:
                        return valor
                continue
            valor = _para_float(m.group(1))
            # sanidade: terrenos entre 20 e 100.000 m²
            if valor and 20 <= valor <= 100_000:
                return valor

    # "10 metros de frente por 22 metros de profundidade" -> 220 m²
    m = re.search(
        r"(\d+(?:[.,]\d+)*)\s*(?:m|metros?)?\s*de\s*frente\s*(?:por|e|x|×)\s*"
        r"(\d+(?:[.,]\d+)*)\s*(?:m|metros?)?\s*(?:de\s*(?:profundidade|fundo))?",
        t,
        re.IGNORECASE,
    )
    if m:
        frente = _para_float(m.group(1))
        fundo = _para_float(m.group(2))
        if frente and fundo:
            valor = frente * fundo
            if 20 <= valor <= 100_000:
                return valor

    return None


def _area_construida_da_descricao(texto: str | None) -> float | None:
    """Extrai a área construída do texto (ex: '220m² de área construída')."""
    if not texto:
        return None
    t = _normalizar_numeros(texto)
    padroes = (
        r"(\d+(?:[.,]\d+)*)\s*m[²2]\s*de\s*(?:área\s*)?constru[íi]",
        r"(\d+(?:[.,]\d+)*)\s*m[²2]\s*de\s*constru[çc]",
        r"constru[íi]d[ao][^\d]{0,15}?(\d+(?:[.,]\d+)*)\s*m[²2]",
        r"constru[çc][ãa]o[^\d]{0,15}?(\d+(?:[.,]\d+)*)\s*m[²2]",
    )
    for p in padroes:
        m = re.search(p, t, re.IGNORECASE)
        if m:
            return _para_float(m.group(1))
    return None


def _titulo_da_url(url: str) -> str:
    """Gera um título legível a partir do slug da URL (fallback).

    Ex.: .../casa-3-quartos-a-venda-122-m-...-3000246816.html ->
         'Casa 3 quartos a venda 122 m ...'
    """
    m = re.search(r"/propriedades/([^/?#]+)\.html", url)
    if not m:
        return ""
    slug = m.group(1)
    slug = re.sub(r"-a?-\d{6,}$", "", slug)
    slug = re.sub(r"-\d{6,}$", "", slug)
    return re.sub(r"[-_]+", " ", slug).strip().capitalize()


# ---------------------------------------------------------------------------
# Parsing da listagem
# ---------------------------------------------------------------------------
def _url_foto_alta_res(url: str) -> str:
    """Troca a resolução 360x266 por 1200x900 (ou similar) na CDN."""
    return url.replace("/360x266/", "/1200x900/")


# Ícones/placeholder do próprio site que não são fotos de imóvel
_RE_FOTO_INVALIDA = re.compile(r"(right-arrow|left-arrow|arrow|logo|placeholder|sprite)", re.I)


def _e_foto_valida(url: str) -> bool:
    """Descarta vetores do site e placeholders que aparecem na galeria."""
    if not url.startswith("http"):
        return False
    if url.lower().endswith(".svg"):
        return False
    return not _RE_FOTO_INVALIDA.search(url)


def url_canonica(url: str) -> str:
    """A URL do anúncio sem os parâmetros de rastreamento.

    O link do card traz `?n_src=Listado&n_pg=1&n_pos=3&n_search_id=<sessão>`:
    posição na lista e id da sessão de busca, que MUDAM a cada coleta. Guardar
    a URL crua fazia o mesmo anúncio parecer novo em toda rodada (linha
    duplicada e galeria baixada de novo). O identificador do anúncio está no
    caminho (`...-3009994367.html`); nada na query é preciso para abri-lo.
    """
    return url.split("#")[0].split("?")[0]


def parse_cards(
    html: str,
    bairro: str,
    filtrar_cidade: bool = True,
    exigir_bairro_alvo: bool = False,
) -> list[Anuncio]:
    """Extrai todos os anúncios da página de listagem.

    Com `filtrar_cidade=True` (padrão) descarta anúncios que não são da
    cidade/UF esperada. Isso protege contra o "fallback nacional" do site:
    quando o slug do bairro não é reconhecido, ele devolve resultados de
    qualquer cidade do país.

    Com `exigir_bairro_alvo=True` vai além: exige que o anúncio seja de um
    bairro da lista `config.BAIRROS`. Protege contra o caso observado com
    `parque-sao-domingo`, em que a URL errada devolvia anúncios de São Paulo,
    mas de outros bairros (Chácara Belenzinho, Jardim Gonzaga…).
    """
    soup = BeautifulSoup(html, "html.parser")
    cards = soup.select(CARD_SELECTOR)
    anuncios: list[Anuncio] = []
    descartados = 0
    fora_do_alvo = 0

    for card in cards:
        a = Anuncio(url="")
        a.bairro = bairro

        # URL do anúncio
        href = card.get("data-to-posting", "")
        if not href:
            link = card.find("a", href=True)
            href = link["href"] if link else ""
        if href:
            a.url = url_canonica(href if href.startswith("http") else config.BASE_URL + href)
        else:
            continue

        # Preço
        el = card.select_one('[data-qa="POSTING_CARD_PRICE"]')
        if el:
            a.preco = _para_float(el.get_text(" ", strip=True))

        # Features: "111 m² tot. 3 quartos 4 ban. 3 vagas"
        el = card.select_one('[data-qa="POSTING_CARD_FEATURES"]')
        texto_feat = el.get_text(" ", strip=True) if el else ""

        m_area = re.search(r"(\d+(?:[.,]\d+)*)\s*m[²2]", texto_feat)
        if m_area:
            a.area_construida = _para_float(m_area.group(1))

        m_q = re.search(r"(\d+)\s*quart", texto_feat, re.IGNORECASE)
        if m_q:
            a.quartos = int(m_q.group(1))

        # "4 ban." ou "1 banheiro"
        m_b = re.search(r"(\d+)\s*ban", texto_feat, re.IGNORECASE)
        if m_b:
            a.banheiros = int(m_b.group(1))

        m_v = re.search(r"(\d+)\s*vaga", texto_feat, re.IGNORECASE)
        if m_v:
            a.vagas = int(m_v.group(1))

        # Rua (logradouro) — campo de exibição; o layout nem sempre traz.
        el = card.select_one(".postingLocations-module__location-address")
        if el:
            a.rua = el.get_text(" ", strip=True)

        # Localização "Bairro, Cidade" — é o que valida cidade/bairro.
        # IMPORTANTE: `location-address` é só a RUA; o bairro/cidade está em
        # `location-text`. Pegar o address aqui fazia a validação rejeitar
        # tudo, porque "Rua Palamedes" não é nenhuma cidade conhecida.
        el = card.select_one(".postingLocations-module__location-text")
        if not el:
            el = card.select_one('[data-qa="POSTING_CARD_LOCATION"]')
        if el:
            a.endereco = el.get_text(" ", strip=True)
        # fallback: usa o bloco completo (rua + bairro, cidade)
        if not a.endereco:
            el = card.select_one('[class*="location-block"]')
            if el:
                a.endereco = el.get_text(" ", strip=True)

        # Descrição
        el = card.select_one('[data-qa="POSTING_CARD_DESCRIPTION"]')
        if el:
            a.descricao = el.get_text(" ", strip=True)

        # Pílulas do card: trazem "Melhor financiamento", "Quintal" etc.
        pills = " ".join(
            p.get_text(" ", strip=True)
            for p in card.select('[class*="pill-item"], [class*="pills-module"]')
        )

        # Fotos + título (o título real fica no alt da 1ª imagem da galeria)
        galeria = card.select_one('[data-qa="POSTING_CARD_GALLERY"]')
        fotos: list[str] = []
        if galeria:
            for img in galeria.find_all("img"):
                alt = _limpar_titulo(img.get("alt"))
                if alt and not a.titulo:
                    a.titulo = alt
                src = (
                    img.get("src")
                    or img.get("data-flickity-lazyload")
                    or img.get("data-src")
                    or ""
                )
                if _e_foto_valida(src) and src not in fotos:
                    fotos.append(_url_foto_alta_res(src))
        a.fotos_urls = fotos

        # Áreas a partir do texto da descrição (o terreno nem sempre está no card)
        terreno = _area_terreno_da_descricao(a.descricao)
        if terreno is not None:
            a.area_terreno = terreno
        construida = _area_construida_da_descricao(a.descricao)
        if construida is not None:
            a.area_construida = construida

        # Aceita financiamento? (pílula oficial da listagem tem prioridade)
        calcular_financiamento(a, pills)

        # Fallback de título a partir do slug da URL
        if not a.titulo:
            a.titulo = _titulo_da_url(a.url)

        # Descarta resultados de outras cidades (fallback nacional do site)
        if filtrar_cidade and not e_de_sao_paulo(a):
            descartados += 1
            continue

        # Opcional: exige que seja de um bairro da lista-alvo
        if exigir_bairro_alvo:
            ok, _ = e_bairro_alvo(a)
            if not ok:
                fora_do_alvo += 1
                continue

        anuncios.append(a)

    if descartados:
        print(f"  [filtro] {descartados} anúncio(s) de outra cidade descartado(s).")
    if fora_do_alvo:
        print(f"  [filtro] {fora_do_alvo} anúncio(s) de bairro fora da lista descartado(s).")

    return anuncios


# ---------------------------------------------------------------------------
# Parsing da página individual do anúncio
# ---------------------------------------------------------------------------
_RE_MAINFEATURES_INI = re.compile(r"const mainFeatures = \s*")
_JSON_DECODER = json.JSONDecoder()


def _main_features(html: str) -> dict[str, str]:
    """Lê o bloco JSON `mainFeatures` (fonte confiável das características).

    Ex.: {"CFT100":{"label":"tot.","value":"111"}, ...}
    Retorna um mapa simples {featureId: value}.
    """
    m = _RE_MAINFEATURES_INI.search(html)
    if not m:
        return {}
    try:
        # raw_decode lê o objeto balanceado a partir do início
        dados, _ = _JSON_DECODER.raw_decode(html[m.end():])
    except json.JSONDecodeError:
        return {}
    if not isinstance(dados, dict):
        return {}
    return {
        k: str(v.get("value"))
        for k, v in dados.items()
        if isinstance(v, dict) and v.get("value") is not None
    }


def _int_feature(features: dict[str, str], chave: str) -> int | None:
    v = features.get(chave)
    if v is None:
        return None
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return None


_RE_PICTURES_INI = re.compile(r"['\"]pictures['\"]\s*:\s*\[")
_RE_PICTURE_ITEM = re.compile(r"\{[^{}]*\}")
_RE_PICTURE_ORDEM = re.compile(r'"order"\s*:\s*(\d+)')
_RE_PICTURE_URL = re.compile(
    r'"(?:url1200x1200|resizeUrl1200x1200|url730x532|resizeUrl720x532)"\s*:\s*"([^"]+)"'
)


def _pictures_do_html(html: str) -> list[str]:
    """Extrai a galeria COMPLETA do anúncio a partir do JS embutido.

    A página individual traz um objeto JS com `'pictures': [ {...}, ... ]`,
    e cada item tem a URL em várias resoluções. A listagem, por outro lado,
    só publica uma prévia — por isso muitos anúncios ficam com 1 foto só.

    Retorna as URLs na ordem original do anúncio (campo `order`).
    """
    m = _RE_PICTURES_INI.search(html)
    if not m:
        return []

    # encontra o ']' que fecha o array, equilibrando os colchetes
    inicio = m.end() - 1
    profundidade = 0
    fim = None
    for j in range(inicio, min(len(html), inicio + 2_000_000)):
        c = html[j]
        if c == "[":
            profundidade += 1
        elif c == "]":
            profundidade -= 1
            if profundidade == 0:
                fim = j
                break
    if fim is None:
        return []

    bloco = html[inicio : fim + 1]

    itens: list[tuple[int, str]] = []
    vistos: set[str] = set()
    for obj in _RE_PICTURE_ITEM.finditer(bloco):
        s = obj.group(0)
        url_m = _RE_PICTURE_URL.search(s)
        if not url_m:
            continue
        ordem_m = _RE_PICTURE_ORDEM.search(s)
        ordem = int(ordem_m.group(1)) if ordem_m else len(itens)
        url = url_m.group(1)

        # dedup pelo nome do arquivo (as resoluções variam, a foto é a mesma)
        chave = url.split("?")[0].rsplit("/", 1)[-1]
        if chave in vistos:
            continue
        vistos.add(chave)

        itens.append((ordem, _url_foto_media(url)))

    itens.sort(key=lambda x: x[0])
    return [u for _, u in itens]


_RE_FOTO_TAMANHO = re.compile(r"/(?:\d+x\d+)/(?=[^/]+$)")


def _url_foto_media(url: str) -> str:
    """Ajusta a URL para um tamanho bom de exibição (1200x900).

    O layout do CDN é `.../<pastas>/<LARGURAxALTURA>/<arquivo>.jpg`, então o
    tamanho fica IMEDIATAMENTE antes do nome do arquivo.
    """
    limpa = url.split("?")[0]
    if _RE_FOTO_TAMANHO.search(limpa):
        return _RE_FOTO_TAMANHO.sub("/1200x900/", limpa)
    # sem tamanho na URL: insere antes do nome do arquivo
    pasta, _, arquivo = limpa.rpartition("/")
    return f"{pasta}/1200x900/{arquivo}"


def _float_feature(features: dict[str, str], chave: str) -> float | None:
    v = features.get(chave)
    if v is None:
        return None
    try:
        return float(v.replace(",", "."))
    except (AttributeError, ValueError):
        return None


def parse_detalhe(html: str, anuncio: Anuncio) -> Anuncio:
    """Completa/atualiza um Anuncio com os dados da página individual.

    A página de detalhe tem a descrição COMPLETA (a listagem só traz um
    resumo truncado), e é nela que o terreno costuma aparecer.
    """
    soup = BeautifulSoup(html, "html.parser")

    # 1) Descrição completa
    el = soup.select_one(DETALHE_DESCRICAO)
    if el:
        texto = re.sub(r"\s+", " ", el.get_text(" ", strip=True)).strip()
        if len(texto) > len(anuncio.descricao or ""):
            anuncio.descricao = texto

    # Pílulas da página (também trazem "Melhor financiamento")
    pills = " ".join(
        p.get_text(" ", strip=True)
        for p in soup.select('[class*="pill-item"], [class*="pills-module"]')
    )

    # 2) Características estruturadas (fonte mais confiável)
    feats = _main_features(html)

    area_total = _float_feature(feats, CFT_AREA_TOTAL)
    area_util = _float_feature(feats, CFT_AREA_UTIL)
    if area_total or area_util:
        # a listagem usa "m² tot." -> area_total é melhor referência
        anuncio.area_construida = area_total or area_util

    for chave, attr in (
        (CFT_QUARTOS, "quartos"),
        (CFT_BANHEIROS, "banheiros"),
        (CFT_VAGAS, "vagas"),
    ):
        valor = _int_feature(feats, chave)
        if valor is not None:
            setattr(anuncio, attr, valor)

    # 3) Terreno: tenta o texto (descrição completa) e, se houver,
    #    a meta description (que às vezes resume "520 mts de terreno").
    terreno = _area_terreno_da_descricao(anuncio.descricao)
    if terreno is None:
        meta = soup.find("meta", attrs={"name": "description"})
        if meta:
            terreno = _area_terreno_da_descricao(meta.get("content", ""))
    if terreno is not None:
        anuncio.area_terreno = terreno

    # 4) Área construída (fallback pela descrição completa)
    construida = _area_construida_da_descricao(anuncio.descricao)
    if construida is not None:
        anuncio.area_construida = construida

    # 5) Título / endereço (a página tem versões mais completas)
    h1 = soup.select_one(DETALHE_H1)
    if h1:
        t = re.sub(r"\s+", " ", h1.get_text(" ", strip=True)).strip()
        # remove o preço do fim: "... por R$ 770.000 - Vila Mangalot ..."
        t = re.sub(r"\s+por\s+R\$\s*[\d.,]+\s*$", "", t).strip(" -–—")
        if len(t) > len(anuncio.titulo or ""):
            anuncio.titulo = t

    # 6) Galeria completa (a listagem só traz uma prévia de 1 foto)
    pictures = _pictures_do_html(html)
    if len(pictures) > len(anuncio.fotos_urls or []):
        anuncio.fotos_urls = pictures

    # 7) Financiamento: pílula + descrição completa
    if anuncio.aceita_financiamento is not True:
        calcular_financiamento(anuncio, pills)

    return anuncio


# ---------------------------------------------------------------------------
# Filtros
# ---------------------------------------------------------------------------
def calcular_match_quintal(a: Anuncio) -> None:
    texto = (a.descricao + " " + a.titulo).lower()
    score = sum(1 for p in config.PALAVRAS_QUINTAL if p in texto)
    score -= 2 * sum(1 for n in config.PALAVRAS_NEGATIVAS if n in texto)
    a.score_quintal = score
    a.match_quintal = score > 0


# Verbos/expressões que indicam aceite quando perto de "financiamento"
VERBOS_ACEITE = (
    "aceita",
    "aceitamos",
    "possibilita",
    "permite",
    "viabiliza",
    "sujeito a",
    "aprovado",
    "combinar",
    "disponível para",
    "disponivel para",
    "faz",
    "trabalhamos com",
)
# Ruído de propaganda: a palavra aparece, mas não é sobre ACEITAR
RUIDO_FINANCIA = (
    "simule",
    "simulação",
    "simulacao",
    "simulador",
    "melhores taxas",
    "consiga",
    "documentação necessária",
    "documentacao necessaria",
    "como conseguir",
    "saiba mais",
)


def _analisa_financiamento(texto: str) -> bool | None:
    """Analisa o texto por SENTENÇA (não por regex entre frases).

    Motivo: as frases são compostas — "Aceita permuta. Financiamento a
    combinar com o proprietário." Um regex que atravessa o ponto erra.
    """
    # separa em sentenças preservando o conteúdo
    sentencas = [s.strip() for s in re.split(r"[.;\n!?]", texto) if s.strip()]

    for i, sent in enumerate(sentencas):
        if "financ" not in sent:
            continue

        # negação explícita na própria sentença
        if any(n in sent for n in PALAVRAS_SEM_FINANCIA):
            return False

        # propaganda ("simule seu financiamento") não decide nada
        if any(r in sent for r in RUIDO_FINANCIA):
            continue

        # verbo de aceite na mesma sentença
        if any(v in sent for v in VERBOS_ACEITE):
            return True

        # ou na sentença imediatamente anterior
        if i > 0 and any(v in sentencas[i - 1] for v in VERBOS_ACEITE):
            if not any(n in sentencas[i - 1] for n in PALAVRAS_SEM_FINANCIA):
                return True

    # "financiável" é inequívoco onde aparecer
    if "financiável" in texto or "financiavel" in texto:
        return True

    return None


def calcular_financiamento(a: Anuncio, pills: str = "") -> None:
    """Define `aceita_financiamento` a partir do texto disponível.

    Ordem de confiança:
      1. Pílula oficial da listagem ("Melhor financiamento") -> True
      2. Negativas explícitas ("não aceita financiamento")   -> False
      3. Texto com verbo de aceite perto de "financiamento"  -> True
      4. Nada                                                -> None

    Cuidado: "financiamento" sozinho aparece em propaganda
    ("Simule seu financiamento"), que NÃO significa aceite.
    """
    if pills and PILL_FINANCIAMENTO in pills.lower():
        a.aceita_financiamento = True
        return

    texto = f"{a.titulo} {a.descricao}".lower()
    if not texto.strip():
        return

    if any(n in texto for n in PALAVRAS_SEM_FINANCIA):
        a.aceita_financiamento = False
        return

    if not any(p in texto for p in PALAVRAS_FINANCIA):
        return

    resultado = _analisa_financiamento(texto)
    if resultado is not None:
        a.aceita_financiamento = resultado


def atende_preco(a: Anuncio) -> bool:
    if a.preco is None:
        return True  # não descarta sem preço; usuário revisa
    if config.PRECO_MAX and a.preco > config.PRECO_MAX:
        return False
    if config.PRECO_MIN and a.preco < config.PRECO_MIN:
        return False
    return True


# ---------------------------------------------------------------------------
# Coleta (Playwright)
# ---------------------------------------------------------------------------
def _sleep() -> None:
    time.sleep(random.uniform(config.DELAY_MIN, config.DELAY_MAX))


def _e_challenge(page) -> bool:
    """Detecta a página de desafio anti-bot (Cloudflare 'Um momento...')."""
    try:
        title = (page.title() or "").lower()
    except Exception:  # noqa: BLE001
        return False
    sinais = ["um momento", "just a moment", "atenção", "verificação", "attent"]
    return any(s in title for s in sinais)


class CloudflareBloqueou(RuntimeError):
    """O Cloudflare barrou várias aberturas seguidas: insistir não adianta."""


# aberturas de listagem bloqueadas desde o último sucesso
_bloqueios_seguidos = 0


def _abrir_pagina_listagem(page, url: str) -> str | None:
    """Abre a URL de listagem tratando o Cloudflare. Devolve o HTML ou None."""
    resp = None
    for tentativa in range(1, config.MAX_RETRIES + 1):
        try:
            resp = page.goto(url, wait_until="domcontentloaded", timeout=60000)
        except Exception as e:  # noqa: BLE001
            log.warning("erro tratado, a execução segue: %s", e, exc_info=True)
            print(f"  [erro] falha ao abrir: {e}")
            time.sleep(5 * tentativa)
            continue

        if _e_challenge(page):
            print(f"  [cloudflare] desafio detectado (tent. {tentativa}), aguardando...")
            page.wait_for_timeout(config.CHALLENGE_TIMEOUT_MS)

        if resp and resp.status < 400 and not _e_challenge(page):
            page.wait_for_timeout(3000)
            global _bloqueios_seguidos
            _bloqueios_seguidos = 0
            return page.content()

        status = resp.status if resp else "?"
        print(f"  [tentativa {tentativa}] status {status}, retry...")
        page.wait_for_timeout(5000)

    _bloqueios_seguidos += 1
    if _bloqueios_seguidos >= config.IMOVELWEB_MAX_BLOQUEIOS:
        raise CloudflareBloqueou(
            f"{_bloqueios_seguidos} aberturas seguidas bloqueadas pelo Cloudflare")
    return None


def coletar_bairro(page, alvo, incremental=None) -> Iterator[Anuncio]:
    """Itera pelas páginas de um bairro e gera Anuncios (já parseados).

    `alvo` pode ser um `config.Bairro` ou uma string.

    `incremental` (ver `incremental.py`): para de paginar quando duas páginas
    seguidas só trazem anúncio já visto. Sem isto o Imovelweb abria SEMPRE as 4
    páginas de cada bairro (78 aberturas, ~17 min só de espera, medido em
    2026-10-07), enquanto ZAP, QuintoAndar e OLX já paravam cedo.

    Antes de coletar, confere o <title> da página: se o bairro não foi
    reconhecido (título diz "em São Paulo, SP **ou** <bairro>", ou é um
    fallback nacional), tenta a próxima grafia configurada. Se nenhuma
    funcionar, o bairro é pulado em vez de trazer lixo.
    """
    if isinstance(alvo, str):
        candidatos = [alvo]
        nome = alvo
    else:
        candidatos = alvo.todos_slugs
        nome = alvo.nome

    slug = None
    primeira_pagina: list[Anuncio] | None = None

    for candidato in candidatos:
        url = montar_url(candidato, 1)
        print(f"[bairro] {nome} | testando grafia {candidato!r}")
        print(f"  {url}")

        html = _abrir_pagina_listagem(page, url)
        if html is None:
            print("  [falhou] bloqueio do Cloudflare. Tentando próxima grafia.")
            continue

        titulo = ""
        try:
            titulo = page.title() or ""
        except Exception:  # noqa: BLE001
            pass

        if e_fallback_nacional(titulo):
            print(f"  [fallback nacional] título: {titulo[:70]!r}")
            continue

        anuncios = parse_cards(html, candidato, filtrar_cidade=True)
        if not anuncios:
            print("  [vazio] nenhum anúncio de São Paulo nesta página.")
            continue

        # O título não confirma o bairro (o site escreve "ou <bairro>" mesmo
        # quando acerta). Quem confirma são os endereços dos próprios anúncios.
        do_bairro = sum(1 for a in anuncios if bairro_confere(a.endereco, candidato))
        proporcao = do_bairro / len(anuncios)

        if proporcao < 0.5:
            print(
                f"  [não confere] só {do_bairro}/{len(anuncios)} anúncios são deste "
                f"bairro (título: {titulo[:45]!r}). Tentando próxima grafia."
            )
            continue

        print(
            f"  [ok] {len(anuncios)} anúncios em São Paulo, "
            f"{do_bairro} do bairro ({proporcao:.0%})."
        )
        slug = candidato
        primeira_pagina = anuncios
        break

    if slug is None or primeira_pagina is None:
        print(f"[bairro] {nome}: nenhuma grafia foi reconhecida. Pulando.")
        return

    if incremental:
        incremental.novo_bairro()

    for pagina in range(1, (config.MAX_PAGINAS or 1) + 1):
        if pagina == 1:
            anuncios = primeira_pagina
        else:
            url = montar_url(slug, pagina)
            print(f"[bairro] {nome} | página {pagina}: {url}")

            resp = None
            for tentativa in range(1, config.MAX_RETRIES + 1):
                try:
                    resp = page.goto(url, wait_until="domcontentloaded", timeout=60000)
                except Exception as e:  # noqa: BLE001
                    log.warning("erro tratado, a execução segue: %s", e, exc_info=True)
                    print(f"  [erro] falha ao abrir: {e}")
                    time.sleep(5 * tentativa)
                    continue

                if _e_challenge(page):
                    print(f"  [cloudflare] desafio detectado (tent. {tentativa}), aguardando...")
                    page.wait_for_timeout(config.CHALLENGE_TIMEOUT_MS)

                if resp and resp.status < 400 and not _e_challenge(page):
                    break

                status = resp.status if resp else "?"
                print(f"  [tentativa {tentativa}] status {status}, retry...")
                page.wait_for_timeout(5000)
            else:
                print("  [falhou] não consegui passar pelo bloqueio. Pulando bairro.")
                # conta no disjuntor, como a abertura da 1ª página
                global _bloqueios_seguidos
                _bloqueios_seguidos += 1
                if _bloqueios_seguidos >= config.IMOVELWEB_MAX_BLOQUEIOS:
                    raise CloudflareBloqueou(
                        f"{_bloqueios_seguidos} aberturas seguidas bloqueadas pelo Cloudflare")
                break

            page.wait_for_timeout(3000)
            anuncios = parse_cards(page.content(), slug)

        if not anuncios:
            print("  [aviso] nenhum card encontrado nesta página.")
            break

        if pagina > 1:
            print(f"  {len(anuncios)} anúncios encontrados.")

        urls_pagina = [a.url for a in anuncios]
        n_ineditos = incremental.novos(urls_pagina) if incremental else 0

        for a in anuncios:
            # guarda o bairro REAL que veio no anúncio (pode diferir do buscado)
            a.bairro = bairro_do_endereco(a.endereco) or nome
            yield a

        if incremental:
            # só depois de o consumidor processar a página inteira: se a coleta
            # cair no meio, ela não é dada como vista
            incremental.registrar(urls_pagina)
            if incremental.pode_parar(n_ineditos):
                print(f"  [incremental] {incremental.paginas_sem_novos} páginas seguidas "
                      f"sem anúncio novo; encerrando {nome}.")
                break

        _sleep()
