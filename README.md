# brada-plataforma-sync

Sync diario **Plataforma Brada (Firestore + Firebase Auth) -> Google Sheets** pra alimentar o dashboard Looker "Funil & Plataforma" da gerencia (Tamyris).

Padrao espelha `brada-hubspot-sync` e `brada-clickup-sync`. Documentacao completa do projeto no vault Obsidian: `01_Projetos/Dashboard_Funil_Plataforma/`.

## O que escreve (Sheet `Brada_Dashboard_Plataforma`)

| Aba | Modo | Conteudo |
|---|---|---|
| `raw_users` | overwrite | 1 linha/usuario (pseudonimizado): cadastro, role, atribuicao UTM, login (proxy via Auth), projetos |
| `raw_projects` | overwrite | 1 linha/projeto: status, expiracao CAC (vigente/expirado/sem_data), budget, ODS/UF |
| `raw_proposals` | overwrite | 1 linha/proposta: status, edital, valor aprovado (R$), datas |
| `snap_diario` | append idempotente | serie historica em formato longo (data, metrica, segmento, valor) |
| `meta_sync` | overwrite | ultima execucao, contagens, avisos de schema drift |

Sprint 2 adiciona `raw_funil_automatize` (HubSpot `trabalhado_por=Automatize`) + motor de atribuicao 3 camadas.

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
- Login = proxy via Firebase Auth `lastSignInTimestamp` ate o campo `lastLogin` existir no Firestore.
- `snap_diario` e a UNICA fonte de serie historica (Firestore so tem estado atual): rodar manual 1x/dia ate o cron subir.
