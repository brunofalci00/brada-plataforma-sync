"""
Sync Plataforma Brada (Firestore + Firebase Auth) -> Google Sheets
Alimenta o dashboard Looker "Funil & Plataforma" da Tamyris (gerencia).

Sprint 1: raw_users, raw_projects, raw_proposals, snap_diario (append
idempotente), meta_sync. Sprint 2 adiciona raw_funil_automatize (HubSpot,
trabalhado_por=Automatize) e o motor de atribuicao 3 camadas completo.

REGRAS INEGOCIAVEIS DE PII (feedback_pii_allowlist_defensiva):
- Serializacao whitelist: so as colunas dos HEADERs saem; campo novo do
  Firestore e ignorado por default.
- email, name, phone, document, uid NUNCA saem pra Sheet nem pra log
  (repo publico = logs publicos no GitHub Actions).
- Guard pre-publicacao: regex de e-mail/CPF/CNPJ em todas as celulas;
  se bater, aborta com exit 1 antes de escrever.
- Identificadores publicados sao hashes: sha256(id)[:12].

Padrao espelha brada-clickup-sync / brada-hubspot-sync. Roda via GitHub
Actions (cron diario 09:15 UTC = 06:15 BRT) ou local com --dry-run.

Doc do projeto: vault Obsidian, 01_Projetos/Dashboard_Funil_Plataforma/.
"""

import argparse
import datetime
import hashlib
import json
import os
import re
import sys
import time
from collections import Counter
from urllib.parse import urlparse
import analytics_contract as analytics

# gspread/google-auth/firestore importados lazy pra --help rodar sem deps.

# ===================================================
# CONFIG
# ===================================================

FIREBASE_PROJECT = "gen-lang-client-0225656939"
# CRITICO: database NOMEADO. O "(default)" existe e esta VAZIO
# (reference_firestore_ai_studio_database).
FIRESTORE_DATABASE = "ai-studio-93e1b1b8-c1c0-446c-87ba-d8fb8e3b0dd6"


def _load_local_env(path):
    """Carrega um .env local (utf-8-sig tolera BOM do PowerShell) sem
    sobrescrever o que ja veio do ambiente (GitHub Secrets tem precedencia)."""
    try:
        with open(path, encoding="utf-8-sig") as fh:
            for line in fh:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip())
    except FileNotFoundError:
        pass


# Em dev local, puxa SPREADSHEET_ID do ~/.brada-secrets/plataforma-sync.env.
_load_local_env(os.path.expanduser(r"~/.brada-secrets/plataforma-sync.env"))

SPREADSHEET_ID = os.environ.get("SPREADSHEET_ID", "")

# Endpoint de leads da Automatize (export-leads-automatize). Token via env
# AUTOMATIZE_ENDPOINT_TOKEN (secret no CI; ~/.brada-secrets/plataforma-sync.env local).
# Cap confirmado de 1000/resposta SEM paginacao. A deduplicacao preserva recebidos,
# mas NAO prova completude; o servidor pode ignorar a janela (revalidado 30/08).
AUTOMATIZE_ENDPOINT_URL = os.environ.get(
    "AUTOMATIZE_ENDPOINT_URL",
    "https://n8n-webhook.painel.automatizenow.io/webhook/export-leads-automatize",
)
AUTOMATIZE_ENDPOINT_TOKEN = os.environ.get("AUTOMATIZE_ENDPOINT_TOKEN", "")
LEADS_LOOKBACK_DAYS = 45  # janela do incremental ?atualizados_desde

# Service account Google Sheets (mesma do hubspot-sync / clickup-sync)
SHEETS_SA_JSON = os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON", "")
SHEETS_SA_FILE = os.environ.get(
    "GOOGLE_SERVICE_ACCOUNT_FILE", os.path.expanduser(r"~/.brada-secrets/sheets-sa.json")
)

# Service account Firebase (Firestore read + Auth list_users)
FIREBASE_SA_JSON = os.environ.get("FIREBASE_SERVICE_ACCOUNT_JSON", "")
FIREBASE_SA_FILE = os.environ.get(
    "FIREBASE_SERVICE_ACCOUNT_FILE", os.path.expanduser(r"~/.brada-secrets/firebase-sa.json")
)

# Fuso de Brasilia: toda truncagem Timestamp -> data usa BRT, senao cadastro
# de 22h vira o dia seguinte (UTC).
BRT = datetime.timezone(datetime.timedelta(hours=-3))

# Blacklist absoluta: nomes de campo que NUNCA podem virar coluna nem log.
PII_FIELDS = {"email", "name", "phone", "document", "uid", "cpf", "cnpj", "telefone"}

# Enums conhecidos (detector de schema drift: valor fora daqui vira aviso
# em meta_sync, nunca quebra o run).
KNOWN_PROJECT_STATUS = {
    "Rascunho", "Disponível", "Em Execução", "Concluído",
    # previstos em briefings do Thiago (ainda nao existem em prod 11/06):
    "Aprovado", "Em Elaboração", "Finalizado",
}
KNOWN_ROLES = {"ONG", "INVESTOR", "SUPER_ADMIN"}
KNOWN_PROPOSAL_STATUS = {"aprovado", "em análise", "em analise", "rejeitado", "rascunho"}

# ===================================================
# HEADERS (contrato das abas — espelhado em contrato/check do Sprint 2)
# ===================================================

HEADER_USERS = [
    "user_hash", "data_cadastro", "mes_cadastro", "role", "status", "tipo_pessoa",
    "is_migrado", "email_verificado",
    "utm_source", "utm_medium", "utm_campaign", "utm_content", "utm_term",
    "tem_gclid", "tem_fbclid", "landing_path", "referrer_dominio",
    "origem_camada", "origem_canal",
    "data_ultimo_login", "logou_alguma_vez", "ativo_30d",
    "tem_projeto", "n_projetos", "data_primeiro_projeto",
]

HEADER_PROJECTS = [
    "project_hash", "data_criacao", "mes_criacao", "status", "is_migrado",
    "owner_hash", "owner_role",
    "data_expiracao_cac", "expiracao_situacao",
    "ods_principal", "n_ods", "uf", "budget_valor", "budget_faixa",
    "dados_em_atualizacao",
    # No fim de proposito: fonte do Looker so ganha campo, nao quebra.
    "n_campos_faltando", "campos_faltando",
    "inicio_observacao", "primeira_publicacao_observada", "primeira_aptidao_observada",
]

HEADER_PROPOSALS = [
    "proposal_hash", "project_hash", "edital", "status", "stage",
    "valor_aprovado", "data_submissao", "mes_submissao", "data_aprovacao",
]

HEADER_SNAP = ["data_snapshot", "metrica", "segmento", "valor"]

HEADER_META = ["chave", "valor"]

# Comparativo de migracao (fonte: planilha da plataforma antiga; ver fonte_antiga.py)
HEADER_MIGRACAO = [
    "legacy_hash", "fim_execucao_antiga", "situacao_antiga", "no_baseline",
    "existe_na_nova", "status_nova", "dono_migrado", "dono_logou",
]

# Leads da Automatize (endpoint export-leads-automatize). SO o subconjunto
# nao-PII do consumidor "dashboard" + colunas de join com o cadastro.
# Contato/dossie (nome/email/telefone/projeto_*/hunter_resumo/drive_url) NAO
# entram: repo publico; esses vivem na base privada do back-office (Thiago).
HEADER_LEADS_AUTOMATIZE = [
    "lead_id", "publico", "uf", "cidade", "segmento", "lei",
    "match_score", "classificacao", "canal", "fonte_coleta", "hunter_nivel",
    "estagio", "coletado_em", "tocado_em", "respondido_em", "direcionado_em",
    "status", "motivo_perda", "backfill",
    "cadastrou_plataforma", "data_cadastro_plataforma", "tem_projeto_plataforma",
]

# ===================================================
# AUTENTICACAO
# ===================================================

def _firebase_creds_info():
    """Retorna dict da SA Firebase (CI: env JSON; local: arquivo)."""
    if FIREBASE_SA_JSON:
        # decode utf-8-sig tolera BOM (feedback_powershell_utf8_bom_bug)
        return json.loads(FIREBASE_SA_JSON.encode("utf-8").decode("utf-8-sig"))
    with open(FIREBASE_SA_FILE, encoding="utf-8-sig") as fh:
        return json.load(fh)


def init_firestore():
    from google.cloud import firestore
    from google.oauth2 import service_account

    info = _firebase_creds_info()
    creds = service_account.Credentials.from_service_account_info(info)
    return firestore.Client(
        project=FIREBASE_PROJECT, database=FIRESTORE_DATABASE, credentials=creds
    )


def init_firebase_auth():
    import firebase_admin
    from firebase_admin import credentials as fb_credentials

    info = _firebase_creds_info()
    cred = fb_credentials.Certificate(info)
    try:
        return firebase_admin.initialize_app(cred)
    except ValueError:
        return firebase_admin.get_app()


# ---------------------------------------------------------------- Sheets: retry

# O Google devolve 5xx e 429 de vez em quando e a rodada inteira morria por causa disso:
# em 60 rodadas do "Mutirao Rascunho", 6 falharam com
# `APIError: [503]: The service is currently unavailable`. O mesmo 503 derrubou o
# `Sync HubSpot -> Sheets` em 26/08. Nenhuma dessas falhas era erro nosso.
#
# 4xx de permissao NAO entra na lista, de proposito. Retentar credencial errada ou
# planilha sem acesso so atrasa em 35 segundos a descoberta de um problema que nao passa
# sozinho — e problema que nao passa sozinho tem que aparecer na primeira tentativa.
STATUS_TRANSITORIOS = frozenset({429, 500, 502, 503, 504})


def _e_transitorio(erro):
    """O erro passa sozinho se a gente esperar?

    Erro sem `.response` (TypeError, KeyError, bug nosso) responde NAO: retentar quatro
    vezes so esconderia o defeito atras de meio minuto de espera.
    """
    resposta = getattr(erro, "response", None)
    return getattr(resposta, "status_code", None) in STATUS_TRANSITORIOS


def com_retry(request, tentativas=4, espera_inicial=5, dormir=time.sleep):
    """Envolve `HTTPClient.request` retentando so o que e transitorio.

    Envolve o CLIENTE, e nao cada chamada: as chamadas ao Sheets estao espalhadas por
    sete arquivos deste repo, e proteger uma a uma e garantia de esquecer alguma.

    `dormir` e injetavel porque teste de backoff nao pode levar 35 segundos de verdade.
    """
    def _wrapper(*args, **kwargs):
        espera = espera_inicial
        for tentativa in range(1, tentativas + 1):
            try:
                return request(*args, **kwargs)
            except Exception as erro:
                if tentativa == tentativas or not _e_transitorio(erro):
                    raise
                codigo = getattr(getattr(erro, "response", None), "status_code", "?")
                print(f"  [sheets] {codigo} na tentativa {tentativa}/{tentativas}; "
                      f"aguardando {espera}s", flush=True)
                dormir(espera)
                espera *= 2
    return _wrapper


def get_sheets_client():
    import gspread
    from google.oauth2.service_account import Credentials

    scopes = [
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive",
    ]
    if SHEETS_SA_JSON:
        info = json.loads(SHEETS_SA_JSON.encode("utf-8").decode("utf-8-sig"))
        creds = Credentials.from_service_account_info(info, scopes=scopes)
    elif os.path.exists(SHEETS_SA_FILE):
        creds = Credentials.from_service_account_file(SHEETS_SA_FILE, scopes=scopes)
    else:
        raise SystemExit(
            "Credenciais Google Sheets nao encontradas "
            "(GOOGLE_SERVICE_ACCOUNT_JSON ou ~/.brada-secrets/sheets-sa.json)."
        )
    gc = gspread.authorize(creds)
    # Um ponto so: `gspread.authorize` aparece uma unica vez no repo, entao envolver o
    # `request` aqui cobre TODA chamada ao Sheets, dos sete arquivos, sem tocar em call site.
    gc.http_client.request = com_retry(gc.http_client.request)
    return gc

# ===================================================
# NORMALIZACAO
# ===================================================

def hash_id(raw_id):
    """Pseudonimo estavel: sha256 truncado. id cru NUNCA sai."""
    if not raw_id:
        return ""
    return hashlib.sha256(str(raw_id).encode("utf-8")).hexdigest()[:12]


def to_date(v):
    """Normaliza Timestamp/datetime/string ISO -> 'AAAA-MM-DD' em BRT.
    Defensivo: projects.createdAt e STRING, users.createdAt e Timestamp,
    e o Thiago pode mudar o schema sem aviso. Nao parseou -> ''. """
    if v is None or v == "":
        return ""
    # Firestore Timestamp / DatetimeWithNanoseconds / datetime
    if isinstance(v, datetime.datetime):
        if v.tzinfo is None:
            v = v.replace(tzinfo=datetime.timezone.utc)
        return v.astimezone(BRT).strftime("%Y-%m-%d")
    s = str(v).strip()
    # ISO completo (com ou sem Z/offset)
    m = re.match(r"^(\d{4})-(\d{2})-(\d{2})[T ]", s)
    if m:
        try:
            dt = datetime.datetime.fromisoformat(s.replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=datetime.timezone.utc)
            return dt.astimezone(BRT).strftime("%Y-%m-%d")
        except ValueError:
            return f"{m.group(1)}-{m.group(2)}-{m.group(3)}"
    # date-only
    if re.match(r"^\d{4}-\d{2}-\d{2}$", s):
        return s
    # epoch ms (defensivo)
    if re.match(r"^\d{12,13}$", s):
        try:
            return datetime.datetime.fromtimestamp(int(s) / 1000, BRT).strftime("%Y-%m-%d")
        except (ValueError, OSError):
            return ""
    return ""


def ms_to_date(ms):
    if not ms:
        return ""
    try:
        return datetime.datetime.fromtimestamp(int(ms) / 1000, BRT).strftime("%Y-%m-%d")
    except (ValueError, TypeError, OSError):
        return ""


def norm_utm(v):
    """trim + lower. Producao ja tem utm_campaign com espaco no fim."""
    if not v:
        return ""
    return str(v).strip().lower()


def referrer_dominio(referrer):
    """So o dominio do referrer (URL completa pode vazar query string)."""
    if not referrer:
        return ""
    try:
        return urlparse(str(referrer)).netloc or ""
    except ValueError:
        return ""


def parse_budget_brl(s):
    """'R$ 1.234.567,89' -> 1234567.89 (float). Nao parseou -> ''. """
    if s is None or s == "":
        return ""
    if isinstance(s, (int, float)):
        return round(float(s), 2)
    txt = re.sub(r"[^\d.,]", "", str(s))
    if not txt:
        return ""
    # formato BR: '.' milhar, ',' decimal
    if "," in txt:
        txt = txt.replace(".", "").replace(",", ".")
    try:
        return round(float(txt), 2)
    except ValueError:
        return ""


def budget_faixa(valor):
    if valor == "" or valor is None:
        return "sem_valor"
    v = float(valor)
    if v < 50_000:
        return "<50k"
    if v < 200_000:
        return "50-200k"
    if v < 1_000_000:
        return "200k-1M"
    return ">1M"


def origem_canal(utm_source, is_migrado):
    """Mapeia utm_source normalizado -> canal canonico do dashboard."""
    src = norm_utm(utm_source)
    if src == "automatize":
        return "automatize"
    if src == "site":
        return "site"
    if src == "leadlovers":
        return "leadlovers"
    if src == "comercial":
        return "comercial"
    if src == "instagram":
        return "instagram"
    if src in ("meta", "facebook", "fb", "meta_ads"):
        return "meta_ads"
    if src:
        return "outro"
    return "migrado" if is_migrado else "sem_atribuicao"


def sim_nao(b):
    return "sim" if b else "nao"

# ===================================================
# LEITURA FIRESTORE / AUTH
# ===================================================

def load_auth_login_index():
    """uid -> last_sign_in_ms via Firebase Auth (proxy de login ate o campo
    lastLogin existir no Firestore — briefing_thiago_lastlogin)."""
    from firebase_admin import auth as fb_auth

    init_firebase_auth()
    out = {}
    for u in fb_auth.list_users().iterate_all():
        md = u.user_metadata
        out[u.uid] = md.last_sign_in_timestamp  # ms ou None
    return out


def load_collection(db, name):
    """Lista de (doc_id, dict). Leitura unica por colecao."""
    return [(doc.id, doc.to_dict() or {}) for doc in db.collection(name).stream()]

# ===================================================
# BUILD DAS ABAS
# ===================================================

# Espelha isProjectComplete do front (brada-plataforma-v3 Projects.tsx:474-492).
# Este modulo e a FONTE UNICA da regra: sync_outreach_rascunho e as reguas de
# e-mail importam daqui. Se a lista divergir entre planilha e e-mail, a Tamyris
# ve um numero e o proponente recebe outro.
# Rotulos sao os do formulario, porque vao direto pro e-mail e pro dashboard.
PROJ_CAMPOS = [
    ("title", "Nome do projeto"),
    ("description", "Descrição"),
    ("budget", "Orçamento estimado"),
    ("startDate", "Data de início"),
    ("category", "Categoria"),
    ("targetAudience", "Público-alvo"),
    ("location", "Localização"),
    ("fundingSource", "Fonte de recurso"),
    ("cacExpirationDate", "Prazo final de captação"),
]
# Compatibilidade: consumidores antigos esperam so as chaves.
PROJ_CAMPOS_OBRIG = tuple(k for k, _ in PROJ_CAMPOS)


def campos_faltando(d):
    """Rotulos dos campos que faltam pro projeto poder ser publicado."""
    falta = [rotulo for chave, rotulo in PROJ_CAMPOS if not d.get(chave)]
    ods = d.get("ods")
    if not (ods and (len(ods) > 0 if isinstance(ods, list) else True)):
        falta.append("ODS")
    if not (d.get("diarioOficialUrl") or d.get("existingDiarioOficialUrl")):
        falta.append("Arquivo do Diário Oficial")
    alvo = d.get("targetAudience") or []
    if isinstance(alvo, list) and "Outros" in alvo and not str(d.get("targetAudienceOther") or "").strip():
        falta.append('Especificação do público-alvo "Outros"')
    return falta


def projeto_incompleto(d):
    return bool(campos_faltando(d))


def build_projects(projects_raw, users_by_id, today_str, issues):
    """raw_projects + indices auxiliares (owner -> projetos)."""
    rows = []
    by_owner = {}
    for doc_id, d in projects_raw:
        status = str(d.get("status") or "").strip()
        if status and status not in KNOWN_PROJECT_STATUS:
            issues["project_status_desconhecido"][status] += 1
        data_criacao = to_date(d.get("createdAt"))
        if d.get("createdAt") and not data_criacao:
            issues["projects_createdAt_nao_parseado"][type(d.get("createdAt")).__name__] += 1
        exp = to_date(d.get("cacExpirationDate"))
        if not exp:
            situacao = "sem_data"
        elif exp < today_str:
            situacao = "expirado"
        else:
            situacao = "vigente"
        owner_id = d.get("ownerId") or ""
        owner = users_by_id.get(owner_id, {})
        ods = d.get("ods") or []
        if not isinstance(ods, list):
            ods = [ods]
        budget = parse_budget_brl(d.get("budget"))
        # selo "dados em atualizacao" = publicado (Disponivel) mas ainda incompleto.
        # Base honesta da segmentacao (burn-down conforme o dono completa).
        faltando = campos_faltando(d)
        selo = sim_nao(status == "Disponível" and bool(faltando))
        row = [
            hash_id(doc_id),
            data_criacao,
            data_criacao[:7] if data_criacao else "",
            status,
            sim_nao(bool(d.get("legacyId"))),
            hash_id(owner_id),
            str(owner.get("role") or ""),
            exp,
            situacao,
            str(ods[0]) if ods else "",
            len(ods),
            str(d.get("location") or "").strip(),
            budget,
            budget_faixa(budget),
            selo,
            len(faltando),
            " | ".join(faltando),
        ]
        rows.append(row)
        if owner_id:
            by_owner.setdefault(owner_id, []).append(data_criacao)
    rows.sort(key=lambda r: r[1], reverse=True)
    return rows, by_owner


def build_users(users_raw, login_index, projects_by_owner, issues):
    rows = []
    now = datetime.datetime.now(BRT)
    cutoff_30d = now - datetime.timedelta(days=30)
    email_seen = Counter()
    for doc_id, d in users_raw:
        role = str(d.get("role") or "")
        if role and role not in KNOWN_ROLES:
            issues["user_role_desconhecido"][role] += 1
        # email usado SO em memoria pra contagem de dups (nunca sai)
        em = str(d.get("email") or "").strip().lower()
        if em:
            email_seen[em] += 1
        is_migrado = bool(d.get("legacyId"))
        attr = d.get("attribution") if isinstance(d.get("attribution"), dict) else {}
        utm_source = norm_utm(attr.get("utm_source"))
        data_cadastro = to_date(d.get("createdAt"))
        last_ms = login_index.get(doc_id)
        data_login = ms_to_date(last_ms)
        ativo_30d = False
        if last_ms:
            try:
                ativo_30d = datetime.datetime.fromtimestamp(int(last_ms) / 1000, BRT) >= cutoff_30d
            except (ValueError, OSError):
                ativo_30d = False
        projetos = projects_by_owner.get(doc_id, [])
        datas_proj = sorted([p for p in projetos if p])
        # Camadas de atribuicao (Sprint 1: utm/migrado/sem_atribuicao;
        # email_hubspot e janela entram no Sprint 2 com o modulo HubSpot)
        # `referrer` fica ENTRE utm e sem_atribuicao de proposito: origem
        # inferida do dominio nao pode ser lida como origem declarada. Passa a
        # aparecer quando o T13 chegar em producao (hoje o front descarta o
        # referrer de quem entra sem UTM, e por isso 33 de 41 cadastros desde
        # 01/07 nao tem sinal nenhum).
        ref_canal = norm_utm(attr.get("referrer_canal"))
        if is_migrado:
            camada = "migrado"
        elif utm_source:
            camada = "utm"
        elif ref_canal:
            camada = "referrer"
        else:
            camada = "sem_atribuicao"
        rows.append([
            hash_id(doc_id),
            data_cadastro,
            data_cadastro[:7] if data_cadastro else "",
            role,
            str(d.get("status") or ""),
            str(d.get("tipoPessoa") or ""),
            sim_nao(is_migrado),
            sim_nao(bool(d.get("emailVerified"))),
            utm_source,
            norm_utm(attr.get("utm_medium")),
            norm_utm(attr.get("utm_campaign")),
            norm_utm(attr.get("utm_content")),
            norm_utm(attr.get("utm_term")),
            sim_nao(bool(attr.get("gclid"))),
            sim_nao(bool(attr.get("fbclid"))),
            str(attr.get("landing_path") or ""),
            referrer_dominio(attr.get("referrer")),
            camada,
            # UTM declarada primeiro; canal inferido do referrer so quando nao
            # ha UTM. `origem_camada` e quem diz COMO a gente sabe, `origem_canal`
            # diz O QUE e — por isso os dois podem apontar pro mesmo canal com
            # niveis de confianca diferentes.
            origem_canal(utm_source or ref_canal, is_migrado),
            data_login,
            sim_nao(bool(last_ms)),
            sim_nao(ativo_30d),
            sim_nao(len(datas_proj) > 0 or len(projetos) > 0),
            len(projetos),
            datas_proj[0] if datas_proj else "",
        ])
    n_dup = sum(1 for c in email_seen.values() if c > 1)
    if n_dup:
        issues["emails_com_multiplos_users"]["(contagem)"] = n_dup
    rows.sort(key=lambda r: r[1], reverse=True)
    return rows


def build_proposals(proposals_raw, grants_raw, issues):
    grant_title = {gid: str(g.get("title") or "") for gid, g in grants_raw}
    rows = []
    for doc_id, d in proposals_raw:
        status = str(d.get("status") or "").strip()
        if status and status.lower() not in KNOWN_PROPOSAL_STATUS:
            issues["proposal_status_desconhecido"][status] += 1
        data_sub = to_date(d.get("submittedAt"))
        valor = parse_budget_brl(d.get("approvedValue"))
        rows.append([
            hash_id(doc_id),
            hash_id(d.get("projectId") or ""),
            grant_title.get(d.get("grantId") or "", ""),
            status,
            str(d.get("stage") or ""),
            valor,
            data_sub,
            data_sub[:7] if data_sub else "",
            to_date(d.get("approvedValueUpdatedAt")),
        ])
    rows.sort(key=lambda r: r[6], reverse=True)
    return rows


def build_migracao(antiga_rows, projects_raw, users_by_id, login_index, today_str):
    """Cruza planilha antiga x Firestore x Auth. Retorna (rows da aba, metricas).
    Joins EM MEMORIA; so legacy_hash + datas + flags saem pra Sheet."""
    import fonte_antiga

    hoje = datetime.date.fromisoformat(today_str)
    proj_by_legacy = {}
    for _, d in projects_raw:
        lid = str(d.get("legacyId") or "").strip()
        if lid:
            proj_by_legacy[lid] = d

    rows = []
    sit_counter = Counter()
    donos_ativos_logaram, donos_ativos_total = 0, 0
    for r in antiga_rows:
        sit = fonte_antiga.situacao(r["fim_execucao"], hoje)
        sit_counter[sit] += 1
        nb = fonte_antiga.no_baseline(r)
        p = proj_by_legacy.get(str(r["legacy_id"]))
        owner_id = (p or {}).get("ownerId") or ""
        owner = users_by_id.get(owner_id, {})
        dono_migrado = bool(owner.get("legacyId"))
        dono_logou = bool(login_index.get(owner_id))
        if nb and dono_migrado:
            donos_ativos_total += 1
            if dono_logou:
                donos_ativos_logaram += 1
        fim = r["fim_execucao"]
        rows.append([
            hash_id(r["legacy_id"]),
            fim.isoformat() if isinstance(fim, datetime.date) else "",
            sit,
            sim_nao(nb),
            sim_nao(p is not None),
            str((p or {}).get("status") or ""),
            sim_nao(dono_migrado),
            sim_nao(dono_logou),
        ])
    rows.sort(key=lambda x: x[1], reverse=True)

    # Migrados VISIVEIS na nova (fora de Rascunho = aparecem no matchmaking).
    # NUNCA usar projects_ativos (mistura projetos novos) como numerador.
    mig_visiveis = Counter()
    for _, d in projects_raw:
        if d.get("legacyId") and str(d.get("status") or "") not in ("", "Rascunho"):
            mig_visiveis[str(d.get("status"))] += 1
    base_total = sum(1 for u in users_by_id.values() if u.get("legacyId"))
    base_logou = sum(1 for uid, u in users_by_id.items()
                     if u.get("legacyId") and login_index.get(uid))
    baseline_n = sum(1 for r in antiga_rows if fonte_antiga.no_baseline(r))

    metrics = {
        "antiga_situacao": dict(sit_counter),
        "antiga_baseline": baseline_n,
        "mig_visiveis_por_status": dict(mig_visiveis),
        "mig_visiveis": sum(mig_visiveis.values()),
        "baseline_visiveis": sum(1 for r in rows if r[3] == "sim" and r[4] == "sim"
                                 and r[5] not in ("", "Rascunho")),
        "retencao_frac": (round(sum(1 for r in rows if r[3] == "sim" and r[4] == "sim"
                                   and r[5] not in ("", "Rascunho")) / baseline_n, 4)
                          if baseline_n else ""),
        "base_total": base_total,
        "base_logou": base_logou,
        "base_logou_frac": round(base_logou / base_total, 4) if base_total else 0,
        "donos_ativos_logaram": donos_ativos_logaram,
        "donos_ativos_total": donos_ativos_total,
    }
    return rows, metrics


def load_automatize_leads(now_brt, issues):
    """Puxa leads do endpoint da Automatize (incremental por janela; cap de
    1000/resposta sem paginacao). Retorna lista de dicts ou None. None (falha,
    sem token, formato) preserva os atributos recebidos; o join com a plataforma
    continua sendo recalculado sobre o historico, mesmo quando a fonte falha.
    NUNCA loga token / URL-com-token / body."""
    if not AUTOMATIZE_ENDPOINT_TOKEN:
        issues["endpoint_automatize"]["sem_token"] = 1
        print("  leads_automatize: sem AUTOMATIZE_ENDPOINT_TOKEN -> recebidos preservados; join recalculado")
        return None
    import requests

    since = (now_brt - datetime.timedelta(days=LEADS_LOOKBACK_DAYS)).astimezone(
        datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
    try:
        r = requests.get(
            AUTOMATIZE_ENDPOINT_URL,
            headers={"Authorization": f"Bearer {AUTOMATIZE_ENDPOINT_TOKEN}"},
            params={"atualizados_desde": since},
            timeout=90,
        )
    except Exception as e:  # noqa: BLE001 (falha de rede nao pode quebrar o sync)
        issues["endpoint_automatize"][f"conexao_{type(e).__name__}"] = 1
        print(f"  leads_automatize: erro de conexao ({type(e).__name__}) -> recebidos preservados; join recalculado")
        return None
    if r.status_code != 200:
        issues["endpoint_automatize"][f"http_{r.status_code}"] = 1
        print(f"  leads_automatize: HTTP {r.status_code} -> recebidos preservados; join recalculado (body/token nao logados)")
        return None
    try:
        data = r.json()
    except ValueError:
        issues["endpoint_automatize"]["json_invalido"] = 1
        print("  leads_automatize: resposta nao-JSON -> recebidos preservados; join recalculado")
        return None
    if isinstance(data, dict):
        for k in ("data", "leads", "results", "items"):
            if isinstance(data.get(k), list):
                data = data[k]
                break
    if not isinstance(data, list):
        issues["endpoint_automatize"]["formato_inesperado"] = 1
        print("  leads_automatize: payload nao e array -> recebidos preservados; join recalculado")
        return None
    if len(data) >= 1000:
        issues["endpoint_automatize"]["possivel_truncamento_1000"] = len(data)
    print(f"  leads_automatize: {len(data)} leads (desde {since}Z)")
    return data


def build_leads_automatize(leads_raw, users_rows, issues):
    """raw_leads_automatize: subconjunto nao-PII + join lead_id<->utm_term.
    Funil vem do campo 'estagio' (os timestamps de etapa nao sao confiaveis:
    direcionado_em/respondido_em vem null). lead_id cru (UUID, nao-PII) casa
    com users.attribution.utm_term (que passa por norm_utm -> lower)."""
    u = {h: i for i, h in enumerate(HEADER_USERS)}
    utm_idx = {}
    for r in users_rows:
        t = r[u["utm_term"]]
        if t:
            utm_idx[t] = r
    li = {h: i for i, h in enumerate(HEADER_LEADS_AUTOMATIZE)}
    rows, seen = [], set()
    for d in leads_raw:
        lid = str(d.get("lead_id") or "").strip().lower()
        if not lid or lid in seen:
            continue  # dedup dentro do proprio pull
        seen.add(lid)
        user = utm_idx.get(lid)
        score = d.get("matchScore")
        rows.append([
            lid,
            str(d.get("publico") or ""),
            str(d.get("uf") or ""),
            str(d.get("cidade") or ""),
            str(d.get("segmento") or ""),
            str(d.get("lei") or ""),
            score if isinstance(score, (int, float)) else "",
            str(d.get("classificacao") or ""),
            str(d.get("canal") or ""),
            str(d.get("fonte_coleta") or ""),
            str(d.get("hunter_nivel") or ""),
            str(d.get("estagio") or ""),
            to_date(d.get("coletado_em")),
            to_date(d.get("tocado_em")),
            to_date(d.get("respondido_em")),
            to_date(d.get("direcionado_em")),
            str(d.get("status") or ""),
            str(d.get("motivo_perda") or ""),
            sim_nao(bool(d.get("backfill"))),
            sim_nao(user is not None),
            user[u["data_cadastro"]] if user else "",
            user[u["tem_projeto"]] if user else "",
        ])
    rows.sort(key=lambda r: r[li["coletado_em"]], reverse=True)
    n_cad = sum(1 for r in rows if r[li["cadastrou_plataforma"]] == "sim")
    print(f"  leads_automatize: {len(rows)} linhas | {n_cad} cruzaram com cadastro")
    return rows


def build_snapshot(users_rows, projects_rows, proposals_rows, today_str, mig=None):
    """Formato longo: enum novo vira so um segmento novo (zero quebra de
    schema no Looker). Firestore nao tem historico — cada dia sem snapshot
    e um ponto de serie perdido."""
    u = {h: i for i, h in enumerate(HEADER_USERS)}
    p = {h: i for i, h in enumerate(HEADER_PROJECTS)}
    q = {h: i for i, h in enumerate(HEADER_PROPOSALS)}
    proposals_rows = [r for r in proposals_rows if not analytics.proposta_teste(dict(zip(HEADER_PROPOSALS, r)))]
    snap = []

    def add(metrica, segmento, valor):
        snap.append([today_str, metrica, segmento, valor])

    add("users_total", "(todos)", len(users_rows))
    for role, n in sorted(Counter(r[u["role"]] or "(vazio)" for r in users_rows).items()):
        add("users_total", role, n)
    add("users_ativos_30d", "(todos)", sum(1 for r in users_rows if r[u["ativo_30d"]] == "sim"))
    add("users_novos_nao_migrados", "(todos)",
        sum(1 for r in users_rows if r[u["is_migrado"]] == "nao"))

    for status, n in sorted(Counter(r[p["status"]] or "(vazio)" for r in projects_rows).items()):
        add("projects_por_status", status, n)
    _n_selo = sum(1 for r in projects_rows if r[p["dados_em_atualizacao"]] == "sim")
    _n_disp = Counter(r[p["status"]] for r in projects_rows).get("Disponível", 0)
    add("disponivel_por_completude", "dados_em_atualizacao", _n_selo)
    add("disponivel_por_completude", "completo", _n_disp - _n_selo)
    for sit, n in sorted(Counter(r[p["expiracao_situacao"]] for r in projects_rows).items()):
        add("projects_por_expiracao", sit, n)
    add("projects_total", "(todos)", len(projects_rows))

    for status, n in sorted(Counter(r[q["status"]] or "(vazio)" for r in proposals_rows).items()):
        add("proposals_por_status", status, n)
    add("valor_aprovado_total", "(todos)", analytics.valor_aprovado(analytics.records(HEADER_PROPOSALS, proposals_rows)))

    # Cobertura de atribuicao sobre cadastros novos (nao-migrados)
    novos = [r for r in users_rows if r[u["is_migrado"]] == "nao"]
    for camada, n in sorted(Counter(r[u["origem_camada"]] for r in novos).items()):
        add("atribuicao_cobertura", camada, n)

    # Comparativo de migracao (serie viva + baseline congelado + reativacao)
    if mig:
        for seg, n in sorted(mig["antiga_situacao"].items()):
            add("antiga_projetos_por_situacao", seg, n)
        add("antiga_ativos_baseline", "corte_2026-06-08", mig["antiga_baseline"])
        add("migracao_migrados_visiveis", "(todos)", mig["mig_visiveis"])
        for st, n in sorted(mig["mig_visiveis_por_status"].items()):
            add("migracao_migrados_visiveis", st, n)
        add("migracao_base_logou", "logaram", mig["base_logou"])
        add("migracao_base_logou", "total", mig["base_total"])
        add("migracao_donos_ativos_logaram", "logaram", mig["donos_ativos_logaram"])
        add("migracao_donos_ativos_logaram", "total", mig["donos_ativos_total"])
    return snap

def serie_semanal(snap_all, metrica, now_brt, segmento="(todos)"):
    """Ultimo valor de `metrica` em cada uma das 8 ultimas semanas (seg-dom).

    Le do historico do snap_diario porque nao da pra reconstruir retroativamente:
    `data_ultimo_login` guarda so o ultimo acesso, entao recalcular semanas
    passadas a partir dele subconta. Semana sem snapshot fica indisponível.
    """
    por_data = {}
    for r in snap_all:
        if len(r) < 4 or str(r[1]).strip() != metrica:
            continue
        if segmento and str(r[2]).strip() != segmento:
            continue
        por_data[str(r[0]).strip()] = r[3]

    monday = now_brt.date() - datetime.timedelta(days=now_brt.weekday())
    out = []
    for i in range(7, -1, -1):
        ini = monday - datetime.timedelta(weeks=i)
        fim = ini + datetime.timedelta(days=7)
        dentro = [d for d in por_data if ini.isoformat() <= d < fim.isoformat()]
        bruto = por_data[max(dentro)] if dentro else None
        try:
            valor = int(float(bruto))
        except (TypeError, ValueError):
            valor = ""
        out.append((ini.strftime("%d/%m"), valor))
    return out


def compute_dashboard_metrics(users_rows, projects_rows, proposals_rows, now_brt):
    """Agregados pra aba Dashboard (consumo humano direto). Mesma fonte de
    verdade em memoria das raw_* — impossivel divergir."""
    u = {h: i for i, h in enumerate(HEADER_USERS)}
    p = {h: i for i, h in enumerate(HEADER_PROJECTS)}
    q = {h: i for i, h in enumerate(HEADER_PROPOSALS)}
    proposals_rows = [r for r in proposals_rows if not analytics.proposta_teste(dict(zip(HEADER_PROPOSALS, r)))]
    user_records = analytics.records(HEADER_USERS, users_rows)
    project_records = analytics.records(HEADER_PROJECTS, projects_rows)
    funnel = analytics.funil_pessoas(user_records, project_records)
    auto = analytics.funil_pessoas([r for r in user_records if r.get("origem_canal") == "automatize"], project_records)

    st = Counter(r[p["status"]] for r in projects_rows)
    n_selo = sum(1 for r in projects_rows if r[p["dados_em_atualizacao"]] == "sim")
    # Regua de "ativo" VALIDADA pela Tamyris em 08/09/2026: status Disponivel ou
    # Em Execucao E prazo de captacao vigente.
    #
    # O que a validacao derrubou junto foi o card "Disponiveis" que ficava ao lado:
    # 377 contra 360, e os 17 de diferenca eram 30 vencidos a menos 13 ja em
    # execucao. Numero que nao correspondia a experiencia de ninguem — nem a do
    # incentivador (o Matchmaking esconde vencido) nem a do reporting.
    ativos = sum(1 for r in project_records if analytics.apto(r))
    # Mesmo universo de `ativos`, partido pelo prazo. Os tres cobrem Disponivel +
    # Em Execucao sem sobra nem repeticao, entao rascunho, concluido e estes tres
    # somam exatamente o estoque — e a tela pode afirmar isso.
    a_reativar = sum(1 for r in project_records if analytics.a_reativar(r))
    sem_prazo = sum(1 for r in project_records if analytics.sem_prazo(r))

    # --- Coorte de expiracao: o que vence quando (cumulativo) ---------------
    # Mesmo universo de `ativos`, pra a coorte falar dos mesmos projetos do card
    # de cima. O filtro por "vigente" e obrigatorio: sem ele, projeto sem data
    # entra como "" e a comparacao de string da falso positivo.
    hoje_iso = now_brt.date().isoformat()

    def vencem_ate(dias):
        limite = (now_brt.date() + datetime.timedelta(days=dias)).isoformat()
        return sum(1 for r in project_records
                   if analytics.apto(r)
                   and hoje_iso <= r["data_expiracao_cac"] <= limite)

    # --- Funil do proponente ------------------------------------------------
    # So ONG: investidor e admin nao fazem parte deste funil.
    # "Publicou" = status != Rascunho, a mesma regua de mig_visiveis. Nao criar
    # uma terceira definicao de publicado.
    ongs = [r for r in users_rows if r[u["role"]] == "ONG"]
    donos_publicados = {r[p["owner_hash"]] for r in projects_rows
                        if r[p["status"]] != "Rascunho" and r[p["owner_hash"]]}

    # --- Rascunhos: o que trava --------------------------------------------
    rascunhos = [r for r in projects_rows if r[p["status"]] == "Rascunho"]

    def rasc_sem(rotulo):
        return sum(1 for r in rascunhos if rotulo in r[p["campos_faltando"]])

    novos = [r for r in users_rows if r[u["is_migrado"]] == "nao"]
    mes = now_brt.strftime("%Y-%m")
    mes_ant = (now_brt.replace(day=1) - datetime.timedelta(days=1)).strftime("%Y-%m")

    canais = {"sem_atribuicao": 0, "leadlovers": 0, "automatize": 0, "meta_ads": 0,
              "instagram": 0, "comercial": 0, "site": 0, "outro": 0}
    for r in novos:
        c = r[u["origem_canal"]]
        if c in canais:
            canais[c] += 1

    # Serie semanal (ultimas 8 semanas, segunda a domingo)
    monday = now_brt.date() - datetime.timedelta(days=now_brt.weekday())
    semanas = []
    for i in range(7, -1, -1):
        ini = monday - datetime.timedelta(weeks=i)
        fim = ini + datetime.timedelta(days=7)
        n = sum(1 for r in novos
                if ini.isoformat() <= r[u["data_cadastro"]] < fim.isoformat())
        semanas.append((ini.strftime("%d/%m"), n))

    return {
        "funil": funnel,
        "automatize": auto,
        "mes_atual_rotulo": now_brt.strftime("01/%m a %d/%m (parcial)"),
        "mes_anterior_rotulo": (now_brt.replace(day=1) - datetime.timedelta(days=1)).strftime("%m/%Y completo"),
        "proj_total": len(projects_rows),
        "proj_ativos": ativos,
        "st_disponivel": st.get("Disponível", 0),
        "st_disponivel_selo": n_selo,
        "st_disponivel_completo": st.get("Disponível", 0) - n_selo,
        "st_em_execucao": st.get("Em Execução", 0),
        "st_rascunho": st.get("Rascunho", 0),
        "st_concluido": st.get("Concluído", 0),
        "proj_a_reativar": a_reativar,
        "proj_sem_prazo": sem_prazo,
        "vence_30d": vencem_ate(30),
        "vence_60d": vencem_ate(60),
        "vence_90d": vencem_ate(90),
        "vence_180d": vencem_ate(180),
        "funil_cadastraram": funnel["cadastros"],
        "funil_acessaram": sum(1 for r in ongs if r[u["logou_alguma_vez"]] == "sim"),
        "funil_com_projeto": funnel["criaram"],
        "funil_publicaram": funnel["publicaram"],
        "funil_aptos": funnel["aptos"],
        "rasc_sem_diario": rasc_sem("Arquivo do Diário Oficial"),
        "rasc_sem_descricao": rasc_sem("Descrição"),
        "rasc_sem_orcamento": rasc_sem("Orçamento estimado"),
        "rasc_perto": sum(1 for r in rascunhos if 0 < int(r[p["n_campos_faltando"]] or 0) <= 3),
        "prop_aprovadas": sum(1 for r in proposals_rows if r[q["status"]].lower() == "aprovado"),
        "prop_valor": analytics.valor_aprovado(analytics.records(HEADER_PROPOSALS, proposals_rows)),
        "n_migrados": len(users_rows) - len(novos),
        "novos_total": len(novos),
        "novos_mes": sum(1 for r in novos if r[u["data_cadastro"]][:7] == mes),
        "novos_mes_ant": sum(1 for r in novos if r[u["data_cadastro"]][:7] == mes_ant),
        "ativacao_frac": (round(sum(1 for r in novos if r[u["logou_alguma_vez"]] == "sim")
                                / len(novos), 4) if novos else 0),
        "ativos_30d": sum(1 for r in users_rows if r[u["ativo_30d"]] == "sim"),
        "canais": canais,
        "semanas": semanas,
        "utm_automatize": sum(1 for r in novos if r[u["origem_canal"]] == "automatize"),
    }


# ===================================================
# SENTINELA DAS FUNCTIONS EM PRODUCAO
# ===================================================

# Em 11/08 uma exportacao do AI Studio sobrescreveu a `main` da plataforma e
# levou junto 6 correcoes, entre elas a onProposalCreated e o desligamento da
# sendFundingReminder. Ninguem percebeu ate alguem ir olhar na mao: nao havia
# alarme nenhum.
#
# Isto confere o que esta VIVO em producao, nao o que esta no repositorio —
# porque e o deploy que muda o comportamento do usuario, e o repo pode ficar
# errado por dias sem consequencia. Roda junto do sync diario e derruba o
# workflow se o estado divergir, o que dispara o e-mail de falha do GitHub.
FUNCTIONS_ESPERADAS = {
    # nome: precisa estar no ar?
    "onProposalCreated": True,    # T6: avisa o incentivador de proposta nova
    "onProposalUpdated": True,    # T9 mora dentro dela (aviso de aprovacao)
    "sendFundingReminder": False,  # T4: duplicava a regua, ja atingiu 2 pessoas
}


def conferir_functions():
    """Devolve lista de divergencias entre producao e o esperado. Vazia = ok."""
    import requests
    from google.oauth2 import service_account
    from google.auth.transport.requests import Request

    try:
        creds = service_account.Credentials.from_service_account_info(
            _firebase_creds_info(),
            scopes=["https://www.googleapis.com/auth/cloud-platform"])
        creds.refresh(Request())
        r = requests.get(
            f"https://cloudfunctions.googleapis.com/v2/projects/{FIREBASE_PROJECT}"
            f"/locations/-/functions",
            headers={"Authorization": f"Bearer {creds.token}"}, timeout=30)
        if r.status_code != 200:
            return [f"nao deu pra listar as functions (HTTP {r.status_code})"]
        no_ar = {f["name"].split("/")[-1] for f in r.json().get("functions", [])}
    except Exception as e:
        # Checagem externa nunca derruba o sync por conta propria.
        return [f"nao deu pra listar as functions ({type(e).__name__})"]

    fora = []
    for nome, esperado in FUNCTIONS_ESPERADAS.items():
        if esperado and nome not in no_ar:
            fora.append(f"{nome} SUMIU de producao (deploy a partir de um repo desatualizado?)")
        elif not esperado and nome in no_ar:
            fora.append(f"{nome} VOLTOU para producao (duplica a regua de expiracao)")
    return fora


# ===================================================
# COMUNICACAO COM O USUARIO (reguas de e-mail)
# ===================================================

# Controle por PESSOA. A regua de expiracao fica de fora porque a chave dela e
# por projeto ({projectId}__{toque}) e precisa do mapa projeto -> dono.
CONTROLES_POR_PESSOA = ("regua_rascunho_envios", "regua_vitrine_envios",
                        "regua_sem_projeto_envios")
CONTROLE_POR_PROJETO = "regua_expiracao_envios"


def _ts_ms(v):
    """Timestamp do Firestore, datetime ou ISO -> milissegundos. None se nao der."""
    if v is None:
        return None
    try:
        if hasattr(v, "timestamp"):
            return int(v.timestamp() * 1000)
        return int(datetime.datetime.fromisoformat(str(v)[:19]).timestamp() * 1000)
    except Exception:
        return None


def mapa_primeiro_envio(db, dono_de_projeto):
    """
    uid -> timestamp (ms) do PRIMEIRO e-mail que a gente mandou pra pessoa.

    Documento sem `enviadoEm` e registro de descadastro
    (`filtros.registrar_supressao`), nao envio: nao entra.

    Fica fora das funcoes de metrica porque as duas precisam dele — comunicacao
    e origem do crescimento. Lido uma vez no `main` e passado adiante.
    """
    primeiro = {}

    def marcar(uid, ms):
        if uid and ms is not None:
            anterior = primeiro.get(uid)
            primeiro[uid] = ms if anterior is None else min(anterior, ms)

    for col in CONTROLES_POR_PESSOA:
        for d in db.collection(col).stream():
            marcar(d.id, _ts_ms((d.to_dict() or {}).get("enviadoEm")))
    for d in db.collection(CONTROLE_POR_PROJETO).stream():
        x = d.to_dict() or {}
        pid = x.get("projectId") or d.id.split("__")[0]
        marcar(dono_de_projeto.get(pid), _ts_ms(x.get("enviadoEm")))
    return primeiro


def metricas_email(db, login_index, primeiro_envio, now_brt):
    """
    Indicadores da comunicacao com o usuario.

    Existe porque as reguas mandaram 265 e-mails em dois dias e a planilha nao
    mostrava nenhum: quem abria a aba nao via que a plataforma passou a falar
    com o usuario, nem se estava funcionando.

    PII: so contagens saem daqui. O campo `to` da colecao `mail` nao entra em
    nenhuma variavel que chegue a planilha.
    """
    corte_7d = int((now_brt - datetime.timedelta(days=7)).timestamp() * 1000)

    total = recentes = ok = erro = 0
    for d in db.collection("mail").stream():
        m = d.to_dict() or {}
        total += 1
        ms = _ts_ms(m.get("createdAt"))
        if ms is not None and ms >= corte_7d:
            recentes += 1
        estado = str((m.get("delivery") or {}).get("state") or "")
        if estado == "SUCCESS":
            ok += 1
        elif estado == "ERROR" and not m.get("retentadoEm"):
            # Documento com `retentadoEm` ja foi reenviado e o reenvio virou
            # outro documento, que conta no SUCCESS. Contar os dois seria
            # penalizar a taxa por uma falha que a retentativa ja resolveu:
            # hoje sao 49 erros, TODOS reenviados, e a taxa real e 100%.
            erro += 1

    # Limite inferior OBSERVADO, não histórico completo de acessos nem causalidade.
    # Só coortes com sete dias completos de acompanhamento.
    janela = 7 * 86400 * 1000
    maduras = {uid: ms for uid, ms in primeiro_envio.items()
               if ms + janela <= int(now_brt.timestamp() * 1000)}
    voltaram = sum(1 for uid, ms in maduras.items()
                   if ms < (login_index.get(uid) or 0) <= ms + janela)

    return {
        "mail_total": total,
        "mail_7d": recentes,
        "mail_entrega_frac": round(ok / (ok + erro), 4) if (ok + erro) else "",
        "mail_voltaram_frac": (round(voltaram / len(maduras), 4) if maduras else ""),
        "mail_janela_n": len(maduras),
    }


# ===================================================
# DE ONDE VEM O CRESCIMENTO
# ===================================================

# Janela movel. A aba e lida continuamente, entao "ultimos 30 dias" responde
# melhor que "desde tal data". Em 12/08 a janela curta escondia o buraco de
# atribuicao: 8 sem origem em 8 dias contra 25 em 30 dias.
JANELA_ORIGEM_DIAS = 30


def metricas_origem(projects_raw, users_raw, primeiro_envio, now_brt):
    """
    Responde "de onde vem o crescimento" e "esse crescimento e concentrado?".

    Nasce da pergunta da Tamyris em 12/08 ("os projetos estao evoluindo muito,
    e algo da Automatize?"). A resposta levou um dia de apuracao pela segunda
    vez, entao agora fica na aba.

    O card de concentracao e o que impede a leitura errada: em 12/08 eram 102
    projetos novos, mas 40 de um unico dono. "+102 projetos" e "+102 projetos
    dos quais 39% de uma pessoa" levam a decisoes opostas.

    NAO abre stream proprio: recebe `projects_raw` e `users_raw` que o main ja
    carregou. Streamar aqui custaria ~1.800 leituras de documento por execucao,
    todo dia, sem ganho nenhum.

    PII: so contagens. Nenhum e-mail, nome ou id de dono sai daqui.
    """
    corte = (now_brt - datetime.timedelta(days=JANELA_ORIGEM_DIAS)).date()

    # uid -> origem da AQUISICAO. Distinta da ativacao: alguem pode ter chegado
    # pelo LeadLovers em julho e so criado projeto em agosto depois da regua.
    origem_do_dono = {}
    for doc_id, u in users_raw:
        attr = u.get("attribution") if isinstance(u.get("attribution"), dict) else {}
        # norm_utm e obrigatorio: producao ja teve UTM com espaco no fim, e sem
        # normalizar "Automatize " viraria um bucket separado.
        src = norm_utm(attr.get("utm_source"))
        origem_do_dono[doc_id] = src or ("migrado" if u.get("legacyId") else "sem_atribuicao")

    por_origem = Counter()
    por_dono = Counter()
    for doc_id, p in projects_raw:
        criado = to_date(p.get("createdAt"))  # string ISO, nao Timestamp
        if not criado or criado < str(corte):
            continue
        dono = str(p.get("ownerId") or "")
        por_dono[dono] += 1
        por_origem[origem_do_dono.get(dono, "sem_atribuicao")] += 1

    total = sum(por_origem.values())
    maior = por_dono.most_common(1)[0][1] if por_dono else 0
    de_tocados = sum(n for uid, n in por_dono.items() if uid in primeiro_envio)

    return {
        "orig_automatize": por_origem.get("automatize", 0),
        "orig_leadlovers": por_origem.get("leadlovers", 0),
        "orig_migrado": por_origem.get("migrado", 0),
        "orig_sem_atribuicao": por_origem.get("sem_atribuicao", 0),
        "orig_projetos_novos": total,
        "orig_donos": len(por_dono),
        "orig_concentracao_frac": round(maior / total, 4) if total else 0,
        "orig_de_tocados": de_tocados,
    }


# ===================================================
# GUARD ANTI-PII (pre-publicacao)
# ===================================================

# Padroes que valem em TODA celula
PII_PATTERNS_GLOBAIS = [
    ("email", re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")),
    ("cpf_mascarado", re.compile(r"\d{3}\.\d{3}\.\d{3}-\d{2}")),
    ("cnpj_mascarado", re.compile(r"\d{2}\.\d{3}\.\d{3}/\d{4}-\d{2}")),
]
# 11/14 digitos corridos: nao publicamos telefone/CPF/CNPJ cru, entao hit e
# vazamento — EXCETO em colunas *_hash (sha256 hex pode conter 11+ digitos
# seguidos por construcao; falso positivo confirmado no 1o dry-run).
# ATENCAO Sprint 2: deal_id do HubSpot tem 11 digitos -> incluir na isencao.
PII_PATTERNS_DIGITOS = [
    ("cpf_ou_telefone_cru", re.compile(r"(?<!\d)\d{11}(?!\d)")),
    ("cnpj_cru", re.compile(r"(?<!\d)\d{14}(?!\d)")),
]
COLUNAS_ISENTAS_DIGITOS = {"user_hash", "project_hash", "owner_hash",
                           "proposal_hash", "user_hash_match", "legacy_hash",
                           "utm_term", "lead_id"}  # utm_term/lead_id: id controlado, nao-PII


def pii_scan(header, rows):
    """Hits 'coluna linha N [label]' de uma aba (sem valores). NAO aborta —
    usado tanto pelo guard interno (abort) quanto pelo soft-guard da aba de
    leads externos (pular a aba sem derrubar o sync)."""
    hits = []
    for i, row in enumerate(rows):
        for j, cell in enumerate(row):
            txt = str(cell)
            patterns = list(PII_PATTERNS_GLOBAIS)
            if header[j] not in COLUNAS_ISENTAS_DIGITOS:
                patterns += PII_PATTERNS_DIGITOS
            for label, pat in patterns:
                if pat.search(txt):
                    hits.append(f"{header[j]} linha {i + 2} [{label}]")
    return hits


def pii_guard(tabs):
    """tabs = {nome_aba: (header, rows)}. Aborta no primeiro hit (dados INTERNOS).
    NUNCA imprime o valor que bateu (log publico) — so aba/linha/coluna."""
    hits = []
    for tab, (header, rows) in tabs.items():
        hits += [f"{tab}!{h}" for h in pii_scan(header, rows)]
    if hits:
        print("PII GUARD FALHOU — publicacao ABORTADA. Posicoes (sem valores):")
        for h in hits[:20]:
            print("  -", h)
        sys.exit(1)

# ===================================================
# ESCRITA SHEETS
# ===================================================

def write_overwrite(sh, name, header, rows):
    import gspread

    try:
        ws = sh.worksheet(name)
    except gspread.exceptions.WorksheetNotFound:
        ws = sh.add_worksheet(title=name, rows=max(1000, len(rows) + 100),
                              cols=max(len(header), 4))
    if ws.row_count < len(rows) + 1 or ws.col_count < len(header):
        ws.resize(rows=max(ws.row_count, len(rows) + 100), cols=max(ws.col_count, len(header)))
    sh.batch_update({"requests": [analytics.atomic_replace_request(
        ws.id, ws.row_count, ws.col_count, header, rows)]})
    print(f"  {name}: {len(rows)} linhas (overwrite)")


def write_snapshot_idempotente(sh, snap_rows, today_str):
    """Append idempotente: preserva o historico de outras datas, substitui
    as linhas de HOJE (re-run no mesmo dia nao duplica)."""
    import gspread

    name = "snap_diario"
    try:
        ws = sh.worksheet(name)
        existing = ws.get_all_values()
    except gspread.exceptions.WorksheetNotFound:
        ws = sh.add_worksheet(title=name, rows=5000, cols=4)
        existing = []
    kept = [r for r in existing[1:] if r and r[0] != today_str] if existing else []
    all_rows = kept + snap_rows
    all_rows.sort(key=lambda r: (str(r[0]), str(r[1]), str(r[2])))
    write_overwrite(sh, name, HEADER_SNAP, all_rows)
    print(f"  snap_diario: {len(snap_rows)} linhas de {today_str} "
          f"(+{len(kept)} historicas preservadas)")
    # Devolve o historico mesclado: e a unica fonte da serie semanal do dashboard,
    # e esta e a unica leitura do snap_diario no sync (evita uma segunda ida ao Sheets).
    return all_rows


def write_leads_dedup(sh, header, new_rows):
    """Append idempotente por lead_id (coluna 0): preserva leads ja vistos e
    substitui os que voltaram no pull (dado mais fresco vence). Acumula o
    historico observado; o cap impede garantir completude. Ordena por coletado_em desc."""
    import gspread

    name = "raw_leads_automatize"
    try:
        ws = sh.worksheet(name)
        existing = ws.get_all_values()
    except gspread.exceptions.WorksheetNotFound:
        ws = sh.add_worksheet(title=name, rows=max(2000, len(new_rows) + 200),
                              cols=max(len(header), 4))
        existing = []
    by_id = {}
    for r in (existing[1:] if existing else []):
        if r and r[0]:
            by_id[r[0]] = r
    n_hist_antes = len(by_id)
    for r in new_rows:
        by_id[r[0]] = r
    col_col = header.index("coletado_em")
    all_rows = sorted(by_id.values(), key=lambda r: str(r[col_col]) if len(r) > col_col else "",
                      reverse=True)
    write_overwrite(sh, name, header, all_rows)
    print(f"  raw_leads_automatize: {len(new_rows)} do pull | {len(all_rows)} no total "
          f"(historico anterior: {n_hist_antes})")

# ===================================================
# MAIN
# ===================================================

def read_existing(sh, name, required=()):
    import gspread
    try:
        values = sh.worksheet(name).get_all_values()
    except gspread.exceptions.WorksheetNotFound:
        return [], []
    if not values:
        return [], []
    if any(column not in values[0] for column in required):
        raise ValueError(f"Contrato de cabeçalho alterado: {name}; nada escrito")
    return values[0], values[1:]

def main():
    ap = argparse.ArgumentParser(description="Sync Plataforma Brada -> Sheets")
    ap.add_argument("--dry-run", action="store_true",
                    help="Le Firestore/Auth e imprime distribuicoes, sem escrever no Sheets")
    ap.add_argument("--output-dir", help="Com --dry-run: salva candidato pseudonimizado para integração local, fora do repositório")
    args = ap.parse_args()
    if args.output_dir and not args.dry_run:
        ap.error('--output-dir exige --dry-run')

    t0 = time.time()
    today_str = datetime.datetime.now(BRT).strftime("%Y-%m-%d")
    issues = {
        "project_status_desconhecido": Counter(),
        "projects_createdAt_nao_parseado": Counter(),
        "user_role_desconhecido": Counter(),
        "proposal_status_desconhecido": Counter(),
        "emails_com_multiplos_users": Counter(),
        "antiga_data_invalida": Counter(),
        "endpoint_automatize": Counter(),
    }

    print("=== Sync Plataforma Brada (Firestore + Auth) -> Sheets ===")
    db = init_firestore()
    users_raw = load_collection(db, "users")
    print(f"firestore: users={len(users_raw)}")
    projects_raw = load_collection(db, "projects")
    print(f"firestore: projects={len(projects_raw)}")
    proposals_raw = load_collection(db, "proposals")
    print(f"firestore: proposals={len(proposals_raw)}")
    grants_raw = load_collection(db, "grants")
    print(f"firestore: grants={len(grants_raw)}")
    login_index = load_auth_login_index()
    print(f"auth: users={len(login_index)}")

    # Planilha da plataforma ANTIGA (KPI comparativo de migracao)
    import fonte_antiga
    gc = get_sheets_client()
    if not SPREADSHEET_ID:
        raise SystemExit("SPREADSHEET_ID ausente")
    sh = gc.open_by_key(SPREADSHEET_ID)
    old_meta_header, old_meta_rows = read_existing(sh, "meta_sync", HEADER_META)
    old_meta = {r[0]: r[1] for r in old_meta_rows if len(r) > 1}
    old_project_header, old_project_rows = read_existing(sh, "raw_projects", ["project_hash", "status"])
    old_lead_header, old_lead_rows = read_existing(sh, "raw_leads_automatize", HEADER_LEADS_AUTOMATIZE)
    _, old_snap_rows = read_existing(sh, "snap_diario", HEADER_SNAP)
    antiga_rows = fonte_antiga.carregar_planilha(gc)
    print(f"planilha antiga: {len(antiga_rows)} projetos")

    users_by_id = {doc_id: d for doc_id, d in users_raw}
    projects_rows, projects_by_owner = build_projects(projects_raw, users_by_id, today_str, issues)
    users_rows = build_users(users_raw, login_index, projects_by_owner, issues)
    observed_at = datetime.datetime.now(BRT).isoformat(timespec="seconds")
    observed = analytics.observe_projects(
        analytics.records(HEADER_PROJECTS, projects_rows),
        analytics.records(old_project_header, old_project_rows), observed_at)
    projects_rows = [[p.get(h, "") for h in HEADER_PROJECTS] for p in observed]
    analytics.validate_source_counts({"n_users": len(users_rows), "n_projects": len(projects_rows)}, old_meta)
    proposals_rows = build_proposals(proposals_raw, grants_raw, issues)
    mig_rows, mig_metrics = build_migracao(antiga_rows, projects_raw, users_by_id,
                                           login_index, today_str)
    n_inv = mig_metrics["antiga_situacao"].get("data_invalida", 0)
    if n_inv:
        issues["antiga_data_invalida"]["(contagem)"] = n_inv

    # Leads da Automatize (endpoint). NAO pode quebrar o sync: None -> pula a aba.
    # Soft-guard de PII: se a aba externa tiver hit, PULA so ela (sem abortar).
    leads_raw = load_automatize_leads(datetime.datetime.now(BRT), issues)
    leads_rows = (build_leads_automatize(leads_raw, users_rows, issues)
                  if leads_raw is not None else None)
    if leads_rows is not None:
        _leads_pii = pii_scan(HEADER_LEADS_AUTOMATIZE, leads_rows)
        if _leads_pii:
            issues["endpoint_automatize"]["pii_hit_aba_pulada"] = len(_leads_pii)
            print(f"  raw_leads_automatize: {len(_leads_pii)} hit(s) de PII -> aba PULADA (sem abortar)")
            leads_rows = None

    leads_ok = leads_rows is not None
    merged_leads = analytics.merge_leads(
        analytics.records(old_lead_header, old_lead_rows),
        analytics.records(HEADER_LEADS_AUTOMATIZE, leads_rows or []),
        analytics.records(HEADER_USERS, users_rows))
    leads_rows = [[row.get(h, "") for h in HEADER_LEADS_AUTOMATIZE] for row in merged_leads]
    health = analytics.source_health(old_meta, leads_ok, observed_at, issues["endpoint_automatize"])

    snap_rows = build_snapshot(users_rows, projects_rows, proposals_rows, today_str,
                               mig=mig_metrics)

    meta_rows = [
        ["publicacao_estado", "concluida"],
        ["publicacao_id", observed_at],
        ["ultima_execucao_brt", datetime.datetime.now(BRT).strftime("%d/%m/%Y %H:%M")],
        ["duracao_s", round(time.time() - t0, 1)],
        ["n_users", len(users_rows)],
        ["n_projects", len(projects_rows)],
        ["n_proposals", len(proposals_rows)],
        ["snapshot_data", today_str],
    ]
    meta_rows.extend([[key, value] for key, value in health.items()])
    if leads_rows is not None:
        meta_rows.append(["n_leads_automatize", len(leads_rows)])
    for k, counter in issues.items():
        if counter:
            meta_rows.append([f"aviso_{k}", json.dumps(dict(counter), ensure_ascii=False)])

    tabs = {
        "raw_users": (HEADER_USERS, users_rows),
        "raw_projects": (HEADER_PROJECTS, projects_rows),
        "raw_proposals": (HEADER_PROPOSALS, proposals_rows),
        "raw_migracao_projetos": (HEADER_MIGRACAO, mig_rows),
        "snap_diario": (HEADER_SNAP, snap_rows),
        "meta_sync": (HEADER_META, meta_rows),
        "raw_leads_automatize": (HEADER_LEADS_AUTOMATIZE, leads_rows),
    }
    pii_guard(tabs)
    print("pii_guard: OK (zero hits)")

    now_brt = datetime.datetime.now(BRT)
    metrics = compute_dashboard_metrics(users_rows, projects_rows, proposals_rows, now_brt)
    # Comunicacao e origem do crescimento. Ficam fora de
    # compute_dashboard_metrics porque aquela funcao trabalha em cima das linhas
    # ja montadas e nao tem acesso ao Firestore.
    #
    # As colecoes de controle das reguas sao lidas UMA vez aqui: as duas
    # metricas precisam do mesmo mapa, uma pra saber quem voltou depois do
    # e-mail e outra pra separar ativacao de aquisicao.
    dono_de_projeto = {doc_id: str(d.get("ownerId") or "") for doc_id, d in projects_raw}
    primeiro_envio = mapa_primeiro_envio(db, dono_de_projeto)
    metrics.update(metricas_email(db, login_index, primeiro_envio, now_brt))
    metrics.update(metricas_origem(projects_raw, users_raw, primeiro_envio, now_brt))
    metrics.update(mig_metrics)  # bloco MIGRACAO da aba Dashboard
    metrics["fontes"] = health
    metrics["leads_total"] = len(leads_rows)
    metrics["leads_cadastrados"] = sum(r.get("cadastrou_plataforma") == "sim" for r in merged_leads)
    metrics["leads_com_projeto"] = sum(r.get("tem_projeto_plataforma") == "sim" for r in merged_leads)
    metrics["automatize_sem_lead"] = sum(
        r.get("origem_canal") == "automatize" and r.get("utm_term") not in {l.get("lead_id") for l in merged_leads}
        for r in analytics.records(HEADER_USERS, users_rows))
    # Serie semanal: fallback so com o snapshot de hoje (dry-run e seguranca contra
    # KeyError em value_data). E sobrescrita logo apos a escrita do snap_diario,
    # que devolve o historico completo.
    snap_historico = [r for r in old_snap_rows if r and r[0] != today_str] + snap_rows
    metrics["semanas_ativos"] = serie_semanal(snap_historico, "users_ativos_30d", now_brt)

    if args.dry_run:
        if args.output_dir:
            from pathlib import Path
            target = Path(args.output_dir).resolve()
            if target == Path(__file__).parent.resolve() or Path(__file__).parent.resolve() in target.parents:
                raise ValueError('Candidato deve ficar fora do repositório público')
            target.mkdir(parents=True, exist_ok=True)
            candidate = {'spreadsheet_id': SPREADSHEET_ID, 'observed_at': observed_at,
                         'metrics': metrics, 'tabs': {name: {'header': h, 'rows': r} for name, (h, r) in tabs.items()}}
            candidate['tabs']['snap_diario']['rows'] = snap_historico
            (target / 'candidate.json').write_text(json.dumps(candidate, ensure_ascii=False), encoding='utf-8')
        u = {h: i for i, h in enumerate(HEADER_USERS)}
        p = {h: i for i, h in enumerate(HEADER_PROJECTS)}
        print(f"[dry-run] users={len(users_rows)} projects={len(projects_rows)} "
              f"proposals={len(proposals_rows)} migracao={len(mig_rows)} snap={len(snap_rows)}")
        print("[dry-run] dashboard metrics:", json.dumps(metrics, ensure_ascii=False))
        print("users por origem_camada:",
              dict(Counter(r[u["origem_camada"]] for r in users_rows)))
        print("users por origem_canal:",
              dict(Counter(r[u["origem_canal"]] for r in users_rows)))
        print("projects por status:",
              dict(Counter(r[p["status"]] for r in projects_rows)))
        print("projects por expiracao:",
              dict(Counter(r[p["expiracao_situacao"]] for r in projects_rows)))
        if leads_rows is not None:
            li = {h: i for i, h in enumerate(HEADER_LEADS_AUTOMATIZE)}
            print("leads_automatize por estagio:",
                  dict(Counter(r[li["estagio"]] for r in leads_rows)))
            print("leads_automatize cadastrou:",
                  dict(Counter(r[li["cadastrou_plataforma"]] for r in leads_rows)))
            print("leads_automatize backfill:",
                  dict(Counter(r[li["backfill"]] for r in leads_rows)))
        else:
            print("leads_automatize: None (sem token/erro/PII) -> aba nao seria tocada")
        for r in meta_rows:
            print("meta:", r[0], "=", r[1])
        print("plataforma_sync: DRY-RUN (nada escrito no Sheets)")
        return

    if not SPREADSHEET_ID:
        raise SystemExit("SPREADSHEET_ID ausente (env ou ~/.brada-secrets/plataforma-sync.env).")
    sh = gc.open_by_key(SPREADSHEET_ID)
    # Leitor do BI recusa gerações em construção. Só o último passo libera a nova foto.
    in_progress = {**old_meta, "publicacao_estado": "em_andamento", "publicacao_id": observed_at}
    write_overwrite(sh, "meta_sync", HEADER_META, [[key, value] for key, value in in_progress.items()])
    write_overwrite(sh, "raw_users", HEADER_USERS, users_rows)
    write_overwrite(sh, "raw_projects", HEADER_PROJECTS, projects_rows)
    write_overwrite(sh, "raw_proposals", HEADER_PROPOSALS, proposals_rows)
    write_overwrite(sh, "raw_migracao_projetos", HEADER_MIGRACAO, mig_rows)
    write_overwrite(sh, "snap_diario", HEADER_SNAP, snap_historico)
    metrics["semanas_ativos"] = serie_semanal(snap_historico, "users_ativos_30d", now_brt)
    # Histórico acumulado, com join recalculado contra a foto atual. Falha externa
    # preserva atributos recebidos e fica explícita no manifesto de cobertura.
    if leads_rows is not None:
        write_overwrite(sh, "raw_leads_automatize", HEADER_LEADS_AUTOMATIZE, leads_rows)
    # Dashboard POR ULTIMO: o carimbo de atualizacao so avanca se tudo acima passou
    import dashboard_layout
    status_layout = dashboard_layout.ensure_dashboard(sh, metrics, now_brt.replace(tzinfo=None))
    write_overwrite(sh, "meta_sync", HEADER_META, meta_rows)
    print(f"  Dashboard: valores atualizados | {status_layout}")
    print(f"plataforma_sync: OK | users={len(users_rows)} projects={len(projects_rows)} "
          f"proposals={len(proposals_rows)} | {round(time.time() - t0, 1)}s")

    # POR ULTIMO, depois de tudo escrito: a planilha atualiza normalmente e o
    # workflow fica vermelho, que e o que faz o alarme chegar em alguem.
    divergencias = conferir_functions()
    if divergencias:
        print("\n" + "=" * 62)
        print("ALERTA: as functions em producao divergem do esperado")
        for d in divergencias:
            print(f"  - {d}")
        print("  Ver o backlog tecnico em 00_Plano_Retomada_Index (T4, T6, T9).")
        print("=" * 62)
        raise SystemExit(1)
    print("  functions em producao: conferidas")


if __name__ == "__main__":
    main()
