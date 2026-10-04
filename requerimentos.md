# Requerimentos — projeto Caça-imóveis

> Lista de tarefas do projeto, organizada por tema.
> Legenda: ✅ feito · 🔶 parcial · ⬜ pendente
>
> Última revisão: 2026-10-03

---

## 1. Coleta de dados (crawlers)

- [x] **Crawler do Imovelweb** funcionando
      Playwright com contexto persistente para passar pelo Cloudflare.
      Coleta 4 bairros: Vila Mangalot, Parque São Domingos, City América e
      Parque Maria Domitila — **178 anúncios**.
- [x] **Crawler da OLX** nos mesmos bairros.
      Playwright (a OLX devolve 403 para `requests`). A OLX **não aceita
      filtro de tipo** por nenhum caminho, então o filtro é feito depois.
- [x] **Enriquecimento pela página de detalhe**
      Descrição completa, quartos/banheiros/vagas, financiamento e a
      **galeria completa** (a listagem publica só 1 foto de capa; o detalhe
      traz até 50). Resultado: **4.756 fotos** armazenadas.
- [x] **Validação de localidade**
      Todo anúncio salvo é de São Paulo **e** de um dos bairros-alvo —
      evita tanto o fallback nacional do site quanto bairro errado.
- [x] **Ampliar a lista de bairros**
      **21 bairros configurados** e 4 portais coletando (Imovelweb, OLX,
      ZAP/VivaReal — mesmo inventário — e QuintoAndar).
- [ ] **Rodar o crawler diariamente**, de forma automática
      (ver pipeline na AWS, item 5).
- [x] **Retirar os apartamentos da busca**
      `tipo_do_anuncio()` classifica por título e URL. **472 apartamentos
      removidos** (1.104 → 632). Armadilha encontrada: `vila` é **nome de
      bairro** (Vila Mangalot, Vila Leopoldina), não tipo de imóvel —
      classificava todo apartamento desses bairros como casa.
- [x] **Filtro de preço máximo**
      `PRECO_MAX = 1.000.000`, verificado nos 4 níveis: `config`,
      `atende_preco()`, o `priceMax` da API e o SQLite final.

## 2. Enriquecimento com IA (DeepSeek)

- [x] **Atributos extraídos das FOTOS** (visão computacional)
      quintal, piso do quintal (terra/grama/cimento/misto), árvores,
      vegetação, iluminação, arejamento, conservação, janelas grandes,
      fachada, reforma, piso interno, cômodos, extras (piscina,
      churrasqueira, edícula, varanda, área gourmet…) e **problemas**
      (mofo, infiltração, entulho, obra inacabada, abandono).
      **178/178 anúncios analisados, 0 falhas.**
- [x] **Análise do texto da descrição** para quintal e financiamento
      Resolvido com **regras determinísticas** (palavras-chave + análise
      por frase) em vez de LLM: mais barato, auditável e reproduzível.
- [x] **Cruzamento texto × foto**
      **37 anúncios (21%) têm problema visível nas fotos que o texto não
      menciona.** O score de quintal do texto é praticamente igual com ou
      sem problema (0,68 vs 0,64) — ou seja, o texto não discrimina
      conservação.
- [ ] **Usar LLM no texto das descrições** para o que a regra não pega
      (nuance, texto indireto, eufemismo).

## 3. Dados externos e pontuação

- [ ] **GeoSampa** — buscar mais dados do imóvel (área oficial, uso do solo)
      e **valor venal**/IPTU, se a API disponibilizar.
- [ ] **API do JEV** — gerar uma **nota** de quão bem o imóvel encaixa no que
      foi pedido.
      ⚠️ Ponto de atenção já registrado no requisito: precisa ser
      **determinístico** — passar **features já calculadas**, não o texto
      bruto.
- [ ] **Estudar outras aplicações** do JEV neste caso.

## 3-B. ITBI e comparação de preços

- [x] **Baixar os dados de ITBI da Prefeitura**
      `src/itbi.py` descobre os links na página da Fazenda (os nomes mudam
      todo mês) e baixa as planilhas. **21 anos (2006–2026), 532 MB.**
- [x] **Ingerir as planilhas no SQLite**
      `src/ingerir_itbi.py` mapeia colunas **por nome** (os arquivos antigos
      têm layout diferente), filtra uso residencial e retoma de onde parou.
      **358 mil+ transações.**
- [x] **Corrigir valores antigos pela inflação**
      `src/indices.py` usa o número-índice do IPCA (IBGE, agregado 1737).
      A API do Banco Central **não resolve DNS** nesta rede.
- [x] **Normalizar endereços para cruzar anúncio × ITBI**
      `src/endereco.py` resolve três diferenças reais: tipo de via abreviado
      (`AV` vs `Avenida`), CEP com zero à esquerda (7 vs 8 dígitos) e número
      no fim do logradouro do anúncio.
- [x] **Comparar o preço pedido com o preço praticado**
      `src/comparar_itbi.py` — compara em **R$/m²** numa cascata de
      `rua+cep` → `rua` → `cep`, exigindo área parecida (±25%).
      **2.128 anúncios comparados (83%).**
- [x] **Listar as transações que embasaram cada comparação**
      Tabela `comparacoes_detalhe` + `--detalhar <url>`: mostra data, área,
      valor corrigido e endereço de cada venda usada — para auditar, em vez
      de aceitar um número anônimo.
- [x] **Filtrar transações que não são preço de mercado**
      A primeira versão comparava com lixo e dava número errado. Descartado:
      doação, herança, leilão, adjudicação, permuta (só `1.Compra e venda`),
      transmissão parcial (proporção < 100%) e R$/m² fora de 800–25.000.
      **Descartou 82 mil de 330 mil linhas e preservou 75% do dado.**
- [x] **Mediana por número de imóvel, não por transação**
      Um empreendimento que vende muitas unidades domina a estatística da
      rua: na Rua Marco Aurélio, 11 das 18 vendas eram no nº 55. A mediana
      passa a ser tirada **dentro** de cada número e depois **entre**
      números — corrigiu **−17%** nessa rua.
- [x] **Priorizar transações financiadas**
      Compra financiada passa por **avaliação do banco** — o valor declarado
      não pode ser reduzido para pagar menos imposto. Medido na base:
      financiadas R$ 5.927/m² contra R$ 4.407/m² das diretas (−26%).
      A cascata tenta **só financiadas primeiro**. Gradiente confirmado:
      0% financiadas → razão 1,57 · 100% financiadas → razão **0,96**.
- [ ] **Levar a comparação para a interface web**
      Hoje só existe pela linha de comando. Falta a coluna "vs. ITBI" na
      listagem, um filtro "abaixo da mediana" e o bloco de transações na
      página de detalhe.

## 4. Interface

- [x] **Melhorar o layout do site**
      Ícones SVG (sem emojis), paleta azul-marinho + terracota verificada
      em contraste WCAG AA, carrossel com setas / swipe / teclado, chips de
      filtros removíveis.
- [x] **Expor a análise das fotos na interface**
      Bloco "visto nas fotos" nos cards e na página de detalhe, + filtros
      por piso do quintal, árvores, conservação e problemas.
- [ ] Novas melhorias de layout/UX a critério.

## 5. Qualidade e infraestrutura

- [ ] **Testes unitários** — backend e interface
      Hoje existem apenas scripts manuais (`testar_parser.py`,
      `testar_playwright.py`, `verificar_localidade.py`,
      `verificar_financiamento.py`); não há suíte automatizada nem pasta
      `tests/`.
- [ ] **Estruturar o backend**
      Separar camadas e mover configuração para variáveis de ambiente.
- [ ] **Deploy no GitHub**, sem subir segredos nem arquivos desnecessários
      🔶 O `.gitignore` já cobre `.env`, `imoveis.db`, `fotos/`,
      `html_ponte/`, `debug_html/`, `playwright-profile/` e `.venv/`.
      Falta inicializar o repositório e publicar.
- [ ] **Pipeline de deploy na AWS** (free tier)
      Lambdas para os crawlers e para o site, com execução diária.
- [x] **Não salvar anúncios repetidos**
      Dedup por URL (chave primária) — reprocessar não duplica.
- [x] **Agrupar anúncios repetidos de imobiliárias diferentes**
      Detecção por **pHash das fotos** (comparar URL/ID não funciona:
      cada imobiliária sobe a própria cópia no CDN).
      **9 grupos marcados.** Marca, **não remove** — preços diferentes entre
      as cópias são informação útil.

---

## Feito além do pedido

- **Desempenho do download de fotos**: paralelizado (8 threads).
  De ~1 foto/s para **~5,7 fotos/s** (~1 hora → ~12 min).
- **Desempenho da análise visual**: 6 requisições em paralelo.
  De 3,4s para **~0,9s por anúncio** (~10 min → ~2 min).
- **Barra de progresso** (`progresso.py`) com ritmo e previsão de término,
  feita em ASCII puro porque o terminal do Windows não lida bem com Unicode.
- **Retomada automática**: interromper e rodar de novo não rebaixa nem
  reanalisa o que já foi feito.
