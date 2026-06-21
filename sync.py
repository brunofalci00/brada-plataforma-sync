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
    return gspread.authorize(creds)

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
    return "migrado" if is_migrado else "organico"


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

# Espelha isProjectComplete do front (brada-plataforma-v3 Projects.tsx:372) e a
# porta de sync_outreach_rascunho. Inline aqui pra evitar import circular
# (sync_outreach_rascunho importa este modulo).
PROJ_CAMPOS_OBRIG = ("title", "budget", "startDate", "description", "targetAudience",
                     "location", "category", "fundingSource", "cacExpirationDate")


def projeto_incompleto(d):
    if not all(d.get(k) for k in PROJ_CAMPOS_OBRIG):
        return True
    ods = d.get("ods")
    if not (ods and (len(ods) > 0 if isinstance(ods, list) else True)):
        return True
    if not (d.get("diarioOficialUrl") or d.get("existingDiarioOficialUrl")):
        return True
    return False


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
        selo = sim_nao(status == "Disponível" and projeto_incompleto(d))
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
        if is_migrado:
            camada = "migrado"
        elif utm_source:
            camada = "utm"
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
            origem_canal(utm_source, is_migrado),
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
        "retencao_frac": round(sum(mig_visiveis.values()) / baseline_n, 4) if baseline_n else 0,
        "base_total": base_total,
        "base_logou": base_logou,
        "base_logou_frac": round(base_logou / base_total, 4) if base_total else 0,
        "donos_ativos_logaram": donos_ativos_logaram,
        "donos_ativos_total": donos_ativos_total,
    }
    return rows, metrics


def build_snapshot(users_rows, projects_rows, proposals_rows, today_str, mig=None):
    """Formato longo: enum novo vira so um segmento novo (zero quebra de
    schema no Looker). Firestore nao tem historico — cada dia sem snapshot
    e um ponto de serie perdido."""
    u = {h: i for i, h in enumerate(HEADER_USERS)}
    p = {h: i for i, h in enumerate(HEADER_PROJECTS)}
    q = {h: i for i, h in enumerate(HEADER_PROPOSALS)}
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
    total_aprovado = sum(r[q["valor_aprovado"]] for r in proposals_rows
                         if r[q["status"]].lower() == "aprovado" and r[q["valor_aprovado"]] != "")
    add("valor_aprovado_total", "(todos)", round(total_aprovado, 2))

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

def compute_dashboard_metrics(users_rows, projects_rows, proposals_rows, now_brt):
    """Agregados pra aba Dashboard (consumo humano direto). Mesma fonte de
    verdade em memoria das raw_* — impossivel divergir."""
    u = {h: i for i, h in enumerate(HEADER_USERS)}
    p = {h: i for i, h in enumerate(HEADER_PROJECTS)}
    q = {h: i for i, h in enumerate(HEADER_PROPOSALS)}

    st = Counter(r[p["status"]] for r in projects_rows)
    exp = Counter(r[p["expiracao_situacao"]] for r in projects_rows)
    n_selo = sum(1 for r in projects_rows if r[p["dados_em_atualizacao"]] == "sim")
    # Regua PROPOSTA de "ativo" (a validar com a Tamyris; rotulada na aba):
    # status Disponivel ou Em Execucao E captacao nao expirada.
    ativos = sum(1 for r in projects_rows
                 if r[p["status"]] in ("Disponível", "Em Execução")
                 and r[p["expiracao_situacao"]] != "expirado")

    novos = [r for r in users_rows if r[u["is_migrado"]] == "nao"]
    mes = now_brt.strftime("%Y-%m")
    mes_ant = (now_brt.replace(day=1) - datetime.timedelta(days=1)).strftime("%Y-%m")

    canais = {"organico": 0, "leadlovers": 0, "automatize": 0, "meta_ads": 0,
              "instagram": 0, "comercial": 0, "outro": 0}
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
        "proj_total": len(projects_rows),
        "proj_ativos": ativos,
        "st_disponivel": st.get("Disponível", 0),
        "st_disponivel_selo": n_selo,
        "st_disponivel_completo": st.get("Disponível", 0) - n_selo,
        "st_em_execucao": st.get("Em Execução", 0),
        "st_rascunho": st.get("Rascunho", 0),
        "st_concluido": st.get("Concluído", 0),
        "exp_vigente": exp.get("vigente", 0),
        "exp_expirado": exp.get("expirado", 0),
        "exp_sem_data": exp.get("sem_data", 0),
        "prop_aprovadas": sum(1 for r in proposals_rows if r[q["status"]].lower() == "aprovado"),
        "prop_valor": round(sum(r[q["valor_aprovado"]] for r in proposals_rows
                                if r[q["status"]].lower() == "aprovado"
                                and r[q["valor_aprovado"]] != ""), 2),
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
                           "proposal_hash", "user_hash_match", "legacy_hash"}


def pii_guard(tabs):
    """tabs = {nome_aba: (header, rows)}. Aborta no primeiro hit.
    NUNCA imprime o valor que bateu (log publico) — so aba/linha/coluna."""
    hits = []
    for tab, (header, rows) in tabs.items():
        for i, row in enumerate(rows):
            for j, cell in enumerate(row):
                txt = str(cell)
                patterns = list(PII_PATTERNS_GLOBAIS)
                if header[j] not in COLUNAS_ISENTAS_DIGITOS:
                    patterns += PII_PATTERNS_DIGITOS
                for label, pat in patterns:
                    if pat.search(txt):
                        hits.append(f"{tab}!{header[j]} linha {i + 2} [{label}]")
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
    ws.clear()
    ws.update(values=[header] + rows, range_name="A1")
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
    ws.clear()
    ws.update(values=[HEADER_SNAP] + all_rows, range_name="A1")
    print(f"  snap_diario: {len(snap_rows)} linhas de {today_str} "
          f"(+{len(kept)} historicas preservadas)")

# ===================================================
# MAIN
# ===================================================

def main():
    ap = argparse.ArgumentParser(description="Sync Plataforma Brada -> Sheets")
    ap.add_argument("--dry-run", action="store_true",
                    help="Le Firestore/Auth e imprime distribuicoes, sem escrever no Sheets")
    args = ap.parse_args()

    t0 = time.time()
    today_str = datetime.datetime.now(BRT).strftime("%Y-%m-%d")
    issues = {
        "project_status_desconhecido": Counter(),
        "projects_createdAt_nao_parseado": Counter(),
        "user_role_desconhecido": Counter(),
        "proposal_status_desconhecido": Counter(),
        "emails_com_multiplos_users": Counter(),
        "antiga_data_invalida": Counter(),
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
    antiga_rows = fonte_antiga.carregar_planilha(gc)
    print(f"planilha antiga: {len(antiga_rows)} projetos")

    users_by_id = {doc_id: d for doc_id, d in users_raw}
    projects_rows, projects_by_owner = build_projects(projects_raw, users_by_id, today_str, issues)
    users_rows = build_users(users_raw, login_index, projects_by_owner, issues)
    proposals_rows = build_proposals(proposals_raw, grants_raw, issues)
    mig_rows, mig_metrics = build_migracao(antiga_rows, projects_raw, users_by_id,
                                           login_index, today_str)
    n_inv = mig_metrics["antiga_situacao"].get("data_invalida", 0)
    if n_inv:
        issues["antiga_data_invalida"]["(contagem)"] = n_inv
    snap_rows = build_snapshot(users_rows, projects_rows, proposals_rows, today_str,
                               mig=mig_metrics)

    meta_rows = [
        ["ultima_execucao_brt", datetime.datetime.now(BRT).strftime("%d/%m/%Y %H:%M")],
        ["duracao_s", round(time.time() - t0, 1)],
        ["n_users", len(users_rows)],
        ["n_projects", len(projects_rows)],
        ["n_proposals", len(proposals_rows)],
        ["snapshot_data", today_str],
    ]
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
    }
    pii_guard(tabs)
    print("pii_guard: OK (zero hits)")

    now_brt = datetime.datetime.now(BRT)
    metrics = compute_dashboard_metrics(users_rows, projects_rows, proposals_rows, now_brt)
    metrics.update(mig_metrics)  # bloco MIGRACAO da aba Dashboard

    if args.dry_run:
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
        for r in meta_rows:
            print("meta:", r[0], "=", r[1])
        print("plataforma_sync: DRY-RUN (nada escrito no Sheets)")
        return

    if not SPREADSHEET_ID:
        raise SystemExit("SPREADSHEET_ID ausente (env ou ~/.brada-secrets/plataforma-sync.env).")
    sh = gc.open_by_key(SPREADSHEET_ID)
    write_overwrite(sh, "raw_users", HEADER_USERS, users_rows)
    write_overwrite(sh, "raw_projects", HEADER_PROJECTS, projects_rows)
    write_overwrite(sh, "raw_proposals", HEADER_PROPOSALS, proposals_rows)
    write_overwrite(sh, "raw_migracao_projetos", HEADER_MIGRACAO, mig_rows)
    write_snapshot_idempotente(sh, snap_rows, today_str)
    write_overwrite(sh, "meta_sync", HEADER_META, meta_rows)
    # Dashboard POR ULTIMO: o carimbo de atualizacao so avanca se tudo acima passou
    import dashboard_layout
    status_layout = dashboard_layout.ensure_dashboard(sh, metrics, now_brt.replace(tzinfo=None))
    print(f"  Dashboard: valores atualizados | {status_layout}")
    print(f"plataforma_sync: OK | users={len(users_rows)} projects={len(projects_rows)} "
          f"proposals={len(proposals_rows)} | {round(time.time() - t0, 1)}s")


if __name__ == "__main__":
    main()
