"""Monta um banco PEQUENO e INVENTADO para os testes.

Por que existe: os testes antigos rodavam contra o `imoveis.db` de trabalho
(638 MB, fora do git). Isso tinha dois problemas: não rodavam em outra
máquina nem no CI, e as contas esperadas ("Lapa + Água Branca = 409") mudavam
a cada coleta.

Aqui os dados são sintéticos e determinísticos (semente fixa). Cada caso que
já quebrou o app em produção tem um representante:

  - bairro com acento ("Água Branca", "Jardim Íris") — o LOWER() do SQLite;
  - URL com `?` e com `$` cru — o href cortado no meio;
  - grupo de 25 cópias com o mesmo preço — o selo "mais barata" repetido;
  - grupo de 2 e grupo com variação de preço;
  - quintal "misto" com terra — o filtro "terra batida" vazio;
  - área do anúncio que é o TERRENO — o falso "divergente";
  - valor venal por CEP e por cidade, com CEP deduzido da rua.

Os testes NÃO fixam números mágicos: perguntam ao próprio banco quanto
esperar, e conferem que a tela mostra o mesmo.
"""

from __future__ import annotations

import random
import sqlite3

import migracoes

# bairro -> quantos anúncios "soltos" (sem cópia). Pirituba é grande para a
# listagem ter mais de duas páginas de 60.
BAIRROS = {
    "Lapa": 30,
    "Água Branca": 12,
    "Jardim Íris": 3,
    "Pirituba": 70,
    "Vila Mangalot": 25,
}

PORTAIS = ["zap", "olx", "quintoandar", "imovelweb", "vivareal"]


def _url(i: int, portal: str) -> str:
    """URLs no formato de cada portal, incluindo os casos que já quebraram."""
    if portal == "olx":
        # a OLX põe parâmetro de rastreio: `?` na URL do anúncio
        return f"https://sp.olx.com.br/sao-paulo-e-regiao/imoveis/casa-{i}?lis=listing_{i}"
    if portal == "quintoandar":
        # `$` cru no caminho: legal pela RFC 3986, não precisa de código
        return f"https://www.quintoandar.com.br/imovel/{i}$comprar"
    if portal == "imovelweb":
        return f"https://www.imovelweb.com.br/propriedades/casa-{i}.html"
    dominio = "www.zapimoveis.com.br" if portal == "zap" else "www.vivareal.com.br"
    return f"https://{dominio}/imovel/{i}/"


def _anuncio(rnd: random.Random, i: int, bairro: str) -> dict:
    portal = PORTAIS[i % len(PORTAIS)]
    analisado = rnd.random() < 0.8
    piso = rnd.choice(["grama", "cimento", "misto", "misto", "incerto"])
    a = {
        "url": _url(i, portal),
        "titulo": f"Casa {i} à venda em {bairro}",
        "bairro": bairro,
        "endereco": f"Rua Teste {i % 17}, {bairro}, São Paulo",
        "rua": f"Rua Teste {i % 17}",
        "cep": f"0291{i % 10}-000" if i % 3 else None,
        "preco": float(rnd.randrange(300, 900) * 1000),
        "area_construida": float(rnd.randrange(60, 250)),
        "area_terreno": float(rnd.randrange(80, 500)),
        "quartos": rnd.randint(1, 4),
        "banheiros": rnd.randint(1, 3),
        "vagas": rnd.randint(0, 3),
        "descricao": "Casa térrea com quintal.",
        "portal": portal,
        "fotos_urls": "[]",
        "match_quintal": int(rnd.random() < 0.5),
        "score_quintal": rnd.randint(0, 10),
        "detalhe_ok": 1,
        "aceita_financiamento": rnd.choice([1, 1, 0, None]),
    }
    if analisado:
        a.update({
            "foto_ok": 1,
            "foto_tem_quintal": 1,
            "foto_piso_quintal": piso,
            # quintal "misto" costuma ter terra + um canto cimentado
            "foto_quintal_terra": int(piso == "misto" and rnd.random() < 0.6),
            "foto_arvores": int(rnd.random() < 0.4),
            "foto_cuidado": rnd.randint(1, 5),
            "foto_problemas": rnd.choice([None, None, None, "mofo,infiltracao"]),
            "foto_resumo": "Casa com quintal e boa iluminação.",
            "foto_confianca": rnd.choice(["alta", "media"]),
            "foto_analisada_em": "2026-10-01T10:00:00",
        })
    return a


def _inserir(conn: sqlite3.Connection, tabela: str, linha: dict) -> None:
    cols = ", ".join(linha)
    marcas = ", ".join("?" * len(linha))
    conn.execute(f"INSERT INTO {tabela} ({cols}) VALUES ({marcas})",
                 list(linha.values()))


def criar(caminho: str) -> None:
    """Cria o banco de teste em `caminho` (sobrescreve o conteúdo)."""
    rnd = random.Random(42)
    conn = sqlite3.connect(caminho)
    migracoes.migrar(conn)

    anuncios: list[dict] = []
    i = 0
    for bairro, n in BAIRROS.items():
        for _ in range(n):
            i += 1
            anuncios.append(_anuncio(rnd, i, bairro))

    # --- grupos de cópias (o mesmo imóvel em várias imobiliárias) ---------
    def grupo(gid: int, n: int, bairro: str, precos: list[float]) -> None:
        nonlocal i
        membros = []
        for k in range(n):
            i += 1
            a = _anuncio(rnd, i, bairro)
            a["preco"] = precos[k % len(precos)]
            membros.append(a)
        principal = min(membros, key=lambda m: m["preco"])
        for m in membros:
            m.update({
                "dup_grupo": gid,
                "dup_qtd": n,
                "dup_melhor": int(m is principal),
                "dup_n_fotos": 3,
                "dup_menor_preco": principal["preco"],
            })
        anuncios.extend(membros)

    grupo(1, 25, "Vila Mangalot", [480_000.0])            # 25 iguais
    grupo(2, 2, "Lapa", [520_000.0, 530_000.0])
    grupo(3, 3, "Pirituba", [400_000.0, 470_000.0, 499_000.0])  # varia >5%

    for n, a in enumerate(anuncios):
        _inserir(conn, "anuncios", a)
        for f in range(3):
            _inserir(conn, "fotos", {
                "anuncio_url": a["url"],
                "foto_url": f"https://cdn.exemplo/{n}/{f}.jpg",
                "arquivo_local": f"fotos/teste_{n}/{f:02d}.jpg",
            })

    # --- comparação com o ITBI (85% dos anúncios) -------------------------
    for a in anuncios:
        if rnd.random() > 0.85:
            continue
        razao = round(rnd.uniform(0.6, 1.4), 3)
        mediana = a["preco"] / razao
        _inserir(conn, "comparacoes", {
            "anuncio_url": a["url"], "fonte": "rua+cep", "n_transacoes": 5,
            "n_numeros": 4, "n_financiadas": 2, "metodo": "mediana",
            "area_ref": a["area_construida"], "mediana": mediana,
            "media": mediana, "minimo": mediana * 0.8, "maximo": mediana * 1.2,
            "preco_pedido": a["preco"], "razao": razao,
            "preco_m2_medio": mediana / a["area_construida"],
            "confianca": "media", "calculado_em": "2026-10-06",
            "ano_mais_antigo": 2018, "ano_mais_novo": 2025,
            "indice_reajuste": "igpm", "data_referencia": "202609",
        })
        for t in range(3):
            _inserir(conn, "comparacoes_detalhe", {
                "anuncio_url": a["url"], "itbi_id": t,
                "logradouro": a["rua"], "numero": str(10 + t),
                "bairro": a["bairro"], "cep": a["cep"] or "",
                "data_transacao": f"202{t + 2}0315", "area": a["area_construida"],
                "area_terreno": a["area_terreno"], "valor_corrigido": mediana,
                "preco_m2": mediana / a["area_construida"],
            })

    # --- valor venal (todos) ----------------------------------------------
    for k, a in enumerate(anuncios):
        regiao = ["cep5:02919", "cep4:0291", "cidade"][k % 3]
        _inserir(conn, "valores_venais", {
            "anuncio_url": a["url"], "valor_venal": a["preco"] * 0.4,
            "razao": 0.4, "regiao": regiao, "n_pares": 120,
            "preco": a["preco"], "calculado_em": "2026-10-06",
            "origem_cep": "portal" if a["cep"] else "rua",
        })

    # --- área oficial (metade), com casos de anúncio que publicou o TERRENO -
    for k, a in enumerate(anuncios[::2]):
        terreno = k % 4 == 0
        _inserir(conn, "areas_oficiais", {
            "anuncio_url": a["url"], "nivel": "lote", "n_lotes": 1,
            "area_anuncio": a["area_terreno"] if terreno else a["area_construida"],
            "area_oficial": a["area_construida"],
            "area_terreno_anuncio": a["area_terreno"],
            "area_terreno_oficial": a["area_terreno"],
            "dif_pct": round((a["area_terreno"] / a["area_construida"] - 1) * 100, 1)
                       if terreno else 3.0,
            "logradouro": a["rua"], "numero": "10", "uso": "residencial",
            "calculado_em": "2026-10-06",
            "area_casa": "terreno" if terreno else "construcao",
        })

    conn.commit()
    conn.close()
