"""Normalização de endereço para ligar anúncios à base do ITBI.

Por que existe: as duas fontes escrevem o MESMO endereço de formas
diferentes, e sem normalizar não há como cruzar nada.

    ITBI       -> 'AV ALEXIOS JAFET'         CEP '5662000'   (7 dígitos)
    anúncio    -> 'Avenida Alexios Jafet'    CEP '05715100'  (8 dígitos)

As três diferenças que precisam ser resolvidas:

  1. TIPO DE VIA abreviado. O ITBI usa 'R', 'AV', 'AL', 'PC', 'TRV', 'EST';
     os anúncios escrevem 'Rua', 'Avenida', 'Alameda', 'Praça', 'Travessa',
     'Estrada'. Normalizar para a forma CANÔNICA (a curta, do ITBI) faz os
     dois lados convergirem.

  2. CEP com zero à esquerda. O ITBI guarda o CEP como número (7 dígitos);
     o anúncio guarda como texto de 8. Preencher com zeros à esquerda.

  3. NÚMERO no fim do logradouro do anúncio ('Rua Estêvão Ribeiro,142').
     O número é separado aqui, porque a comparação é por RUA (o ITBI tem o
     número em campo próprio, e casa-lo exigiria precisão que os anúncios
     não têm — muitos omitem).

Também importa o que NÃO fazer: comparar o logradouro inteiro é frágil
demais. 'Rua Santo' casaria com 'Rua Santo Antônio' e 'Rua Santo Amaro'.
A comparação usa o nome normalizado completo, com as palavras em ordem,
e exige bater o nome todo.
"""

from __future__ import annotations

import re
import unicodedata

# Tipo de via: várias grafias -> forma canônica curta (a que o ITBI usa).
# A ordem importa: os mais longos primeiro, senão 'TRAV' casaria antes de 'TV'.
_TIPOS_VIA = [
    ("AVENIDA", "AV"),
    ("AVEN", "AV"),
    ("ESTRADA", "EST"),
    ("ALAMEDA", "AL"),
    ("TRAVESSA", "TRV"),
    ("RODOVIA", "ROD"),
    ("VIELA", "VL"),
    ("PRACA", "PC"),
    ("PRAÇA", "PC"),
    ("LARGO", "LGO"),
    ("VIADUTO", "VD"),
    ("VILA", "VL"),
    ("RUA", "R"),
    ("AV", "AV"),
    ("AL", "AL"),
    ("PC", "PC"),
    ("R", "R"),
]

# palavras ignoradas na comparação (não distinguem a via)
_VAZIAS = {"DE", "DA", "DO", "DAS", "DOS", "E"}


def sem_acento(texto: str) -> str:
    t = unicodedata.normalize("NFKD", str(texto or ""))
    return "".join(c for c in t if not unicodedata.combining(c))


def normalizar_cep(cep) -> str:
    """Devolve o CEP com 8 dígitos, ou '' se não der.

    O ITBI guarda como número (perde o zero à esquerda): 5662000 -> 05662000.
    """
    if cep is None:
        return ""
    digitos = re.sub(r"\D", "", str(cep))
    if not digitos:
        return ""
    if len(digitos) == 8:
        return digitos
    if len(digitos) == 7:
        return "0" + digitos
    # 5 dígitos: alguns registros antigos vêm truncados; não é confiável
    return ""


def normalizar_logradouro(rua: str) -> str:
    """Nome de via canônico, maiúsculo, sem acento e sem número.

    'Rua Estêvão Ribeiro, 142'  -> 'R ESTEVAO RIBEIRO'
    'AV ALEXIOS JAFET'          -> 'AV ALEXIOS JAFET'
    """
    if not rua:
        return ""

    t = sem_acento(str(rua)).upper()

    # separa um número no fim (padrão dos anúncios: "Rua X, 142" ou "Rua X 142")
    t = re.sub(r"[,;]?\s*\d+\s*[A-Z]?$", "", t)
    t = re.sub(r"[^A-Z0-9 ]", " ", t)
    t = re.sub(r"\s+", " ", t).strip()
    if not t:
        return ""

    palavras = t.split()

    # tipo de via -> forma canônica
    if palavras:
        for grafia, canonico in _TIPOS_VIA:
            if palavras[0] == grafia:
                palavras[0] = canonico
                break
        else:
            # sem tipo explícito ('ARAGUAIA'): assume R, que é o padrão do ITBI
            pass

    # remove partículas ('R DE ESTEVAO' -> 'R ESTEVAO')
    limpo = [p for p in palavras if p not in _VAZIAS]
    return " ".join(limpo) if limpo else t


def chave_rua(rua: str) -> str:
    """Chave de comparação por logradouro.

    Igual a `normalizar_logradouro`, mas SEM o tipo de via: 'AV PAULISTA',
    'R PAULISTA' e 'AL PAULISTA' são logradouros distintos na cidade, mas
    o anúncio às vezes erra o tipo. Tirar o tipo aumenta bastante o número
    de casamentos, ao custo de um risco pequeno de confundir vias de mesmo
    nome em tipos diferentes (raro: 'Rua X' e 'Avenida X' convivendo).

    Na prática o CEP desempata: duas vias de mesmo nome em CEPs diferentes
    caem em baldes diferentes na comparação em cascata.
    """
    t = normalizar_logradouro(rua)
    if not t:
        return ""
    palavras = t.split()
    if palavras and palavras[0] in ("R", "AV", "AL", "PC", "TRV", "EST", "ROD",
                                    "VL", "LGO", "VD"):
        palavras = palavras[1:]
    return " ".join(palavras)


# ---------------------------------------------------------------------------
# Abreviações PRÓPRIAS DO CADASTRO DA PREFEITURA (GeoSampa)
# ---------------------------------------------------------------------------
# Não são as mesmas do ITBI. Sem expandir, o casamento falha:
#
#     anúncio                      GeoSampa
#     General Charles de Gaulle -> GAL CHARLES DE GAULLE
#     Cônego José Salomon       -> CON JOSE SALOMON
#     Comendador Feiz Zarzur    -> COMEN FEIZ ZARZUR
#
# Descoberto medindo: 440 dos 742 logradouros voltaram vazios na primeira
# ingestão, e estes três estavam entre eles.
_ABREVIACOES_CADASTRO = {
    "GAL": "GENERAL",
    "CON": "CONEGO",
    "COMEN": "COMENDADOR",
    "COMEND": "COMENDADOR",
    "PROF": "PROFESSOR",
    "PROFA": "PROFESSORA",
    "MAL": "MARECHAL",
    "BRIG": "BRIGADEIRO",
    "CEL": "CORONEL",
    "TEN": "TENENTE",
    "CAP": "CAPITAO",
    "SEN": "SENADOR",
    "DEP": "DEPUTADO",
    "DES": "DESEMBARGADOR",
    "MIN": "MINISTRO",
    "PRES": "PRESIDENTE",
    "ENG": "ENGENHEIRO",
    "ARQ": "ARQUITETO",
    "PE": "PADRE",
    "STA": "SANTA",
    "STO": "SANTO",
}


def chave_tolerante(rua: str) -> str:
    """Chave que sobrevive às abreviações do cadastro da prefeitura.

    A `chave_rua()` compara o MESMO logradouro escrito por fontes que
    abrevia diferente. Esta versão expande as abreviações conhecidas do
    cadastro (GAL -> GENERAL, CON -> CONEGO, COMEN -> COMENDADOR) e depois
    tira o tipo de via — igual à `chave_rua()`.

    Usar `chave_rua()` cru não basta porque o cadastro MANTÉM partículas
    ('AV DIOGENES RIBEIRO DE LIMA') enquanto nós as removemos; como as duas
    pontas passam por esta mesma função, o casamento acontece.
    """
    t = normalizar_logradouro(rua)
    if not t:
        return ""
    palavras = [_ABREVIACOES_CADASTRO.get(p, p) for p in t.split()]
    if palavras and palavras[0] in ("R", "AV", "AL", "PC", "TRV", "EST", "ROD",
                                    "VL", "LGO", "VD"):
        palavras = palavras[1:]
    return " ".join(palavras)


def termo_de_busca(rua: str) -> str:
    """Palavra mais distintiva do logradouro, para consultar o WFS.

    Do outro lado do HTTP não dá para filtrar por `chave_tolerante` — só com
    LIKE por substring. Usar o nome inteiro falha (as abreviações divergem) e
    usar a primeira palavra devolve centenas de ruas ('Rua José ...' são
    milhares).

    A escolha é a palavra MAIS LONGA entre as que não são título/patente,
    que na prática é a mais distintiva: em 'General Charles de Gaulle' dá
    CHARLES, não GENERAL.
    """
    t = chave_tolerante(rua)
    if not t:
        return ""
    palavras = [p for p in t.split() if len(p) >= 3]
    if not palavras:
        return ""
    patentes = {"GENERAL", "MARECHAL", "BRIGADEIRO", "CORONEL", "TENENTE",
                "CAPITAO", "SENADOR", "DEPUTADO", "PROFESSOR", "PROFESSORA",
                "PRESIDENTE", "MINISTRO", "ENGENHEIRO", "ARQUITETO",
                "COMENDADOR", "DESEMBARGADOR", "DOUTOR", "PADRE"}
    especificos = [p for p in palavras if p not in patentes]
    return max(especificos or palavras, key=len)


def cep_do_bairro(bairro: str) -> str:
    """Devolve o bairro normalizado como chave de região.

    Existe para servir de nível mais fino de agrupamento quando não há CEP
    NEM rua, que é o caso de 1.980 anúncios (todos da OLX, mais alguns do
    Imovelweb): esses portais não publicam o CEP e o anúncio não traz a rua.
    O bairro, porém, existe em 100% dos anúncios.

    O nome é só normalizado (maiúsculo, sem acento, sem pontuação) — quem
    casa as duas pontas é a MESMA função, então não importa a grafia.
    """
    if not bairro:
        return ""
    t = sem_acento(str(bairro)).upper()
    t = re.sub(r"[^A-Z0-9 ]", " ", t)
    return re.sub(r"\s+", " ", t).strip()


def numero_do_logradouro(rua: str) -> str:
    """Extrai o número do imóvel do campo `rua` do anúncio.

    Serve para casar o LOTE EXATO no cadastro fiscal do GeoSampa, em vez de
    usar a rua inteira.

    ATENÇÃO ao que NÃO pode ser confundido: o nome da via costuma terminar em
    número que NÃO é endereço.

        'Rua Doutor Odon Carlos de Figueiredo, 120'  -> '120'   (endereço)
        'Rua Duque de Caxias'                        -> '120'?  NÃO — 'Caxias'
        'Rua Nove de Julho'                          -> '9'?    NÃO

    Por isso só aceito número depois de VÍRGULA ou espaço, com 1 a 5 dígitos,
    e que seja o ÚLTIMO token da string. 'Rua Duque de Caxias' não termina em
    dígito, então devolve ''. Já o caso ambíguo 'Rua Vinte e Três de Maio'
    também não termina em dígito — está escrito por extenso, que é o padrão
    dos logradouros brasileiros.
    """
    if not rua:
        return ""
    t = str(rua).strip()
    m = re.search(r"[,]?\s+(\d{1,5})\s*([A-Za-z])?\s*$", t)
    if not m:
        return ""
    numero = m.group(1)
    letra = (m.group(2) or "").upper()
    # número 0 não existe como endereço; veio de truncamento
    return "" if numero == "0" else numero + letra


def resumo() -> None:
    """Mostra a normalização em exemplos reais (para conferir)."""
    casos = [
        ("Rua Estêvão Ribeiro, 142", "R ESTEVAO RIBEIRO"),
        ("Rua Estevão Ribeiro", "R ESTEVAO RIBEIRO"),
        ("AV ALEXIOS JAFET", "AV ALEXIOS JAFET"),
        ("Avenida Alexios Jafet", "AV ALEXIOS JAFET"),
        ("R SEN OTAVIO MANGABEIRA", "R SEN OTAVIO MANGABEIRA"),
        ("Rua Deputado Salvador Julianelli", "R DEPUTADO SALVADOR JULIANELLI"),
        ("AL DOS TUPINAS", "AL DOS TUPINAS"),
        ("Alameda dos Tupinas", "AL TUPINAS"),
    ]
    print(f"{'entrada':44s} {'normalizado':32s} {'chave(rua)'}")
    print("-" * 104)
    for entrada, esperado in casos:
        norm = normalizar_logradouro(entrada)
        marca = "" if norm == esperado else f"  <- esperado {esperado!r}"
        print(f"{entrada[:42]:44s} {norm[:30]:32s} {chave_rua(entrada)[:26]}{marca}")

    print("\nCEP:")
    for c in (5662000, "05662000", "05133-004", "5133004", 513300):
        print(f"   {str(c):12s} -> {normalizar_cep(c) or '(invalido)'}")


if __name__ == "__main__":
    import sys

    if "--teste" in sys.argv:
        resumo()
    else:
        print(__doc__)
