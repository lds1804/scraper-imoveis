# Plano de deploy na AWS — backend + crawler para um app Android

> Revisão de `plano-deploy-aws.md` (2026-10-07). Substitui a arquitetura
> daquele plano; os números de custo de visão e a seção legal continuam valendo.
> **O que é medido** está marcado como tal; o resto é *estimado* ou *a confirmar
> no console da AWS* — preços e limites da AWS mudam e não foram conferidos.

## 1. O que muda em relação ao plano anterior

| Plano anterior | Agora | Motivo |
|---|---|---|
| Site HTML em Lambda + Mangum + CloudFront | **API JSON** | o consumidor é o app, não um navegador |
| Pipeline de 6 estágios em Lambdas + EventBridge | **1 servidor + `caca-atualizar` por cron** | `atualizar.py` já encadeia todas as etapas; fatiar em Lambdas é trabalho sem ganho |
| Publicar `site.db` no S3 a cada rodada | **API lê o próprio banco** | sem etapa de publicação, sem janela em que o app vê dado velho |
| Miniaturas de fotos no S3 | **App carrega a foto direto do CDN do portal** | já era a opção A do plano antigo; menos custo e menos exposição de direitos autorais |
| Site público ou privado (dúvida em aberto) | **API privada: só o app** | resolve a pendência 1 do §9 antigo |

## 2. O que existe hoje (lido no código)

- `webapp.py` é Flask **só com rotas HTML**: `/`, `/anuncio/<url>`, `/fotos/<caminho>`.
  **Não há JSON nem autenticação.** O app Android não consegue consumir isso.
- Os filtros e a consulta já estão separados da tela em `filtros.py`
  (`Filtros.da_url`, `montar_consulta`) — dá para reaproveitar na API sem copiar SQL.
- `atualizar.py` (`caca-atualizar`, `--completo` para as mensais) roda: backup,
  coleta incremental, fotos, duplicatas, endereço, visão, ITBI, venal, área.
  Segue adiante quando uma etapa falha. **É o job do crawler, pronto.**
- `CACA_AMBIENTE=producao` já liga o modo de produção (desliga a ponte do
  navegador, não abre o debugger do Werkzeug).
- Config por variável de ambiente: `CACA_DB`, `CACA_FOTOS`, `CACA_VISAO`.
- **`imoveis.db` está com ~975 MB hoje** (o plano antigo citava 638 MB;
  `site.db` tem 46 MB). Em SQLite com um único escritor, isso é aceitável em disco EBS.
- Playwright/Chromium é usado em `scraper_browser`, `olx`, `quintoandar`, `zap`
  e `main` (Imovelweb): **a coleta precisa de Chromium**. Não cabe em Lambda.

## 3. Arquitetura

```
 Android ──HTTPS──> CloudFront ──HTTP + segredo──> EC2 (1 instância)
                    (cert grátis)                  ├─ gunicorn: API JSON (somente leitura)
                                                   ├─ cron 04:00: caca-atualizar
                                                   ├─ cron mensal: caca-atualizar --completo
                                                   └─ EBS: imoveis.db  (fotos NÃO ficam aqui)
                                                        │
                                              S3: backups do banco (retenção 7 dias)
```

**Uma instância só.** O crawler escreve no SQLite e a API lê; no mesmo disco isso
funciona sem rede nem sincronização, e o WAL do SQLite deixa a API ler durante a
escrita. Separar em duas máquinas obrigaria a copiar ~1 GB por rodada.

### Por que não as alternativas

| alternativa | por que não |
|---|---|
| Lambda para a API | cold start baixando banco de 1 GB; o SQLite precisaria de EFS (pago) |
| RDS / DynamoDB | reescrita do SQL (`JOIN`/`LIKE`) por zero ganho; RDS free tier é só 12 meses |
| Fargate | sem free tier útil para serviço 24/7 |
| Crawler em Lambda | Chromium não cabe no zip de 250 MB |
| API Gateway | funciona, mas CloudFront na frente do EC2 dá HTTPS grátis **sem domínio** |

### HTTPS é obrigatório
O Android bloqueia HTTP puro desde a API 28 (cleartext). Duas saídas:
**(a)** CloudFront na frente (domínio `*.cloudfront.net`, certificado grátis, sem
comprar domínio) — recomendada; **(b)** domínio próprio + Caddy/Let's Encrypt no EC2.

## 4. A API (o trabalho de código que falta)

Novo módulo `src/cacaimoveis/api.py`, blueprint Flask montado em `/api/v1`,
reaproveitando `filtros.py` e as funções `_comparacoes`, `_areas_oficiais`,
`_venais`, `_venais_iptu`, `_copias_do_anuncio` hoje privadas em `webapp.py`
(extrair para um módulo comum em vez de duplicar).

| rota | retorno |
|---|---|
| `GET /api/v1/anuncios?bairro=&preco_max=&pagina=&limite=` | lista paginada (cursor ou página), campos enxutos |
| `GET /api/v1/anuncios/{id}` | detalhe: comparação ITBI, área oficial, venal, IPTU, cópias, URLs das fotos |
| `GET /api/v1/bairros` | bairros disponíveis (com os prioritários primeiro, como na tela) |
| `GET /api/v1/status` | data da última atualização, contagens — o app mostra "atualizado há X h" |
| `GET /health` | sem autenticação, para o monitor |

Decisões que o código atual obriga a tomar:

- **ID do anúncio:** hoje a chave é a URL (`/anuncio/<path:url>`). Para API, usar
  um id estável curto (hash da URL) e devolver também a URL original. *Decisão pendente: adicionar coluna `id` ou calcular o hash na consulta.*
- **Fotos:** devolver a URL do CDN do portal (tabela `fotos`); remover a rota
  `/fotos/` do deploy. O app usa Coil/Glide direto. Medido no plano antigo: há 10
  `.svg` na tabela `fotos` que precisam ser filtrados (§2.8 antigo).
- **Autenticação:** token estático (`Authorization: Bearer`) comparado em tempo
  constante, mais um cabeçalho secreto que o CloudFront injeta para o EC2 só
  aceitar tráfego vindo dele. Cognito é exagero para um usuário. Guardar os dois
  segredos no **SSM Parameter Store** (grátis, padrão), não no repositório.
  Nunca embutir o token no APK em texto claro sem saber que ele é extraível:
  para uso próprio é aceitável; se o app for distribuído, precisa de login por usuário.
- **Somente leitura:** abrir o SQLite com `?mode=ro` na API.
- **Limite de resposta:** `limite` máximo 50; sem isso, a listagem sem filtro
  traz 4.793 anúncios de uma vez.
- **Testes:** `tests/test_api.py` na mesma linha de `tests/test_web.py`, já com
  a fábrica de banco em `tests/fabrica.py`.

## 5. O crawler

Roda no mesmo EC2, via `cron` (ou `systemd timer`, que registra falhas melhor):

```
0 4 * * *   caca-atualizar                      # diário
0 3 1 * *   caca-atualizar --completo           # mensal (ITBI, IPTU, ajustes)
```

Pontos que o código atual já cobre e os que faltam:

- ✅ Coleta **incremental** (`incremental.py`): medido 60 min → poucos min por
  rodada. Respeita `GLUE_DELAY_S = 10` (crawl-delay do ZAP/VivaReal).
- ✅ Backup antes de tudo (`backup.py`). **Falta** enviar o backup ao S3.
- ✅ Etapas independentes: uma falha não derruba as seguintes.
- ⚠️ **Travar execução concorrente** (`flock`): duas rodadas simultâneas
  escrevendo no mesmo SQLite. Hoje não há trava.
- ⚠️ **Visão (DeepSeek):** `atualizar.py` aceita `--provedor deepseek` e `--teto`.
  Em produção usar `CACA_VISAO=deepseek` com teto diário; o provedor `claude`
  depende do Claude Code instalado e de login interativo (`caca-login`) e **não
  roda em servidor**. O commit `8f511fa` já pede confirmação antes de gastar:
  conferir como isso se comporta sem terminal (`--sim`).
- ⚠️ **OLX bloqueia por horas após ~60 páginas** (medido antes) e o Cloudflare
  barra o Imovelweb após ~4 páginas: **IP de datacenter da AWS costuma ser pior
  que IP residencial**. É o maior risco do plano — ver §8, teste 2.
  Não contornar bloqueio com proxy rotativo (decisão do plano antigo: não é reversível).
- ⚠️ `playwright-profile/` (101 MB de cookies) precisa ficar no EBS.
- ⚠️ **Alerta de falha:** hoje só há o resumo no terminal. Enviar o resumo final
  do `atualizar` por SNS (e-mail, grátis) ou ao menos um `/api/v1/status` com
  `ultima_atualizacao_ok` que o app exiba.

## 6. Instância e custo

| item | escolha | custo |
|---|---|---|
| EC2 | **t3.small (2 GB)** ou t3.micro (1 GB) | *a confirmar:* free tier de 12 meses cobre t2/t3.micro; t3.small ~US$ 15/mês on-demand |
| EBS gp3 | 20 GB (banco ~1 GB + backups locais + Chromium + SO) | ~US$ 1,6/mês |
| S3 backups | ~1 GB × 7 dias | centavos |
| CloudFront | HTTPS | free tier "sempre grátis" cobre o uso de um app pessoal |
| SSM Parameter Store | segredos | grátis (padrão) |
| DeepSeek | manutenção diária | R$ 3–17/mês (medido no plano antigo) |

**t3.micro com 1 GB é arriscado:** Chromium (~500–800 MB) + gunicorn + SQLite.
Plano: começar na micro com **2 GB de swap** e medir o pico no teste 3 do §8;
subir para small se passar de ~80% de memória. Alternativa para evitar a briga
de memória: o Chromium só roda às 04:00, quando ninguém usa a API.

Estimativa realista: **R$ 25–60/mês**, dominada por EC2 (após os 12 meses) e DeepSeek.
*A confirmar no console antes de criar:* cobertura do free tier para a família
escolhida, prazo dele, e a região (`sa-east-1` é mais perto mas mais cara que
`us-east-1`; para um app pessoal a latência extra de `us-east-1` é imperceptível).

## 7. Segurança

- Security group: **sem SSH aberto**. Acesso por **SSM Session Manager**.
  Porta 80 só do prefixo gerenciado do CloudFront.
- IAM do EC2: apenas `s3:PutObject` no bucket de backup + leitura dos
  parâmetros SSM necessários. Nada de chaves de acesso no disco.
- Segredos (token da API, chave do DeepSeek) no SSM; **não** em `.env` versionado
  (o repo já tem `tools/conferir_segredos.py` para checar vazamento — rodar no CI).
- Rate limit simples na API (por token) para o caso de o token vazar.
- Orçamento: **AWS Budgets** com alerta a US$ 5/10/20 — é o que impede surpresa.

## 8. Ordem de execução

Cada fase termina com um critério verificável.

**Fase 1 — API local (sem AWS).** Extrair as consultas de `webapp.py`; criar
`api.py` e `tests/test_api.py`. *Saída:* testes passam; `curl` retorna JSON
paginado do banco real; tempo de resposta medido.

**Fase 2 — Teste de risco antes de pagar qualquer coisa.** Conta AWS, instância
t3.micro, **só o crawler**, sem API:
1. instalar (`pip install -e .`, `playwright install chromium`);
2. rodar `caca-atualizar --so coleta` por um bairro e ver se **ZAP, QuintoAndar e OLX respondem** do IP da AWS;
3. medir o pico de memória do Chromium (`/usr/bin/time -v`).
*Saída:* dado concreto para decidir micro × small e saber se o IP de datacenter é bloqueado. Se for, o crawler volta para a máquina local e a AWS fica só com a API + banco recebido por `rsync` — o app Android não muda.

**Fase 3 — API no EC2 atrás do CloudFront.** gunicorn (2 workers) via systemd,
token e segredo do CloudFront no SSM. *Saída:* `curl` com token passa, sem token dá 401, acesso direto ao IP dá recusa.

**Fase 4 — Agendamento.** cron + `flock` + backup no S3 + alerta SNS. Rodar 3
dias em modo sombra (`--so coleta`, sem visão) antes de ligar tudo.
*Saída:* 3 execuções seguidas sem intervenção, `/api/v1/status` correto.

**Fase 5 — Visão em produção.** Ligar `CACA_VISAO=deepseek` com teto diário.
*Saída:* gasto do dia registrado no ledger e abaixo do teto.

**Fase 6 — App Android.** Contrato = o OpenAPI gerado do blueprint (versão `/v1`;
mudança incompatível vira `/v2` para não quebrar APKs já instalados).

### O que é reversível

| ação | reversível? |
|---|---|
| criar/destruir a instância, ligar/desligar o cron | ✅ |
| restaurar o banco a partir do backup S3 | ✅ |
| gasto do DeepSeek já feito | ❌ (mas registrado e com teto) |
| contornar bloqueio de portal | ❌ — não fazer |
| distribuir o app com token embutido | ❌ — trocar o token invalida o APK |

## 9. Pontos em aberto (preciso da sua decisão)

1. **O app é só seu ou será distribuído?** Define se o token estático basta ou se precisa de login por usuário.
2. **O crawler deve rodar na AWS mesmo se o IP for bloqueado?** Se a Fase 2 mostrar bloqueio, a alternativa é coletar no seu PC e subir só o banco.
3. **Teto de custo mensal** (AWS + DeepSeek) — o plano assume até ~R$ 60.
4. **Id do anúncio:** coluna nova `id` no banco (migração em `migracoes.py`) ou hash calculado?
