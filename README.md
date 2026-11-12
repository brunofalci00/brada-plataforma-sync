# brada-plataforma-sync

Sync diario **Plataforma Brada (Firestore + Firebase Auth) -> Google Sheets** pra alimentar o dashboard Looker "Funil & Plataforma" da gerencia (Tamyris).

Padrao espelha `brada-hubspot-sync` e `brada-clickup-sync`. Documentacao completa do projeto no vault Obsidian: `01_Projetos/Dashboard_Funil_Plataforma/`.

## O que escreve (Sheet `Brada_Dashboard_Plataforma`)

| Aba | Modo | Conteudo |
|---|---|---|
| `raw_users` | overwrite | 1 linha/usuario (pseudonimizado): cadastro, role, atribuicao UTM, acesso (Auth + `lastLogin`), projetos |
| `raw_projects` | overwrite | 1 linha/projeto: status, expiracao CAC (vigente/expirado/sem_data), budget, ODS/UF |
| `raw_proposals` | overwrite | 1 linha/proposta: status, edital, valor aprovado (R$), datas |
| `snap_diario` | append idempotente | serie historica em formato longo (data, metrica, segmento, valor) |
| `raw_tendencia_diaria` | overwrite | pivot de `snap_diario` (aptos a captar, usuarios ativos, leads reais) ja em formato largo, so pra alimentar o grafico de linha nativo da aba Plataforma Brada |
| `meta_sync` | overwrite | ultima execucao, contagens, avisos de schema drift |

Sprint 2 adiciona `raw_funil_automatize` (HubSpot `trabalhado_por=Automatize`) + motor de atribuicao 3 camadas.

## Sprint 3 — AprovAI e SDR Incentivador (scripts separados, mesma Sheet)

| Script | Aba(s) | Fonte | Cron |
|---|---|---|---|
| `sync_aprovai.py` | `AprovAI`, `raw_asaas_aprovai`, `raw_funil_aprovai` | Asaas (cobrancas filtradas por `is_aprovai_transaction`, porta exata da regra do app `iasmartsites/aprovai2`) + Firestore do projeto `aprovai-de156` (`analytics_funnel_events`) | Junto do `sync.py`, mesmo workflow |
| `sync_sdr_incentivador.py` | `SDR Incentivador`, `raw_hubspot_incentivador` | HubSpot, pipeline Incentivador (`default`): cards por `trabalhado_por`, quem chega em `[EV] Reuniao Agendada` ou alem e por qual caminho (automatico vs manual) | Junto do `sync.py`, mesmo workflow |
| `sync_disparo_incentivador.py` | `Disparo Incentivador`, `raw_disparo_incentivador` | Banco Postgres do SDR Brada (Automatize, so leitura, schema `dados`): funil da Brenda (envio -> abertura -> clique -> resposta -> agenda -> card no HubSpot) + estagio ATUAL de cada card, consultado ao vivo no HubSpot (o estagio salvo no banco da Automatize e so o de quando o card foi criado) | Junto do `sync.py`, mesmo workflow |

PII do Asaas (nome/e-mail/CPF do cliente) e usada SO em memoria pra classificar
teste-ou-staff; nunca vira coluna. Identificador publicado e `sync.hash_id`
(sha256[:12]), mesma convencao do resto do repo. `dealname`/`deal_id` do HubSpot
nao entram nessa regra (registro de negocio B2B, nao pessoa fisica).

Rodar local:
```
python sync_aprovai.py --dry-run
python sync_sdr_incentivador.py --dry-run
python sync_disparo_incentivador.py --dry-run
```
Secrets adicionais: `ASAAS_APROVAI_API_KEY` (local: `~/.brada-secrets/asaas-aprovai.env`),
`COMUNIDADE_SA_JSON` (local: `~/.brada-secrets/comunidade-dashboard-sa.json` - conta dashboard-leitura,
reusada pelo Firestore do AprovAI e pelo Google Analytics; a antiga SA dedicada `aprovai-sa.json` nao
e mais usada desde 11/11),
`HUBSPOT_TOKEN` (local: `~/.brada-secrets/hubspot.env`, mesmo token do `brada-hubspot-sync`),
`AUTOMATIZE_SDR_DB_HOST/PORT/NAME/USER/PASSWORD` (local: `~/.brada-secrets/automatize-sdr-brada.env`,
banco so leitura liberado pelo Jean/Automatize em 11/11).

## Graficos nativos (`dashboard_charts.py`)

Achado do audit de dados de 11/11: nenhuma aba usava grafico nativo do Sheets, so card de numero.
`dashboard_charts.publish_line_chart()` desenha um grafico de linha (`addChart`) a partir de uma
aba `raw_*_diario` ja em formato largo (1a coluna = dia, demais = series numericas), reaproveitado
por `sync_comunidade.py`, `sync_primeira_fila.py`, `sync_aprovai.py` (funil) e `sync.py` (pivot de
`snap_diario`). Idempotente: apaga o(s) chart(s) existente(s) na aba de destino antes de redesenhar,
e redimensiona a aba se faltar linha pra caber a altura inteira do grafico (senao o Sheets clampa e
sobrepoe conteudo). Valor que volta de `read_existing` (historico) chega como string; quem monta a
serie precisa converter pra numero de verdade, senao o Sheets descarta a serie inteira do grafico
sem avisar (`sync.build_tendencia_diaria`/`_numero_ou_vazio` cobre isso pro pivot da Plataforma).
`SDR Incentivador` e `Disparo Incentivador` ainda nao tem grafico: nenhum dos dois guarda historico
diario hoje, so foto atual (`write_overwrite`).

## Legibilidade do `dashboard_kpi_blocks.py` (achado do Bruno 12/11)

Rotulo de metrica e nota de secao cortavam no meio da frase em TODAS as abas deste modulo (Visão
Geral, Comunidade, AprovAI, SDR Incentivador, Disparo Incentivador, Primeira Fila - so a Plataforma
Brada, com layout proprio em `dashboard_analytics.py`, nao tinha o problema). Causa: nenhuma celula
desse modulo setava `wrapStrategy: WRAP` - aumentar a altura da linha sozinho (o que a 1a tentativa
de correcao fez) nao adianta nada se o texto nunca quebra de linha. Corrigido: `_cell(..., wrap=True)`
em rotulo/nota/subtitulo/titulo de secao, mais altura de linha generosa (`ALTURA_ROTULO`/`ALTURA_NOTA`)
e coluna mais larga (115px, era o default 100px do Sheets). Revalidado nas 6 abas apos a correcao.

## PII (inegociavel)

- Serializacao whitelist; `email/name/phone/document/uid` NUNCA saem pra Sheet nem pra log.
- Identificadores publicados sao `sha256(id)[:12]`.
- Guard pre-publicacao varre todas as celulas com regex de e-mail/CPF/CNPJ e **aborta o run** se bater (exit 1 = step vermelho no Actions).
- O futuro join por e-mail HubSpot x Firestore acontece em memoria, nunca na Sheet.

## Rodar local

Secrets locais em `~/.brada-secrets/` (`firebase-sa.json`, `sheets-sa.json`, `plataforma-sync.env` com `SPREADSHEET_ID=...`).

```
python sync.py --dry-run   # le tudo, imprime distribuicoes, nao escreve
python sync.py             # escreve nas 5 abas
```

## GitHub Actions

Cron diario `15 9 * * *` (06:15 BRT) + `workflow_dispatch` manual.

Secrets do repo (Settings -> Secrets and variables -> Actions):

| Secret | Valor |
|---|---|
| `FIREBASE_SERVICE_ACCOUNT_JSON` | conteudo de `~/.brada-secrets/firebase-sa.json` |
| `GOOGLE_SERVICE_ACCOUNT_JSON` | conteudo de `~/.brada-secrets/sheets-sa.json` |
| `SPREADSHEET_ID` | id da Sheet `Brada_Dashboard_Plataforma` |

## Gotchas

- Firestore: database NOMEADO `ai-studio-93e1b1b8-...` (o `(default)` existe e esta VAZIO).
- `projects.createdAt` e STRING; `users.createdAt` e Timestamp — `to_date()` cobre os dois.
- Datas truncadas em America/Sao_Paulo (BRT) antes de virar `AAAA-MM-DD`.
- Acesso = o MAIS RECENTE entre o `lastLogin` do doc em `users` e o `lastSignInTimestamp` do Firebase Auth (`sync.load_login_index`). O Auth sozinho so registra LOGIN, entao quem volta com a sessao viva congela nele: em 08/09/2026 isso dava 21 pessoas como paradas ha 30+ dias tendo acessado dentro da janela, e o `ativo_30d` da planilha marcava 49 em vez de 70.
- `snap_diario` e a UNICA fonte de serie historica (Firestore so tem estado atual): rodar manual 1x/dia ate o cron subir.
