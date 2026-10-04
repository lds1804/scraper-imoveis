"""Testes do classificador de tipo de imóvel (casa x apartamento).

Por que existe: a OLX NÃO tem filtro de tipo que funcione. Verificado no site:
  - `/imoveis/venda/estado-sp/sao-paulo-e-regiao/casas?q=X` -> ignorado
    (544 resultados, os mesmos, com apartamentos na 1ª página)
  - `?category=1002`                                       -> ignorado
  - as abas "Casas"/"Apartamentos" da página                -> ignorado (JS)
Então o tipo é classificado pelo TÍTULO. Como isso decide o que entra no
banco, precisa de teste — e há uma armadilha real aqui:

  "Vila Mangalot", "Vila Leopoldina" e "Vila Jaguara" são NOMES DE BAIRRO.
  Se "vila" contar como pista de casa, todo apartamento desses bairros passa
  como casa. Foi exatamente o que aconteceu na 1ª versão.

Roda sem rede e sem banco:
    .venv\\Scripts\\python.exe tests\\testar_tipo.py
"""

from _bootstrap import iniciar
iniciar()  # poe src/ no sys.path e fixa a raiz como diretorio de trabalho

import sys

from scraper_browser import Anuncio, e_casa, tipo_do_anuncio

_falhas: list[str] = []
_total = 0


def verificar(titulo: str, esperado: str, url: str = "") -> None:
    global _total
    _total += 1
    a = Anuncio(url=url or "https://sp.olx.com.br/x/imoveis/anuncio-12345678",
                titulo=titulo)
    got = tipo_do_anuncio(a)
    if got == esperado:
        print(f"  PASSA  {titulo[:50]:52s} -> {got}")
    else:
        print(f"  FALHA  {titulo[:50]:52s} -> {got}  (esperado {esperado})")
        _falhas.append(f"{titulo[:50]} -> {got} (esperado {esperado})")


def main() -> int:
    print("\n=== 1) Apartamentos (o que deve ser DESCARTADO) ===")
    verificar("Apartamento à Venda - Vila Mangalot, 3 Quartos", "apartamento")
    verificar("APARTAMENTO À VENDA VILA MANGALOT", "apartamento")
    verificar("Ótimo apartamento à venda na Vila Mangalot", "apartamento")
    verificar("Apartamento para venda em Vila Mangalot com 3 quartos", "apartamento")
    verificar("Apartamento em Vila Leopoldina", "apartamento")
    verificar("Apartamento 2 quartos Vila Jaguara", "apartamento")
    verificar("Cobertura duplex à venda", "apartamento")
    verificar("Kitnet mobiliada no centro", "apartamento")
    verificar("Studio novo na Lapa", "apartamento")
    verificar("Flat mobiliado com serviço", "apartamento")
    verificar("Loft moderno em Pinheiros", "apartamento")
    # contém as duas palavras: o apartamento é o tipo real
    verificar("Apartamento em condomínio de casas", "apartamento")

    print("\n=== 2) Casas (o que deve ser MANTIDO) ===")
    verificar("Casa para venda em Vila Mangalot com 4 quartos", "casa")
    verificar("Sobrado à venda no Parque Maria Domitila", "casa")
    verificar("Lindo sobrado novo à venda", "casa")
    verificar("Casa térrea com quintal", "casa")
    verificar("Casa de vila 120m² reformada", "casa")
    verificar("Casa residencial em São Paulo", "casa")
    verificar("Chácara com pomar em Pirituba", "casa")

    print("\n=== 3) Armadilha: 'Vila' e nome de BAIRRO, nao de imovel ===")
    # estes sao apartamentos cujo titulo cita um bairro "Vila X"
    verificar("Apartamento à venda Vila Mangalot", "apartamento")
    verificar("Apartamento novo em Vila Leopoldina", "apartamento")
    verificar("Apartamento 3 quartos na Vila Jaguara", "apartamento")
    # e estes sao casas nesses mesmos bairros (nao podem ser perdidas)
    verificar("Casa térrea na Vila Mangalot", "casa")
    verificar("Sobrado na Vila Leopoldina", "casa")

    print("\n=== 4) Incerto (mantido, para nao perder casa por engano) ===")
    # titulos sem tipo explicito: a decisao e nao descartar
    verificar("Excelente oportunidade na vila mangalot!", "incerto")
    verificar("Imóvel para venda com 285 metros quadrados", "incerto")
    verificar("OPORTUNIDADE CITY AMÉRICA POR APENAS 3 MESES", "incerto")

    print("\n=== 5) Fallback pela URL quando o titulo nao diz ===")
    # o slug da URL traz o tipo: "-apartamento-...-12345678"
    verificar("Sem tipo no titulo",
              "apartamento",
              url="https://sp.olx.com.br/sao-paulo-e-regiao/imoveis/"
                  "venda-apartamento-sao-caetano-do-sul-centro-1460188398")
    verificar("Sem tipo no titulo",
              "casa",
              url="https://sp.olx.com.br/sao-paulo-e-regiao/imoveis/"
                  "casa-para-venda-em-vila-mangalot-1540642325")

    print("\n=== 6) e_casa(): incerto conta como casa ===")
    global _total
    for titulo, esperado in [
        ("Casa simples", True),
        ("Apartamento grande", False),
        ("Imóvel sem tipo informado", True),  # incerto nao e descartado
    ]:
        _total += 1
        got = e_casa(Anuncio(url="https://x/i/a-12345678", titulo=titulo))
        if got == esperado:
            print(f"  PASSA  {titulo[:44]:46s} -> {got}")
        else:
            print(f"  FALHA  {titulo[:44]:46s} -> {got} (esperado {esperado})")
            _falhas.append(f"e_casa({titulo}) -> {got}")

    print("\n" + "=" * 58)
    if _falhas:
        print(f"{len(_falhas)} FALHA(S) de {_total} verificações:")
        for f in _falhas:
            print(f"  - {f}")
        return 1
    print(f"TODAS AS {_total} VERIFICAÇÕES PASSARAM")
    return 0


if __name__ == "__main__":
    sys.exit(main())
