# Requerimentos — projeto Caça-imóveis

> Lista de tarefas do projeto, organizada por tema.
> Legenda: ✅ feito · 🔶 parcial · ⬜ pendente
>
> Última revisão: 2026-10-02

---

## 1. Coleta de dados (crawlers)

- [x] **Crawler do Imovelweb** funcionando
      Playwright com contexto persistente para passar pelo Cloudflare.
      Coleta 4 bairros: Vila Mangalot, Parque São Domingos, City América e
      Parque Maria Domitila — **178 anúncios**.
- [ ] **Crawler da OLX** nos mesmos bairros.
- [x] **Enriquecimento pela página de detalhe**
      Descrição completa, quartos/banheiros/vagas, financiamento e a
      **galeria completa** (a listagem publica só 1 foto de capa; o detalhe
      traz até 50). Resultado: **4.756 fotos** armazenadas.
- [x] **Validação de localidade**
      Todo anúncio salvo é de São Paulo **e** de um dos bairros-alvo —
      evita tanto o fallback nacional do site quanto bairro errado.
- [ ] **Ampliar a lista de bairros**
      Hoje 21 bairros configurados, mas só 4 retornaram anúncios.
- [ ] **Rodar o crawler diariamente**, de forma automática
      (ver pipeline na AWS, item 5).

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
