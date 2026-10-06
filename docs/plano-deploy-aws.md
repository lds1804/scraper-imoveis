# Plano de deploy na AWS (free tier) + atualização automática

> **Método:** todo número deste documento foi **medido no banco, na rede ou no
> código** — não deduzido. Onde não deu para medir, está escrito *estimado*.
> Scripts usados: `experimentos/sonda_fontes.py`, `experimentos/medir_deploy.py`, `experimentos/medir_site_db.py`,
> `experimentos/custo_visao.py`.

---

## Resumo executivo

| Pergunta | Resposta medida |
|---|---|
| Cabe no free tier da AWS? | **Sim**, e com folga — desde que o site sirva um banco de **46 MB**, não os 638 MB |
| O "R$ 30" do DeepSeek de onde vem? | **Todas as fotos + `detail: original` = R$ 29,04.** É o cenário mais caro possível |
| Dá para rodar o mesmo por muito menos? | **Sim: R$ 12,43** (1× por grupo de duplicata, `detail: low`). E a atualização diária custa **centavos** |
| O crawler está na legalidade? | **Parcialmente.** O `Crawl-delay: 10` do ZAP/VivaReal passou a ser respeitado (`GLUE_DELAY_S = 10`, garantido em `glue_api.buscar_pagina`); restam os pontos de direito autoral abaixo |
| Pode publicar o site com esses dados? | **Sim, com ressalvas pontuais.** O risco não é LGPD (não há dado pessoal) — é **direito autoral das fotos e dos textos** |
| O pipeline já está pronto? | **Não.** Existe o coletor, mas falta a camada de agendamento, retomada e controle de custo |

---

## 1. Números medidos

### 1.1 Tamanhos (o que decide a arquitetura)

```
banco de trabalho  imoveis.db    637,8 MB   163.276 páginas de 4 KB
fotos                           5.031,2 MB   57.451 arquivos (média 93 KB)
playwright-profile                101,5 MB
html_ponte                         74,2 MB
```

O `imoveis.db` parece grande, mas **o site não lê quase nada dele**:

| tabela | linhas | quem usa |
|---|---|---|
| `lotes` | 1.627.266 | **só o cálculo** — o site nunca lê |
| `itbi` | 537.354 | **só o cálculo** — o site nunca lê |
| `fotos` | 57.402 | site |
| `cep_da_rua` | 26.660 | cálculo |
| `comparacoes_detalhe` | 17.599 | site |
| `anuncios` | 4.793 | site |
| `valores_venais` | 4.793 | site |
| `areas_oficiais` | 3.163 | site |
| `comparacoes` | 3.155 | site |

**Testado, não estimado:** gerei um `site.db` só com o que a tela lê e com os
índices que ela precisa (`idx_fotos_anuncio`, `idx_comp_anuncio`,
`idx_val_anuncio`, `idx_area_anuncio`, `idx_det_anuncio`, `idx_anuncios_preco`,
`idx_anuncios_bairro`):

```
imoveis.db (trabalho)   637,8 MB
site.db    (produção)    46,3 MB   <- 13,8x menor
```

> Sem esses índices a home levava **213 s** (medido numa sessão anterior:
> 44,5 ms × 4.793 cards varrendo `fotos`). Eles são obrigatórios, não opcionais.

### 1.2 Custo de tokens da análise visual

Parâmetros **medidos** (não estimados): `detail: low` = 203 tok/imagem ·
`detail: original` = 458 · prompt ≈ 907 tok · saída ≈ 265 tok.
Preço `deepseek-flash` (US$/1M): entrada 0,30 peak / **0,15 off-peak**;
saída 1,20 peak / **0,60 off-peak**. Off-peak = 01–04h e 06–10h UTC, seg–sex.

Estado atual do código: `VISAO_DETALHE = "original"` e **`VISAO_MAX_FOTOS = 0`
(sem teto)** — ou seja, envia **todas** as fotos. Matriz para a base inteira
(4.793 anúncios, 57.402 fotos):

| cenário | US$ | R$ |
|---|---|---|
| TODAS as fotos, `original`, peak | 10,72 | 58,08 |
| **TODAS as fotos, `original`, off-peak** | **5,36** | **29,04** ← o "R$ 30" |
| TODAS as fotos, `low`, peak | 6,32 | 34,28 |
| TODAS as fotos, `low`, off-peak | 3,16 | 17,14 |
| teto 12 fotos, `original`, off-peak | 5,37 | 29,08 |
| teto 12 fotos, `low`, off-peak | 3,17 | 17,16 |
| teto 8 fotos, `low`, off-peak | 2,58 | 13,99 |
| **1× por grupo de duplicata, `low`, off-peak** | **2,29** | **12,43** |

**Onde está o desperdício** — a cauda de fotos:

```
  1-5 fotos    1.907 anúncios (42,0%)  ##################
  6-12           853 anúncios (18,8%)  ########
  13-25        1.159 anúncios (25,6%)  ###########
  26-50          543 anúncios (12,0%)  #####
  51+             74 anúncios ( 1,6%)
  media 12,7 · mediana 12 · p90 30 · p99 57 · MÁXIMO 135
```

60% dos anúncios têm ≤12 fotos, mas **14% têm mais de 25** — e é aí que o
dinheiro vai. Um teto de 12 não muda quase nada na conta do `detail: original`
(R$ 29,04 → R$ 29,08, porque o prompt domina), mas **corta 45% das fotos**.

**Fotos repetidas:** 57.402 linhas, 56.263 URLs distintas — só **2,0%** são
repetição literal. A economia grande **não** vem de deduplicar URL: vem de
**analisar 1× por grupo de duplicata** (2.938 imóveis distintos em vez de 4.793
anúncios), o que leva a 46.865 fotos em vez de 56.263.

> Nota de método: uma contagem por **nome de arquivo** acusa 44.311 repetições
> — é falso. O QuintoAndar usa `image.jpg` como nome genérico e põe o ID no
> **diretório** (`895323426-897.5243673517473image.jpg`). Contar por nome
> apontaria uma economia de 77% que não existe. Contar por URL é o correto.

### 1.3 Latência e cadência real de cada fonte

| fonte | resposta | cadência | o que muda |
|---|---|---|---|
| IBGE IPCA (agregado 1737) | 0,2 s | **mensal** | fator de correção |
| BCB SGS 189 (IGP-M) | 0,6 s | **mensal** | fator de correção |
| GeoSampa WFS | 0,4 s | **diária** | área oficial do lote |
| ITBI (Fazenda) | ✅ `descobrir_links()` acha **21 anos (2006–2026)** | **mensal** | preço praticado |
| Portal dados abertos (CKAN) | **falha intermitente** | — | requer retry |

> O `itbi.py` **já está no endereço certo**: a página antiga (`/20706`) dá 404 e
> a nova (`/31501`) responde. `descobrir_links()` devolve os 21 anos. Não há
> nada a consertar aqui.

### 1.4 Tráfego das fotos (decide se o free tier aguenta)

Amostra de 12.001 fotos: **média 93 KB**, mediana 87 KB, p90 146 KB.

Página com 60 cards × 1 foto de capa = **5,5 MB por carregamento**.

| limite | visitas de página que cabem |
|---|---|
| 1 TB/mês (CloudFront free tier) | **191.728** |
| 100 GB/mês (limite de saída da EC2) | 18.723 |

**O free tier aguenta.** 190 mil páginas/mês é muito mais do que este site vai
ver. Mas 5,5 MB por página é ruim para o usuário em 4G — e miniatura resolve
os dois: com 35 KB/foto, a página cai para 2,1 MB e cabem **511 mil** visitas.

---

## 2. Legalidade — leia antes de publicar

### 2.1 robots.txt: o que cada portal efetivamente autoriza

Medido em 2026-10-05:

| portal | HTTP | `Crawl-delay` | achado relevante |
|---|---|---|---|
| **Imovelweb** | 200 | nenhum | libera a busca; bloqueia só `?utm_*`, `?duplicated=*`, `?labs=*` |
| **OLX** | **403** | — | **não consigo ler o robots.txt** — o anti-bot bloqueia a própria leitura |
| **ZAP** | 200 | **`10`** | declarado para `*` |
| **VivaReal** | 200 | **`10`** | declarado para `*` |
| **QuintoAndar** | 200 | nenhum | libera a busca; bloqueia `/imovel/*/descricao`, `/detalhes-imovel`, `/agendar` |

### 2.2 As três exposições reais

**1. `Crawl-delay: 10` ignorado (ZAP e VivaReal).**
É uma instrução explícita do dono do site, no arquivo que existe exatamente
para isso. Hoje o coletor paraleliza (8 threads / `VISAO_PARALELO=10`) e passa
longe de 10 s entre requisições. **Isto é o item mais fácil de corrigir e o
mais difícil de justificar não corrigir.**

**2. Direito autoral dos TEXTOS.**
Descrições de anúncio são obra protegida (Lei 9.610/98, art. 7º). Reproduzir a
íntegra num site público é redistribuição. Medido: **131 anúncios têm descrição
preenchida, 71 acima de 500 caracteres, máximo 3.849**. A exposição é pequena
em volume, mas existe.

**3. Direito autoral das FOTOS — o maior risco.**
5 GB de fotos de 57 mil arquivos, de imobiliárias distintas. Foto é obra
protegida. Baixar para análise local é uma coisa; **servir num site público é
outra**, porque aí você é quem redistribui. É o item com maior probabilidade de
gerar reclamação (as imobiliárias monitoram uso das próprias fotos).

### 2.3 LGPD: verificado, NÃO é o problema

Procurei dado pessoal no banco:

- **Colunas de `anuncios`:** nenhuma de corretor, telefone, e-mail, contato,
  anunciante, imobiliária ou CRECI. As 49 colunas são de imóvel, foto e
  duplicata.
- **Nas descrições (amostra de 131):** **0 com telefone, 0 com e-mail.**

Ou seja: o dado é sobre **imóveis** (endereço, preço, área), publicamente
anunciados. Risco de LGPD é baixo. Há uma nuance a registrar: *endereço de
imóvel + "à venda"* pode indiretamente indicar que uma pessoa está vendendo —
mas como o dado já era público no anúncio original, não há tratamento novo de
dado pessoal.

### 2.4 Recomendações concretas

| # | medida | custo | efeito |
|---|---|---|---|
| 1 | **Respeitar `Crawl-delay: 10`** no ZAP/VivaReal: 1 requisição a cada 10 s, sem paralelismo nesses dois | alonga a coleta | remove a exposição mais clara |
| 2 | **Linkar as fotos, não copiar** (`<img src="CDN original">`) | zero | deixa de redistribuir; **derruba os 5 GB do S3** |
| 3 | **Não reproduzir a descrição integral** — mostrar resumo curto + link "ver no portal" | baixo | respeita o art. 8º (citação para fins informativos) |
| 4 | **`User-Agent` honesto** identificando o projeto, com URL de contato | baixo | é o que o `robots.txt` presume |
| 5 | **`robots.txt` próprio** bloqueando indexação do site | baixo | evita que o conteúdo replicado apareça em busca |
| 6 | **Página de remoção** (*takedown*): "se você é o autor e quer remover, escreva para X" e **atender em 48 h** | baixo | reduz muito o risco prático |
| 7 | **`noindex, nofollow`** nas páginas de detalhe | baixo | tira o site do radar de busca |
| 8 | Manter a análise visual **local/privada**; publicar só o **agregado** ("tem quintal: sim"), não a foto | zero | o dado derivado é seu |

### 2.5 O que NÃO fazer

- **Não** criar uma versão "pública de verdade" com as fotos hospedadas se a
  intenção é divulgar. Se for só para uso próprio, mantenha privado (login) e a
  análise toda muda de figura.
- **Não** contornar o 403 da OLX com proxy/rotação de IP. Isso é o que
  transforma "coleta de dado público" em "evasão de medida técnica", que é
  agravante, não atenuante.
- **Não** republicar a descrição integral nem o nome/telefone do corretor se um
  dia isso entrar na base.

### 2.6 Uso privado x site público — o texto da lei, lido (2026-10-05)

Leitura direta da Lei 9.610/98 e da Lei 12.965/2014 (texto do Planalto), não
de memória.

**O dispositivo do uso privado é o art. 46, II:**

> "a reprodução, **em um só exemplar de pequenos trechos**, para **uso privado
> do copista**, desde que feita por este, **sem intuito de lucro**"

Ele **não encaixa perfeitamente** aqui, e é honesto dizer por quê:

| exigência do inciso | nossa situação |
|---|---|
| uso privado do copista | ✅ |
| feita por este | ✅ (o próprio crawler) |
| sem intuito de lucro | ✅ |
| **"em um só exemplar"** | ❌ são **57.402** arquivos |
| **"pequenos trechos"** | ❌ a foto é a obra **inteira** |

**O argumento mais forte é outro — o art. 46, VIII** (o "fair use" brasileiro),
que admite **obra integral** quando *"a reprodução em si não seja o objetivo
principal da obra nova"* e não prejudique a exploração normal. O objetivo deste
site é a **análise de preço**; a foto é meio, não fim. Esse é o enquadramento
defensável.

Outros dois pontos que costumam ser esquecidos:
- **art. 44** — obra fotográfica é protegida por **70 anos** da divulgação.
- **art. 79, §1º** — *"A fotografia, quando utilizada por terceiros, indicará
  de forma legível o nome do seu autor."* Exigência legal que **nem os portais
  cumprem**. Um site que credita está em situação melhor.

#### Por que o uso privado é tranquilo na prática

1. **O site já é local.** `webapp.py` faz `app.run(host="127.0.0.1", ...)` — só
   a máquina do usuário. Isso **não é "comunicação ao público"** (art. 5º, V:
   "ato mediante o qual a obra é colocada ao alcance do público"). Se só o dono
   acessa, o dispositivo nem incide.
2. **O risco se materializa na divulgação**, não na pesquisa pessoal.

#### Correção de uma afirmação anterior deste plano

A seção 2.4 dizia que "linkar em vez de copiar elimina a maior parte do risco".
Isso é **direcionalmente certo, mas exagerado**. Linkar **reduz**, não elimina:

- Continua havendo debate sobre se exibir a imagem no contexto da página é
  "comunicação ao público" (art. 5º, V).
- **Para direitos autorais a proteção de provedor NÃO se aplica.** O Marco
  Civil (art. 19, §2º e art. 31) diz que, em matéria autoral, a
  responsabilidade segue a **lei autoral** — art. 104 fala em
  **responsabilidade solidária com o contrafator**. O "só responde após ordem
  judicial" **não** nos protege aqui.
- E nós seríamos **divulgador direto**, não provedor de conteúdo de terceiro.

#### Ordem de eficácia das mitigações

| # | medida | eficácia |
|---|---|---|
| 1 | **Publicar só a ANÁLISE, sem as fotos** | 🥇 muito alta |
| 2 | Não ser indexado (`noindex`) e não divulgar | alta |
| 3 | Linkar o CDN em vez de hospedar | média |
| 4 | Truncar a descrição + link "ver no portal" | média |
| 5 | Página de *takedown*, atender em 48 h | média |
| 6 | Crédito do fotógrafo (art. 79, §1º) | baixa, mas é exigência legal |

#### A saída que resolve: publicar o derivado, não o original

**art. 8º** — *"Não são objeto de proteção como direitos autorais... **as
informações de uso comum**"*.
**art. 7º, §2º** — a proteção sobre base de dados *"**não abarca os dados ou
materiais em si mesmos**"*.

Ou seja: **o dado não é protegido; a foto e o texto são.** O ativo real deste
projeto — "tem quintal, conservação 4/5, está 12% abaixo do preço praticado na
rua" — é **trabalho próprio sobre fatos**. É isso que sustenta um site público
defensável: exibir a avaliação e **devolver a imagem a quem a hospeda**, com
link "ver no portal".

> **Aviso:** não é parecer jurídico. É leitura do texto legal aplicada aos fatos
> técnicos medidos. O cenário público é o único com risco real — se o plano for
> divulgá-lo, cabe consulta profissional antes.

#### Recomendação por cenário

| cenário | recomendação |
|---|---|
| **Só para você** (hoje) | manter local. Risco baixo, é o cenário que a lei tolera melhor |
| **Acessar do celular** | **login**, não URL pública. URL não é cadeado: se qualquer um que a ache acessa, já é "ao alcance do público" |
| **Público de verdade** | publicar **a análise sem as fotos** + link "ver no portal" |

### 2.7 Site privado na AWS (só o dono acessa) — o que muda

Cenário pedido: publicar na AWS com acesso restrito. É viável e é o meio-termo
razoável, mas **três coisas precisam mudar antes** — o código como estava não
podia ir para servidor.

#### Os três bloqueios encontrados (medidos, não supostos)

**1. `debug=True` é execução remota de código.**
`webapp.py` terminava com `app.run(host="127.0.0.1", port=5000, debug=True)`.
O depurador do Flask, exposto, permite **rodar comando no servidor** — é o
vetor mais conhecido do Flask. Quem alcança o site lê o `.env` (com a chave da
DeepSeek) ou apaga o banco.

> Atenuante verificado: `webapp.py` **não** importa `visao`/`analise_visual`,
> então a chave não aparece em traceback. Mas o `.env` está na raiz, ao lado
> do banco — legível por qualquer execução de comando.

**2. `host="127.0.0.1"` não escuta nada.** Em container na AWS isto significa
que **ninguém** alcança, nem o balanceador.

**3. As rotas `/_ponte/*` não tinham guarda nenhuma.** O código **afirmava**:

> *"Ferramenta de uso pessoal e local (só aceita 127.0.0.1)."*

Procurei no bloco: a única ocorrência de `127.0.0.1` era **nesse comentário**.
A rota `/_ponte/html` aceitava POST de qualquer origem, respondia com
`Access-Control-Allow-Origin: *` e **grava `request.get_data()` em disco** (até
120 caracteres de nome de arquivo). `/_ponte/limpar` fazia `rmtree`. Expostas,
qualquer um escreve e apaga arquivo no servidor.

> **Lição de método:** um comentário que afirma uma garantia não é a garantia.
> Este sobreviveu porque ninguém testou a partir de outro IP.

#### O que foi implementado

`src/ambiente.py` (novo) decide o modo pelo ambiente — nunca fixo no código:

| variável | efeito |
|---|---|
| `SITE_AMBIENTE=producao` | desliga `debug`, sobe em `0.0.0.0`, **exige senha** |
| `SITE_SENHA=<longa>` | a senha do acesso (mínimo 12 caracteres) |
| `SITE_USUARIO=eu` | usuário (opcional) |

No `webapp.py`:
- **`before_request` com autenticação básica** em produção. Usa
  `hmac.compare_digest` — comparação byte a byte vaza o prefixo correto da
  senha pelo tempo de resposta.
- **Guarda de rede real** nas rotas `/_ponte/*` (`_ponte_so_local()` → 403).
  Elas não podem exigir login porque o navegador as chama de dentro do
  `imovelweb.com.br`; a escolha foi permiti-las **só de 127.0.0.1**.
- **`noindex` + `no-store`** em toda resposta (`X-Robots-Tag`,
  `Cache-Control`, `X-Frame-Options`, `Referrer-Policy`) e `<meta robots>` nos
  templates. Sem isso, um buscador indexa o site privado.
- **Recusa a subir sem senha.** Em produção sem `SITE_SENHA`, `ambiente.validar()`
  levanta exceção e o processo morre. É deliberado: *um site que parece
  protegido e não está é pior que um site que não sobe.*

#### Legalidade neste cenário

Continua valendo a análise de 2.6, com **duas diferenças**:

1. **O dispositivo passa a incidir.** Em `127.0.0.1` o site não é "ao alcance
   do público" (art. 5º, V). Na AWS ele **está** na internet — mesmo com
   senha. A defesa segue sendo o art. 46, VIII (o objetivo é a análise, não a
   foto), mas **a posição é mais frágil que a local**.
2. **A autenticação é o que sustenta o "uso privado".** Autenticação básica
   com senha longa é controle de acesso real (ao contrário de URL não
   divulgada). Autenticação básica transmite a senha em base64 — **só use com
   HTTPS**, que o CloudFront fornece.

**O que deixa isto defensável:** as fotos **linkadas do CDN** (branch
`feat/site-publico`) em vez de hospedadas. Assim o servidor não armazena nem
serve obra de terceiro — ele aponta para quem já a publica. Essa combinação
(sem hospedar + privado + noindex) é a versão mais conservadora possível.

#### Limitações honestas

- **Autenticação básica tem UX ruim** no celular (diálogo do navegador, sem
  "sair"). Para uso pessoal serve; para mais gente, login com sessão.
- **Senha em base64 não é criptografia** — sem HTTPS é texto claro. CloudFront
  resolve, mas é obrigatório, não opcional.
- **Nada foi implantado na AWS.** Isto é o código pronto, não o deploy.

---

## 2.8 Linkar a foto do CDN: medido (2026-10-05)

Testei 490 URLs de foto (120 por portal) **sem Referer**, exatamente como o
site faria: **490/490 = 100% responderam 200** com `content-type` de imagem
(`image/jpeg` ou `image/webp`). Tempo: 53 s.

E **nenhuma** URL tem assinatura ou expiração — procurei por `Expires=`,
`X-Amz-Expires`, `Signature=`, `Policy=`, `token=`, `sig=`, `exp=`,
`AWSAccessKeyId`: **0 ocorrências**. As query strings são só
`?isFirstImage=true`. **O link não caduca sozinho.**

### O custo escondido que essa medição NÃO pega

| | hospedada | linkada |
|---|---|---|
| Site quebra quando o anúncio sai do ar | não | **sim** |
| Você armazena obra de terceiro | sim | **não** |
| Funciona offline | sim | não |
| Depende do CDN de outro | não | sim |

Quando o imóvel é vendido ou a imobiliária troca a foto, **o site fica com
imagem quebrada, sem aviso**. E não há como medir quantas quebraram: a tabela
`anuncios` **não tem data de coleta** — as únicas colunas com data são
`foto_analisada_em` e `calculado_em` (ambas de processamento, não de coleta).

> Se o modo `link` for para produção, **vale acrescentar `coletado_em`** em
> `anuncios`. Sem isso não há como saber se um link está velho.

### Sobre a legalidade de linkar

Linkar **melhora a posição** (você não armazena nem serve o arquivo — devolve
a imagem a quem a publica), mas **não zera** a questão: continua havendo
exibição da imagem no contexto da sua página, que é o que o art. 5º, V chama
de comunicação ao público. É uma posição **mais confortável**, não uma
imunidade. A leitura de 2.6 continua valendo.

### Correção necessária: 10 `.svg` na tabela `fotos`

Há **10 linhas** cujo arquivo é `.svg` — ícone do próprio site (seta do Navent,
`right-arrow.5c51420c.svg`), baixado por engano como se fosse foto. Afetam 10
anúncios. `fotos_fonte._e_imagem()` já os descarta na exibição, mas eles
**contam** em `COUNT(*)` de `fotos` e inflam a lista `fotos_urls` (é a foto
`\09.svg`, sempre na 9ª posição).

---

## 2.9 🔴 A área do anúncio é frequentemente o TERRENO, não a construção

**Isto é um defeito de medição, não do dado.** Descoberto ao investigar por que
tantos anúncios apareciam como "divergentes" na conferência de área.

### A prova (929 casos com lote exato, nível `lote`)

O anúncio publica **um** número. Comparei com as duas áreas da prefeitura:

| endereço | anúncio | terreno (pref.) | construída (pref.) |
|---|---|---|---|
| Rua Álvares Otero, 37 | **200 m²** | **200 m²** ✅ exato | 60 m² |
| Rua Itapejara, 86 | **434 m²** | **434 m²** ✅ exato | 145 m² |
| Rua Aurélia, 1589 | 250 m² | 129 m² | 73 m² |

Sintoma que denunciou: os números do anúncio são **redondos** (200, 240, 250,
300) — típico de terreno. Construção dá valores quebrados (73, 46, 98).

### A estatística

```
bate SÓ com a construída :  380  ( 40,9%)
bate SÓ com o terreno    :  113  ( 12,2%)
bate com os DOIS (iguais):  149  ( 16,0%)
não bate com nenhum      :  287  ( 30,9%)
```

**Hoje só conferimos a construída** (`area_oficial`), então acusamos de
divergente **43%** (12,2 + 30,9) que podem ser apenas **comparação de áreas
diferentes** — falso positivo nosso, não mentira do anúncio.

### A causa é estrutural

| fonte | `area_terreno` | `area_construida` |
|---|---|---|
| **GeoSampa** (1.627.266 lotes) | **100,0%** | 93,4% |
| **anúncio** (4.793) | **1,1%** (52) | 99,9% (4.786) |

A prefeitura tem **terreno em 100%** dos lotes; o anúncio quase **não tem esse
campo**. Então o portal publica a metragem que possui, e nós assumimos que é
construída.

### O que fazer

1. **A conferência deve testar as duas** e dizer qual casa. Fica muito mais
   informativo que "divergente":
   > *"anúncio diz 200 m² · prefeitura: terreno 200 m², construção 60 m² →
   > o número do anúncio é o **terreno**"*
2. **`dif_pct` deixa de ser erro** quando a área do anúncio bate com o
   terreno — passa a ser **classificação**, não divergência.
3. Rever o threshold `_grau_area()` em `webapp.py`, que hoje assume só
   construída.

> **Lição de método:** o usuário desconfiou ("o anúncio não publica a área
> construída do imóvel, publica a área do terreno") e estava certo. O dado
> sempre esteve no banco — o defeito era **nossa suposição** de qual campo o
> anúncio preenche. Medir nos 929 casos exatos provou em 2 minutos o que 929
> linhas de "divergente" escondiam.

### ✅ CORRIGIDO (2026-10-05) — e o efeito foi medido

Implementado em `referencia_geosampa.py` (`_casa_com()`) e aplicado na
interface (`webapp._grau_da_area()`). Nova coluna
`areas_oficiais.area_casa` diz o que o número do anúncio é; bancos antigos
se atualizam sozinhos (o `calcular()` faz `ALTER TABLE` se faltar).

**Distribuição nas 3.163 conferências:**

| o número do anúncio é | casos | % |
|---|---|---|
| nenhuma das duas — divergência real | 1.329 | 42,0% |
| construção (o esperado) | 823 | 26,0% |
| **ambas** (terreno = construção) | 529 | 16,7% |
| **terreno** | **481** | **15,2%** |

**Efeito nos graus que a tela mostrava:**

| grau | antes | depois | mudança |
|---|---|---|---|
| compatível | 1.350 | **1.831** | **+481** |
| atenção | 665 | 459 | −206 |
| divergente | 1.147 | 872 | −275 |

**481 casos (15,2%) deixaram de ser acusação falsa** — eram anúncios que
publicaram o terreno. Antes, o `+233%` que aparecia como "divergente" era
simplesmente área diferente sendo comparada.

**O que a tela mostra agora**, no caso real (anúncio 175 m², construção 100 m²,
terreno 175 m²):

> **o anúncio publica a área do TERRENO**
> *lote exato no cadastro da prefeitura*
> | | área construída | terreno |
> |---|---|---|
> | anúncio | 175 m² | não informado |
> | cadastro | 100 m² | 175 m² |
>
> O anúncio informou **uma** metragem (175 m²) e ela corresponde ao **terreno**
> do cadastro (175 m²), não à construção (100 m²). A maioria dos portais
> publica só esse campo — **não é erro do anúncio**, é o dado que ele tem.

E a divergência **real** continua sendo acusada: um anúncio de 85 m² contra
construção 110 m² (mediana de 71 lotes da rua) segue como `atenção −23%`, com
o aviso de que cadastro desatualizado é comum.

**Verificação:** `testar_area_casa.py`, **22 verificações** (a função de
classificação com 6 casos, a zona morta dos dois lados, o grau na interface,
a coluna preenchida no banco e uma página real renderizada). Confirmado também
visualmente no navegador nos dois cenários.

> **Nota sobre o teste:** a primeira versão de `testar_area_casa.py` acusou
> falha em 2 casos que estavam **certos** — eu procurava `"area do TERRENO"`
> sem acento e não incluí `"sem dado"` na lista de valores válidos. O teste
> estava errado, não o código. Vale conferir no navegador antes de "corrigir"
> o que o teste aponta.

> **Aviso honesto:** não sou advogado e isto não é parecer jurídico. É a leitura
> dos fatos técnicos medidos. Antes de abrir o site ao público, vale uma
> consulta — a exposição principal (fotos) tem solução técnica simples
> (linkar em vez de copiar), que já elimina a maior parte do risco sem custo.

---

## 3. Arquitetura recomendada

### 3.1 A decisão que muda tudo: `site.db` de 46 MB

Com 46 MB, **SQLite é a escolha certa** e todo o resto simplifica:

- cabe no `/tmp` do Lambda (512 MB–10 GB) com folga;
- o deploy é **copiar um arquivo**, não migrar dados;
- não há banco gerenciado para pagar;
- leitura é local (sem latência de rede por consulta).

### 3.2 Diagrama

```mermaid
flowchart TB
    subgraph EVENT["⏰ EventBridge (agendador)"]
        E1["diário 04:00 BRT<br/>coleta + visão"]
        E2["1º dia do mês<br/>ITBI + índices"]
        E3["domingo<br/>GeoSampa + derivados"]
    end

    subgraph CRAWL["🕷️ Coleta — EC2 t4g.small (Spot) sob demanda"]
        C1["crawlers<br/>4 portais"]
        C2["detalhes + fotos<br/>S3 apenas se hospedar"]
    end

    subgraph IA["🧠 Visão DeepSeek — Lambda"]
        V1["só anúncios NOVOS<br/>+ dedup por grupo<br/>+ hash-gating"]
    end

    subgraph DADOS["📥 Dados externos — Lambda"]
        D1["ITBI mensal"]
        D2["IPCA/IGP-M"]
        D3["GeoSampa"]
    end

    subgraph DERIVA["🔧 Derivação — Lambda"]
        P1["pHash duplicatas"]
        P2["comparações ITBI"]
        P3["valor venal / modelo"]
    end

    subgraph PUB["📤 Publicação"]
        G1["gera site.db 46 MB"]
        G2["converte miniaturas"]
        G3["S3: site.db + miniaturas"]
    end

    subgraph SITE["🌐 Site"]
        L1["Lambda + Function URL<br/>Flask via Mangum"]
        L2["CloudFront<br/>cache + HTTPS"]
    end

    E1 --> C1 --> C2 --> V1
    E1 --> PUB
    E2 --> D1 & D2
    E3 --> D3
    C2 --> P1
    D1 --> P2
    D2 --> P3
    P1 & P2 & P3 --> G1
    G1 --> G2 --> G3
    G3 -->|baixa na inicialização| L1
    L1 --> L2
```

### 3.3 Por que não as alternativas óbvias

| alternativa | por que não |
|---|---|
| **RDS** | o free tier de RDS é **12 meses, não "sempre grátis"**. E 46 MB não precisa de banco gerenciado |
| **DynamoDB** | o motor do site é SQL com `JOIN`/`LIKE` em SQLite. Reescrever tudo para NoSQL é semanas de trabalho por zero de benefício em 46 MB |
| **ECS/Fargate** | sem free tier real (0,5 GB·h não cobre um serviço rodando 24/7) |
| **Lambda com Playwright** | o pacote Chromium não cabe nos 250 MB do zip. Por isso a coleta fica em **EC2 Spot**, não em Lambda |
| **EventBridge Scheduler "grátis"** | são 14 milhões de invocações grátis, mas **agendamentos têm cobrança própria** (~US$ 0,01/dia por schedule ≈ R$ 1,60/mês cada). Com 3 schedules, ~R$ 5/mês. *Valor a confirmar no console* |

---

## 4. Economia de tokens

### 4.1 Onde o dinheiro está indo

No cenário de R$ 29,04:

- **entrada: quase tudo.** 57.402 fotos × 458 tok = 26,3M tokens de imagem,
  contra 4,3M do prompt. A foto domina.
- **`detail: original` custa 2,26× o `low`** (458 vs 203 tok).
- **a cauda de fotos custa caro:** 14% dos anúncios têm >25 fotos.
- **60% da base é anúncio duplicado** (2.857 de 4.793): **a mesma casa é
  analisada até 25 vezes.**

### 4.2 As alavancas, ordenadas por ganho

| # | alavanca | ganho | risco |
|---|---|---|---|
| 1 | **Analisar 1× por grupo de duplicata** (reaproveitar a análise do principal) | R$ 29,04 → **R$ 12,43** (−57%) | nenhum — já é o que `analisar_visual.py` faz no comentário, mas não na prática |
| 2 | **`detail: low`** em vez de `original` | −41% na entrada | **perde detalhe fino** (mofo, rachadura). Foi o motivo de usar `original` |
| 3 | **Off-peak sempre** (01–04h e 06–10h UTC) | −50% | nenhum, só agendar |
| 4 | **Teto de fotos** (`VISAO_MAX_FOTOS`) + amostragem | corta 45% das fotos | já existe `_amostrar_fotos`; a ordem da galeria põe quintal no fim, então a amostragem **é melhor** que as N primeiras |
| 5 | **Não reanalisar o que já foi analisado** | essencial no diário | nenhum |
| 6 | Deduplicar foto repetida antes de enviar | **0,6%** (medido) | não compensa — ver 4.2.1 |

**Recomendação:** ligar 1, 3, 4 e 5 agora (risco zero, leva de R$ 29,04 para
~R$ 12). Deixar o `detail` como está até **medir** `low` vs `original` num
conjunto com verdade conhecida — o comentário do código já avisa que nunca foi
comparado.

#### 4.2.1 Fotos repetidas: medido, e o número é pequeno

Antes de otimizar, medi se foto repetida compensa — com **md5 dos bytes reais**
de todos os 57.392 arquivos (63 s de leitura de disco), não por semelhança.

| medida | valor |
|---|---|
| imagens distintas (md5 únicos) | **55.104** de 57.392 |
| fotos repetidas na base | **2.288 (4,0%)** |
| fotos repetidas **dentro do mesmo anúncio** | **318 em 138 anúncios** |
| economia no envio ao modelo, deduplicando | **318 fotos = 0,6%** |

**Por que o ganho é tão pequeno:** o modelo **precisa** analisar a mesma
imagem em anúncios diferentes (são imóveis distintos que por acaso usam a
mesma foto). E das repetidas, a maioria **nem é foto de imóvel**:

```
   34x em 34 anúncios | 1200x330px | 41 KB   <- formato de BANNER
   33x em 33 anúncios | 601x900px  | 85 KB
   33x em 33 anúncios | 894x900px  | 69 KB
   ...todas aparecendo em 20 a 34 BAIRROS E RUAS DIFERENTES
```

Uma imagem que aparece em 32 ruas distintas **não é a mesma casa** — é imagem
genérica da imobiliária (o mesmo falso positivo já documentado no pHash).
Total: 957 linhas (1,7%) em imagens repetidas em 3+ anúncios.

**Conclusão:** dedup por md5 custa 63 s de leitura, exige mais uma coluna em
`fotos` e economiza 0,6% — **não vale**. O ganho está em não reenviar a mesma
casa 25 vezes (alavanca 1), que é duplicata de **anúncio**, não de foto.

> **Nota de método:** o banco **não guarda** hash de foto (`fotos` é só
> `id, anuncio_url, foto_url, arquivo_local`); o pHash é recalculado a cada
> execução de `achar_duplicatas.py`. Se um dia valer evitar esse recálculo
> (~11 min), é ali que se grava — mas por 0,6% não se paga.

### 4.3 Guard-rails (o que impede o gasto descontrolado)

O ponto cego hoje é que **a cobertura medida é de 38% (1.829 de 4.793)** e não
há registro de quando cada anúncio foi analisado *com qual configuração*. Isso
já causou: a base cresceu de 178 para 4.793 e ninguém percebeu que a análise
ficou para trás.

Proposta — tabela `visao_ledger` (controle de custo e idempotência):

```sql
CREATE TABLE visao_ledger (
  anuncio_url  TEXT,
  modelo       TEXT,      -- deepseek-flash
  detalhe      TEXT,      -- low | original
  n_fotos      INTEGER,
  tok_entrada  INTEGER,
  tok_saida    INTEGER,
  usd          REAL,
  executado_em TEXT,
  PRIMARY KEY (anuncio_url, modelo, detalhe, n_fotos)
);
```

- **Chave primária composta** = rodar de novo não repaga o mesmo anúncio com a
  mesma configuração.
- Trocar de `low` para `original` **conta como trabalho novo** (é legítimo), mas
  fica registrado e comparável.
- Um `SELECT SUM(usd) WHERE executado_em > ...` mostra o gasto do dia, e o
  pipeline **aborta** se passar de um teto (`VISAO_ORCAMENTO_USD`).

---

## 5. Os pipelines

### 5.1 Cadência por tipo de dado

| dado | cadência | por quê | custo diário medido |
|---|---|---|---|
| **anúncios + fotos** | **diário** | preço/estoque mudam todo dia; é o valor do produto | 0 |
| **análise visual** | diário, **só novos** | acompanha os anúncios | 20 novos = **R$ 0,11–0,22**; 100 novos = R$ 0,57–1,09 |
| **ITBI** | **mensal** | a prefeitura publica por mês (medido: 21 anos disponíveis, arquivo novo mensal) | 0 |
| **IPCA / IGP-M** | **mensal** | o índice muda uma vez por mês | 0 |
| **GeoSampa (lotes)** | **domingo** (semanal) | o cadastro muda devagar; baixar 1,6M de lotes é caro em banda | 0 |
| **pHash + duplicatas** | diário, **só o delta** | anúncio novo pode duplicar um antigo; não precisa reprocessar tudo | 0 |
| **comparações ITBI + venal** | **após** ITBI e após os anúncios | é derivado dos dois | 0 |
| **site.db** | **após qualquer um acima** | publica o resultado | 0 |
| **miniaturas** | diário, só fotos novas | alimenta o site | 0 |

**A resposta ao "os outros dados precisam ver a latência":** só os **anúncios**
precisam ser diários. ITBI e índices são **mensais por natureza da fonte** —
rodar diário não traria dado novo e só gastaria banda. GeoSampa é semanal.

### 5.2 Estágio 0 — Coleta (EC2 Spot)

Ordem por bairro, respeitando `crawl-delay`:

```
para cada bairro em config.BAIRROS:
    crawler do portal  ->  upsert por URL (chave primária, nunca duplica)
```

- **Retomada obrigatória:** cada etapa grava seu progresso; interromper e rodar
  de novo não refaz trabalho. O projeto já faz isso em `baixar_fotos` e
  `ingerir_itbi` — replicar nos outros.
- **Rate limit é real:** medido anteriormente, a OLX bloqueia por horas após
  ~60 páginas. Lotes de ~50 com pausa.
- **Ponto de quebra:** o `playwright-profile/` (101 MB) guarda cookies. Num
  servidor isso precisa de disco persistente — por isso EC2 e não Lambda.

### 5.3 Estágio 1 — Normalização (Lambda)

Tudo o que não gasta API:

```
herdar_endereco.py      (consenso de rua/CEP dentro do grupo — custo zero)
endereco.py             (chave_tolerante, normalizar_cep)
tipo_do_anuncio()       (descarta apartamento)
```

**Determinístico e idempotente.** Rodar 10× dá o mesmo resultado.

### 5.4 Estágio 2 — Visão (Lambda) — *o estágio que gasta*

```
selecionar anúncios onde:
    foto_analisada_em IS NULL
    OU (modelo/detalhe/n_fotos) mudou em relação ao ledger
ordenar do mais recente para o mais antigo
agrupar por dup_grupo, analisar só o PRINCIPAL, propagar para as cópias
respeitar VISAO_MAX_FOTOS (teto) e VISAO_AMOSTRAGEM
rodar em off-peak (01-04h / 06-10h UTC)
abortar se SUM(usd) do dia > VISAO_ORCAMENTO_USD
```

**Idempotência pelo `visao_ledger`** — é o que impede o gasto repetido.

### 5.5 Estágio 3 — Dados externos (Lambda)

| tarefa | cadência | cuidado |
|---|---|---|
| `itbi.py` → `descobrir_links()` → baixar | mensal | a URL muda de mês em mês; a função resolve, mas **testar a cada mês** |
| `ingerir_itbi.py` | após o download | mapeia colunas **por nome** (2006 tem layout diferente) |
| `indices.py` (IPCA; IGP-M agora disponível) | mensal | cachear em `dados/indices.json` |
| `baixar_cadastro.py --completar` | semanal | retomar pelos blocos finais (senão regasta 13 min) |

### 5.6 Estágio 4 — Derivação (Lambda)

```
achar_duplicatas.py --marcar      (com a trava de 2+ ruas / 2+ bairros)
comparar_itbi.py --refazer        (preenche area_terreno também)
valor_venal.py                    (razão por região)
```

> **Ordem importa:** `pHash` **antes** de `comparar_itbi`, porque a comparação
> aproveita a herança de endereço que o pHash destrava.

### 5.7 Estágio 5 — Publicação (Lambda)

```
1. gerar site.db  (só as 6 tabelas da tela + os 7 índices)   -> 46 MB
2. converter miniaturas 400px das fotos novas
3. subir site.db e miniaturas para o S3
4. invalidar o cache do CloudFront
```

O site baixa o `site.db` na inicialização (cold start) ou o mantém em `/tmp`.

---

## 6. AWS: limites e custos

### 6.1 Mapa de serviços

| serviço | para quê | free tier | nosso uso |
|---|---|---|---|
| **Lambda** | site, visão, dados externos, derivação | 1M req + 400.000 GB·s/mês | folgado |
| **S3** | `site.db` (46 MB) + miniaturas (~349 MB se gerar) | 5 GB + 20.000 GET / 2.000 PUT | cabe |
| **CloudFront** | cache, HTTPS, CDN | 1 TB + 10M req/mês | cabe (medido: 191k páginas cheias) |
| **EventBridge** | agendador | invocações grátis | ⚠️ schedules podem ter custo (~R$ 1,60/mês cada) |
| **EC2 t4g.small** | coleta (precisa de Chromium + disco) | **750 h/mês de t2/t3.micro até 2025** | ⚠️ ver 6.2 |
| **ECR / logs** | imagens, CloudWatch | 500 MB logs | vigiar |

### 6.2 Limites que apertam — conferir no console

Estas três **mudaram recentemente e não consegui confirmar na documentação**.
São as que decidem o custo real:

1. **O free tier de EC2 de 750 h/mês é válido só para `t2.micro`/`t3.micro`** —
   a família `t4g` (ARM, mais barata) pode não estar coberta. Uma `t3.micro`
   com Chromium faz a coleta, mas com pouca memória (1 GB).
2. **O free tier de 12 meses x "sempre grátis":** Lambda/S3/CloudFront são
   "sempre grátis"; EC2 e RDS são **só nos primeiros 12 meses**. O plano não
   depende de RDS, mas depende de EC2.
3. **Cobrança de schedule** no EventBridge Scheduler.

**Mitigação se o EC2 sair do free tier:** a coleta roda **sob demanda**, não
24/7. Uma `t3.small` Spot por ~1 h/dia custa na casa de **US$ 1–3/mês**
(~R$ 5–16). Continua dentro do "até R$ 20".

### 6.3 Custos fora do free tier (estimados)

| item | estimativa |
|---|---|
| EC2 Spot, 1 h/dia | R$ 5–16/mês |
| EventBridge, 3 schedules | ~R$ 5/mês |
| CloudWatch Logs (retenção 7 dias) | < R$ 2/mês |
| Tráfego de saída da EC2 (não via CloudFront) | variável — **manter tudo atrás do CloudFront** |
| Domínio (opcional) | ~R$ 40/ano (.com.br) |
| **DeepSeek, base inteira uma vez** | **R$ 12,43** (com as alavancas) |
| **DeepSeek, manutenção diária** | **R$ 3–17/mês** (20–100 novos/dia) |

**Total realista: R$ 25–45/mês**, sendo a maior parte o DeepSeek — **não** a AWS.
Isso responde a preocupação central: a AWS não é o custo dominante; o
processamento de imagem é, e ele é controlável.

---

## 7. Estratégia de branches

### 7.1 Regra

> `master` **só recebe o que passou por verificação**. Nada entra direto.
> Cada branch tem um critério objetivo de saída (testes passando, número
> medido, revisão legal) — não "acho que está pronto".

### 7.2 As branches e o que entra em cada uma

Ordem importa: as primeiras produzem os *helpers* que as seguintes reusam.

| # | branch | conteúdo | critério de saída |
|---|---|---|---|
| 1 | **`chore/limpeza-raiz`** | mover os ~35 scripts soltos da raiz (o padrão do projeto é atalho de 2 linhas + `src/`); tirar os 3 `.db` de backup do disco; ampliar o `.gitignore` para `saida_*.txt`, `custo.txt`, `deploy.txt`, `site_db.txt`, `sonda.txt` | `git status` limpo, testes passando, raiz com ≤ ~15 arquivos |
| 2 | **`plano/deploy-aws`** | este documento + `experimentos/sonda_fontes.py`, `experimentos/medir_deploy.py`, `experimentos/medir_site_db.py`, `experimentos/custo_visao.py` | plano revisado por você |
| 3 | **`feat/visao-custo`** | `VISAO_MAX_FOTOS` ≠ 0, `VISAO_ORCAMENTO_USD`, tabela `visao_ledger`, 1× por grupo, agendamento off-peak | rodar em 50 anúncios e **medir** US$ gasto ≈ previsto |
| 4 | **`dados/indice-igpm`** | corrigir a memória do projeto (o IGP-M **funciona**; o "NXDOMAIN" era certificado self-signed), `indices.py` com escolha de índice | dispersão intrarrua ≤ a do IPCA (já medido: 17,4% vs 18,6%) |
| 5 | **`feat/pipeline-diario`** | scripts agendáveis, retomada, `config.CADENCIA`, EventBridge | rodar 3 dias seguidos sem intervenção |
| 6 | **`feat/site-publico`** | `LinkSource` (linkar CDN em vez de copiar), **truncar descrição** + link "ver no portal", `robots.txt` + `noindex`, página de *takedown*, miniaturas 400px | revisão legal + `site.db` < 50 MB + página < 2,5 MB |

### 7.3 Como fazer o merge

```bash
# 1. parte de master atualizada
git checkout master && git pull

# 2. branch curta, com um assunto só
git checkout -b feat/visao-custo

# 3. commits pequenos -- um por mudança lógica, não um commit gigante
# 4. antes de abrir o PR, rebase (não merge) para o histórico ficar linear
git rebase master

# 5. valide ANTES de subir
.\.venv\Scripts\python.exe tests\testar_web.py
.\.venv\Scripts\python.exe tests\testar_tipo.py

# 6. só então empurra a branch
git push -u origin feat/visao-custo
# 7. merge em master (squash) somente depois do critério de saída cumprido
```

### 7.4 Estado atual do repositório (medido)

```
branch: master (limpa, sincronizada com origin/master)
96 arquivos rastreados · .git = 0,86 MB · nenhum segredo publicado
8 arquivos NÃO rastreados na raiz:

  experimentos/conferir_todos.py          -> chore/limpeza-raiz  (ferramenta útil, catalogar)
  experimentos/medir_igpm.py              -> dados/indice-igpm
  experimentos/testar_indice.py           -> dados/indice-igpm
  experimentos/testar_download_lotes.py   -> chore/limpeza-raiz
  saida_igpm.txt             -> IGNORAR (saída gerada)
  saida_indice.txt           -> IGNORAR (saída gerada)
  log_dup.txt                -> IGNORAR (já coberto por *.log)
  log_web.txt                -> IGNORAR (já coberto por *.log)
```

**O `.gitignore` já está correto**: 0 de banco, fotos, perfil de navegador ou
`.env` versionados. Confirmado com `git ls-files`.

---

## 8. Ordem de execução

### Fase 0 — Limpeza (pré-requisito)
Sem ela, todo diff futuro vira ruído. Branch `chore/limpeza-raiz`.

### Fase 1 — Economia de tokens (não depende da AWS)
Branch `feat/visao-custo`. **Dá retorno imediato e reduz o custo antes de
automatizar qualquer coisa.** Ligar: 1× por grupo, off-peak, teto de fotos,
ledger. Medir em 50 anúncios antes de soltar na base.

### Fase 2 — Fechar a lacuna dos 2.964
Custo medido: **R$ 9,25** (teto 12, `low`) a **R$ 14,92** (todas, `original`).
Como a análise é o insumo da nota de encaixe (JEV), fechar a lacuna é o que
destrava o item 3 do `requerimentos.md`.

### Fase 3 — Conta AWS e limites
Criar a conta, **conferir no console** os três pontos da seção 6.2 (cobertura
do `t4g`, prazo do free tier, custo de schedule). Só então escolher a
instância da coleta.

### Fase 4 — Site atrás do CloudFront
Lambda + Mangum + `site.db` no S3. Medir cold start e tempo de rota.

### Fase 5 — Pipeline diário
Branch `feat/pipeline-diario`. Rodar 3 dias em modo sombra (gera, não publica)
antes de ligar de verdade.

### Fase 6 — Endurecimento legal
Branch `feat/site-publico`, **antes** de divulgar o site.

### O que é reversível e o que não é

| ação | reversível? |
|---|---|
| Ligar/desligar agendamento | ✅ |
| Trocar `detail` e teto de fotos | ✅ (só dinheiro já gasto, registrado no ledger) |
| Baixar dado público de novo | ✅ |
| **Publicar descrição/foto de terceiro** | ⚠️ **não** — uma vez público, pode ter sido copiado |
| **Contornar 403 com proxy** | ⚠️ **não** — cria histórico |

**A regra prática:** tudo que é técnico é reversível; o que é **publicação** não
é. Por isso o site vem **depois** da análise de custo e do endurecimento legal,
nunca antes.

---

## 9. Ainda em aberto (depende de decisão sua)

Nenhuma destas foi respondida — não travei o plano por causa delas, escolhi a
opção mais conservadora provisoriamente:

1. **Público do site:** assumi **"só você / link não divulgado"**, que é o
   cenário de menor risco. Se for público em geral, a Fase 6 deixa de ser
   opcional.
2. **Fotos:** assumi **linkar do CDN** (opção A). Se preferir hospedar, some
   ~R$ 0,65/mês de S3 pelas miniaturas — o custo é irrelevante, **o risco não**.
3. **Teto de custo:** assumi **R$ 0 de AWS + DeepSeek**. O plano não usa nada
   pago sem fallback.
4. **`detail: low` vs `original`:** não troquei. Precisa de um teste com verdade
   conhecida — o próprio código registra que nunca foi comparado.