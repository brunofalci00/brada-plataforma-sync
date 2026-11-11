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
| `meta_sync` | overwrite | ultima execucao, contagens, avisos de schema drift |

Sprint 2 adiciona `raw_funil_automatize` (HubSpot `trabalhado_por=Automatize`) + motor de atribuicao 3 camadas.

## Sprint 3 — AprovAI e SDR Incentivador (scripts separados, mesma Sheet)

| Script | Aba(s) | Fonte | Cron |
|---|---|---|---|
| `sync_aprovai.py` | `AprovAI`, `raw_asaas_aprovai`, `raw_funil_aprovai` | Asaas (cobrancas filtradas por `is_aprovai_transaction`, porta exata da regra do app `iasmartsites/aprovai2`) + Firestore do projeto `aprovai-de156` (`analytics_funnel_events`) | Junto do `sync.py`, mesmo workflow |
| `sync_sdr_incentivador.py` | `SDR Incentivador`, `raw_hubspot_incentivador` | HubSpot, pipeline Incentivador (`default`): cards por `trabalhado_por`, quem chega em `[EV] Reuniao Agendada` ou alem e por qual caminho (automatico vs manual) | Junto do `sync.py`, mesmo workflow |

PII do Asaas (nome/e-mail/CPF do cliente) e usada SO em memoria pra classificar
teste-ou-staff; nunca vira coluna. Identificador publicado e `sync.hash_id`
(sha256[:12]), mesma convencao do resto do repo. `dealname`/`deal_id` do HubSpot
nao entram nessa regra (registro de negocio B2B, nao pessoa fisica).

Rodar local:
```
python sync_aprovai.py --dry-run
python sync_sdr_incentivador.py --dry-run
```
Secrets adicionais: `ASAAS_APROVAI_API_KEY` (local: `~/.brada-secrets/asaas-aprovai.env`),
`COMUNIDADE_SA_JSON` (local: `~/.brada-secrets/comunidade-dashboard-sa.json` - conta dashboard-leitura,
reusada pelo Firestore do AprovAI e pelo Google Analytics; a antiga SA dedicada `aprovai-sa.json` nao
e mais usada desde 11/11),
`HUBSPOT_TOKEN` (local: `~/.brada-secrets/hubspot.env`, mesmo token do `brada-hubspot-sync`).

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
