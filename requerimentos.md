# Requerimentos — projeto Caça-imóveis

> Lista de tarefas do projeto, organizada por tema.
> Legenda: ✅ feito · 🔶 parcial · ⬜ pendente
>
> Última revisão: 2026-10-04 (números reconferidos contra o banco)

---

## Onde estamos — pendências em ordem de facilidade

Tudo abaixo foi **medido no banco**, não deduzido da lista. Vários itens que
estavam marcados como concluídos nesta lista **não estavam** de fato, e é isso
que esta revisão corrige.

### 1. Refazer a detecção de duplicatas — o mais fácil
`achar_duplicatas.py --marcar`. O código está pronto e as colunas `dup_*`
existem, mas estão **todas vazias**: os 9 grupos foram perdidos quando a base
foi reconstruída. Só CPU, sem custo de API. Precisa antes ter as fotos locais
(as 57.402 já estão baixadas).

### 2. Rodar a análise visual nos 2.707 anúncios que faltam
O script existe e o preço é conhecido: **menos de US$ 7** para o lote inteiro.
Sem isso, os filtros de quintal e de árvore simplesmente **não têm dado em 61%
da base**, e qualquer contagem de "quantos têm quintal" fica respondendo só
sobre os 3 bairros já analisados — que é um viés invisível na tela.

### 3. Decidir os pesos da nota de encaixe (JEV)
O cálculo é trivial depois que os pesos existirem. As features já estão
calculadas (área, quartos, quintal, conservação, preço vs mercado). A decisão
de peso é do dono do produto, não do código — é o item que **depende de você**,
não de programação.

### 4. Levar o modelo de casa para a interface
`modelo_casa.py` já está validado (mediana estimado/real 1,04) e não aparece em
lugar nenhum da tela. É ligar o dado que já existe a mais um bloco.

### 5. Rodar o pipeline diário (exige AWS)
Junto com o item 1 da seção 5. Precisa de conta e de decisão de arquitetura;
não é uma sessão de código.

### 6. Usar LLM no texto das descrições (opcional, menor retorno)
As regras determinísticas já cobrem quintal e financiamento. O ganho estaria em
eufemismo e nuance. É o único item aqui que gasta API e cujo retorno ainda não
está demonstrado.

---

## 1. Coleta de dados (crawlers)

> **Nota:** os números desta seção foram reconferidos contra o banco em
> 2026-10-04. Vários estavam parados na época de 4 bairros.

- [x] **Crawler do Imovelweb** funcionando
      Playwright com contexto persistente para passar pelo Cloudflare.
      Estado atual: **131 anúncios** do Imovelweb, dentro do total de
      **4.793** dos 4 portais (o Imovelweb é hoje o menor deles).
- [x] **Crawler da OLX**
      Playwright (a OLX devolve 403 para `requests`). A OLX **não aceita
      filtro de tipo** por nenhum caminho, então o filtro é feito depois.
      **1.890 anúncios** — o maior portal da base, junto com o ZAP.
- [x] **Crawler do ZAP/VivaReal** e **do QuintoAndar**
      **1.895** (ZAP) e **877** (QuintoAndar). São os que mais ajudam: o ZAP
      é o único portal que publica o **CEP** no anúncio.
- [x] **Enriquecimento pela página de detalhe**
      Descrição completa, quartos/banheiros/vagas, financiamento e a
      **galeria completa** (a listagem publica só 1 foto de capa; o detalhe
      traz até 50). Resultado: **57.402 fotos** armazenadas.
- [x] **Validação de localidade**
      Todo anúncio salvo é de São Paulo **e** de um dos bairros-alvo —
      evita tanto o fallback nacional do site quanto bairro errado.
- [x] **Ampliar a lista de bairros**
      **21 bairros configurados**; **18** têm anúncios na base.
- [ ] **Rodar o crawler diariamente**, de forma automática
      (ver pipeline na AWS, item 5).
- [x] **Retirar os apartamentos da busca**
      `tipo_do_anuncio()` classifica por título e URL. Armadilha encontrada:
      `vila` é **nome de bairro** (Vila Mangalot, Vila Leopoldina), não tipo
      de imóvel — classificava todo apartamento desses bairros como casa.
- [x] **Filtro de preço máximo**
      `PRECO_MAX = 1.000.000`, verificado nos 4 níveis: `config`,
      `atende_preco()`, o `priceMax` da API e o SQLite final.
      Reconferido: **nenhum** anúncio na base passa de R$ 1.000.000.

## 2. Enriquecimento com IA (DeepSeek)

- [x] **Atributos extraídos das FOTOS** (visão computacional) — **funciona**
      quintal, piso do quintal (terra/grama/cimento/misto), árvores,
      vegetação, iluminação, arejamento, conservação, janelas grandes,
      fachada, reforma, piso interno, cômodos, extras (piscina,
      churrasqueira, edícula, varanda, área gourmet…) e **problemas**
      (mofo, infiltração, entulho, obra inacabada, abandono).
      **Mas a cobertura caiu:** hoje são **1.829 de 4.793 anúncios (38%)**,
      e não os 178/178 que estavam aqui antes. A base cresceu de 178 para
      4.793 e a análise não foi reexecutada.
      Completos: Parque Maria Domitila (638/638), Vila Mangalot (435/436),
      Parque São Domingos (756/775). **Zerados:** os outros 13 bairros —
      Vila Pereira Barreto (524), Vila Jaguara (465), Jardim Líbano (423),
      Lapa (365), Vila Bonilha (361), Pirituba (237), Vila Romana (187),
      Barra Funda (140), Vila Leopoldina (120) e os pequenos.
      **2.707 anúncios têm fotos baixadas e nunca passaram pela visão.**
- [ ] **Rodar a análise visual nos 2.707 que faltam**
      O script já existe (`analisar_visao.py`) e o custo é conhecido:
      `detail: original` custa 458 tokens/imagem, ~US$ 0,0024 por anúncio de
      30 fotos — o lote inteiro sai por **menos de US$ 7**. É a tarefa mais
      fácil e de maior efeito: sem ela, os filtros "quintal" e "árvore de
      porte" simplesmente não têm dado em 61% da base, e a nota de encaixe
      fica viesada para os 3 bairros analisados.
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

- [x] **GeoSampa** — buscar mais dados do imóvel (área oficial, uso do solo)
      e **valor venal**/IPTU, se a API disponibilizar.
      Feito, e a resposta tem duas partes:
      **Área oficial:** o WFS do GeoSampa (`geoportal:lote_cidadao`) fornece
      `area_terreno` e `area_construida` por lote. **26.475 lotes** ingeridos.
      Armadilha: o campo é `cd_tipo_terreno_imovel` (não `dc_`), e o GeoSampa
      abrevia diferente da prefeitura (`GAL`=GENERAL, `CON`=CONEGO,
      `COMEN`=COMENDADOR) — sem tratar isso, a maioria das ruas vinha vazia.
      Cobertura: **86%** dos anúncios com rua.
      **Valor venal:** a prefeitura **não publica** o venal por imóvel. Mas o
      ITBI traz o venal em **98% das transações**, junto com o preço — então a
      **razão venal/preço é mensurável** por região (mediana da cidade: 0,82).
      `valor_venal = preco_pedido * razao_da_regiao`, com **100% de cobertura**.
      É uma projeção, não uma consulta — a interface diz isso explicitamente.
- [ ] **API do JEV** — gerar uma **nota** de quão bem o imóvel encaixa no que
      foi pedido.
      ⚠️ Ponto de atenção já registrado no requisito: precisa ser
      **determinístico** — passar **features já calculadas**, não o texto
      bruto.
      **O que já existe NÃO é isso:** a interface oferece ordenar por
      "Melhor match" (`ordem=score`), mas esse `score_quintal` é uma
      **contagem de palavras-chave no texto** (`config.PALAVRAS_QUINTAL`),
      não uma nota de encaixe. Por exemplo, um "sim" de quintal conta pelo
      menos +1, mas quem olha prefere terreno grande e casa conservada.
      Construir a nota de verdade implica decidir **pesos** — e pesos são
      uma escolha do dono do produto, não do código.
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
- [x] **Levar a comparação para a interface web**
      Cada card traz o comparativo com o mercado, e a página de detalhe mostra
      **três medidas independentes**: (1) preço praticado (ITBI), (2) valor
      venal projetado e (3) área oficial do cadastro (GeoSampa). Há filtro
      "abaixo do preço praticado" e ordenação por "mais abaixo do mercado",
      além do bloco com as transações usadas na comparação.
      > **O modelo de casa (`modelo_casa.py`) NÃO está na interface.** Ele
      > existe e está validado por linha de comando (terreno + construção
      > depreciada), mas não é uma das três medidas exibidas.
- [x] **Valor venal na interface**
      Rótulo deixa claro que é **projeção de referência tributária**, não
      preço de mercado — serve para comparar com o IPTU.

## 4. Interface

- [x] **Melhorar o layout do site**
      Ícones SVG (sem emojis), paleta azul-marinho + terracota verificada
      em contraste WCAG AA, carrossel com setas / swipe / teclado, chips de
      filtros removíveis.
- [x] **Expor a análise das fotos na interface**
      Bloco "visto nas fotos" nos cards e na página de detalhe, + filtros
      por piso do quintal, árvores, conservação e problemas.
- [x] **Simplificar os filtros**
      "Conservação" e "com problemas" foram **removidos**: não filtravam bem
      e ocupavam espaço. "Quintal" e "árvores" ganharam nomes mais claros.
- [x] **Filtros de múltipla escolha** (bairro e piso do quintal)
      O `<select multiple>` nativo exige Ctrl+clique e fecha a lista a cada
      escolha — inutilizável na prática. Cada campo virou um botão que abre um
      painel de caixas de marcação, com o resumo da escolha no próprio botão.
      No servidor, `request.args.get` virou `getlist` (com `get`, marcar três
      bairros fazia a busca considerar **só o primeiro**, ignorando os outros
      em silêncio).
- [ ] Novas melhorias de layout/UX a critério.

## 5. Qualidade e infraestrutura

- [x] **Testes automatizados** — backend e interface
      Pasta `tests/` criada, com `_bootstrap.py` que põe `src/` no path.
      **`testar_web.py`: 52 verificações** (rotas, cada filtro reduz o total,
      filtros combinados, filtros múltiplos, chips removem só a si mesmos,
      paginação, as três medidas de valor, links da listagem, 404 em anúncio
      inexistente) e **`testar_tipo.py`: 32**. Também `medir_rotas.py` para
      medir o tempo de cada rota e `conferir_segredos.py` para varrer o
      repositório antes de publicar.
- [x] **Estruturar o backend**
      Código em `src/`; os arquivos da raiz viraram atalhos de duas linhas
      (`from _runner import executar; executar('main')`). Configuração
      centralizada em `config.py` (`config.caminho(...)` resolve os caminhos
      de dados, `VISAO_*` controla a análise visual), com segredos no `.env`.
- [x] **Deploy no GitHub**, sem subir segredos nem arquivos desnecessários
      O `.gitignore` cobre `.env`, `imoveis.db`, `fotos/`, `html_ponte/`,
      `debug_html/`, `playwright-profile/` e `.venv/`. Repositório publicado
      e **clone limpo validado** a partir do GitHub.
      **Bug encontrado no conferidor de segredos:** ele calculava a raiz como
      `dirname(__file__)` — que aponta para `tests/` — e por isso lia 9
      arquivos em vez de 106, declarando o repositório "seguro para publicar"
      **sem nunca ter lido o `.env`**. Uma verificação que não verifica é pior
      que nenhuma.
- [ ] **Pipeline de deploy na AWS** (free tier)
      Lambdas para os crawlers e para o site, com execução diária.
- [x] **Não salvar anúncios repetidos**
      Dedup por URL (chave primária) — reprocessar não duplica.
- [ ] **Agrupar anúncios repetidos de imobiliárias diferentes** — dados perdidos
      O código está pronto e a detecção por **pHash das fotos** funciona
      (comparar URL/ID não serve: cada imobiliária sobe a própria cópia no CDN).
      **Mas as colunas `dup_*` estão TODAS vazias (0 linhas)** — `dup_grupo`,
      `dup_qtd`, `dup_melhor`, `dup_n_fotos`, `dup_menor_preco` e
      `dup_detalhes`. Os 9 grupos que existiam antes foram perdidos, quase
      certamente quando a base foi reconstruída de 178 para 4.793 anúncios.
      Para refazer: `.venv\Scripts\python.exe achar_duplicatas.py --marcar`.
      Marca, **não remove** — preços diferentes entre as cópias são informação
      útil. Com 57 mil fotos será bem mais lento que os 178 de antes, mas é só
      CPU: não gasta API.

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
- **Conserto do travamento da interface** (não estava no pedido; apareceu no
  uso). `GET /` *nunca* retornava — sem erro, sem mensagem. Duas causas, as
  duas medidas e não deduzidas:
  1. A tabela `fotos` não tinha índice em `anuncio_url`. A consulta de fotos
     era feita **uma vez por card**, e cada uma varria as 57 mil linhas
     (`SCAN`): **44,5 ms × 4.793 cards = 213 s**. Com o índice a consulta caiu
     para 0,118 ms.
  2. A página renderizava **4.793 cards de uma vez**: 33,8 MB. Agora são 60
     por página (com paginação que preserva os filtros) — **514 KB**.
  Resultado: a home saiu de 10,8 s para **1,0 s**, e o total das rotas de
  37,3 s para 2,4 s.
  **Lição de método:** eu apontei duas causas erradas antes de medir. E um
  travamento é **invisível** para `print` — a saída só aparece quando o
  processo termina, então a única forma de ver onde para é `python -u` com
  redirecionamento para arquivo.
