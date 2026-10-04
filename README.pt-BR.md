# Scraper Imovelweb

> ⚠️ **Documento histórico.** Este README descreve uma versão anterior do
> projeto: os camelôs `config.py`/`main.py` ainda estão na raiz, mas o código
> de produção foi movido para `src/` e os testes para `tests/`. Os comandos
> abaixo continuam funcionando (há atalhos na raiz). Para a documentação
> atual, veja o [README.md](README.md), em inglês.

Coleta anúncios de **casas à venda** na **Vila Mangalot** e **Parque São Domingo**
(São Paulo), filtra por preço (até R$ 1 milhão) e área de terreno (> 200 m²),
e tenta identificar imóveis com quintal/terra. Salva tudo localmente (SQLite + fotos).

## Instalação

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

## Uso

**1. Testar o acesso antes de tudo (recomendado):**

```powershell
python main.py --dry-run
```

Isso acessa a 1ª página de cada bairro e mostra quantos links de anúncios
foram encontrados. Se der problema de bloqueio, o HTML é salvo em `debug_html/`.

**2. Coleta completa:**

```powershell
python main.py                                # todos os bairros
python main.py --bairros city-america lapa    # só estes (slug ou nome)
python main.py --listar                       # ver os bairros disponíveis
```

> **Preço:** o teto é `PRECO_MAX` em `config.py` (hoje R$ 1 mi). Bairros de
> alto padrão — como **City América**, cujo anúncio mais barato é ~R$ 1,75 mi —
> têm **todos** os imóveis descartados com um teto baixo, e isso acontece em
> silêncio. Ao adicionar um bairro novo, confira a faixa de preço dele.

**3. Enriquecer com a página individual de cada anúncio (recomendado):**

```powershell
python enriquecer_detalhes.py --headless
```

A listagem do Imovelweb só traz um resumo truncado da descrição e quase
nunca informa a área do terreno. Este script abre a **página individual**
de cada anúncio para pegar:

- descrição completa (de onde sai a área de terreno)
- quartos, banheiros, vagas e áreas do bloco `mainFeatures`
- título completo (do `h1`)
- **galeria completa de fotos** (a listagem publica só 1 prévia; a página
  traz o array `pictures` com todas)

Opções:

| Flag | Efeito |
|---|---|
| *(padrão)* | só anúncios ainda sem `area_terreno` |
| `--todos` | inclui os que já têm terreno |
| `--limite N` | processa no máximo N anúncios |
| `--refazer` | ignora a marca de já visitado |
| `--headless` | sem janela do navegador (mais rápido) |
| `--sem-fotos` | não baixar as galerias completas |
| `--delay-min/--delay-max` | intervalo entre páginas (segundos) |

O progresso fica salvo na coluna `detalhe_ok`, então pode interromper com
Ctrl+C e rodar de novo depois — ele retoma de onde parou.

> ⚠️ **Rate limit:** após ~60 páginas individuais seguidas o Cloudflare passa
> a bloquear todas as requisições por várias horas (não adianta trocar entre
> headless/headful). Planeje lotes de ~50 e dê uma pausa longa.

**4. Ver a interface web:**

```powershell
python webapp.py
# abra http://127.0.0.1:5000
```

**5. Testar as rotas do webapp:**

```powershell
python tests\testar_web.py          # não precisa do servidor rodando
python tools\conferir_web.py        # precisa do servidor rodando
```

## Estrutura

```
src/                    código de produção
  config.py             bairros, preços, palavras-chave, limites
  scraper_browser.py    Playwright: parsing da listagem E da página individual
  storage.py            SQLite + download de fotos
  visao.py              biblioteca da análise visual
  progresso.py          barra de progresso
  main.py               orquestração da coleta
  enriquecer_detalhes.py  completa os dados via página individual
  webapp.py             servidor Flask da interface web
tests/                  testes (rodam direto, sem servidor)
  testar_web.py         rotas e filtros do webapp
  conferir_web.py       teste das rotas do webapp
tools/                  ferramentas de debug
  inspecionar_detalhe.py  dump de uma página de detalhe
antigo/                 substituídos, mantidos como referência
web/                    templates e CSS da interface
dados/                  saídas geradas (ignorado)
imoveis.db              banco gerado (SQLite, ignorado)
fotos/                  fotos baixadas (uma pasta por anúncio, ignorado)
```

## Avisos importantes

- **URL de busca (crítico):** o formato é
  `tipo-operacao-BAIRRO-cidade-uf`:
  `/casas-venda-parque-sao-domingos-sao-paulo-sp.html`.
  Duas armadilhas confirmadas:
  1. **Grafia errada cai no fallback.** `parque-sao-domingo` (sem o "s")
     devolve *"1.672.720 Casas no Brasil"*. O correto é `parque-sao-domingos`.
  2. **Ordem invertida não filtra.** `casas-venda-sao-paulo-sp-<bairro>.html`
     é aceito, mas devolve a **cidade inteira** (381.953 imóveis), não o bairro.

  O `<title>` não confirma o bairro — o site escreve *"em São Paulo, SP **ou**
  Vila Mangalot"* mesmo quando acerta 29/29. Ele só serve para detectar o
  fallback nacional. Quem confirma o bairro é `bairro_confere()`, comparando
  com os endereços dos anúncios; `coletar_bairro` testa as grafias alternativas
  e pula o bairro se nenhuma conferir.
- **Auditoria de localidade:** `python auditar_localidade.py` mostra os
  anúncios fora de SP; `--limpar` remove, `--corrigir-bairros` reescreve a
  coluna `bairro` com o bairro real do endereço.
- **Anti-bot:** o Imovelweb tem proteção (Cloudflare). As requisições simples
  (403) não passam — é necessário um Chromium real via Playwright com
  **perfil persistente** (`playwright-profile/`), que guarda os cookies do
  desafio entre execuções. Resolva o desafio **uma vez** em modo visível
  (`HEADLESS = False` em `config.py`) e depois pode rodar headless.
- **Seletores:** as classes CSS do site mudam. Se os links não forem
  encontrados, ajuste `parse_cards()` em `scraper_browser.py`. Para inspecionar
  uma página de detalhe, use `python inspecionar_detalhe.py` (gera
  `debug_detalhe.html` e `insp_detalhe.txt`).
- **Etiqueta:** respire entre requisições (rate limiting já configurado em
  `config.py`). Leia os Termos de Uso do site. Uso pessoal.
- **Filtro "quintal com terra":** puramente heurístico por palavras-chave.
  Revise manualmente (campo `match_quintal` e `score_quintal` no banco).
- **Aceita financiamento:** o site não tem campo estruturado. Três fontes, em
  ordem de confiança:
  1. Pílula oficial da listagem ("Melhor financiamento") → `True`
  2. Negativa explícita ("não aceita financiamento", "somente à vista") → `False`
  3. Análise do texto por **sentença** — "financiamento" sozinho não basta,
     pois aparece em propaganda ("Simule seu financiamento"), que não é aceite

  `None` = não informado (a maioria, pois a listagem trunca a descrição).
  Rodar `enriquecer_detalhes.py` melhora bastante a cobertura, porque analisa
  a descrição completa.
- **Área de terreno:** o site **não tem campo estruturado** de terreno — o
  valor só aparece quando o anunciante cita no texto da descrição. Por isso
  a cobertura é naturalmente baixa.
