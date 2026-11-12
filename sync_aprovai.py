"""
Sync AprovAI -> Google Sheets (aba `AprovAI` + raw_asaas_aprovai + raw_funil_aprovai
na mesma Sheet `Brada_Dashboard_Produtos` do sync.py principal).

Duas fontes:
- Asaas (conta SOMOS BRADA SOCIAL LTDA, compartilhada com Elaboracao/Assessoria):
  cobrancas filtradas por `is_aprovai_transaction`, porta exata da funcao
  `isAprovaiTransaction` do app (repo iasmartsites/aprovai2, server-analytics.ts),
  pra nao divergir do criterio que o proprio app usa.
- Firestore do projeto `aprovai-de156` (app novo do Thiago): colecao
  `analytics_funnel_events`, unica fonte confiavel de visualizacoes/cliques/leads
  (o contador local do servidor do app, analytics_events_store.json, reseta a
  cada redeploy via AI Studio — nao usar).

PII: nome/e-mail/CPF do cliente Asaas sao usados SO em memoria pra classificar
teste-ou-staff (mesmo criterio do app). Nunca viram coluna nem log (mesma regra
do sync.py / feedback_pii_allowlist_defensiva).

Rodar local: python sync_aprovai.py --dry-run
"""

import argparse
import datetime
import json
import os
import urllib.error
import urllib.request
from collections import Counter

import sync  # reusa auth Sheets, hash_id, pii_guard, write_overwrite, retry
import dashboard_kpi_blocks as kpi
import ga_analytics

# ===================================================
# CONFIG
# ===================================================

APROVAI_FIREBASE_PROJECT = "aprovai-de156"

ASAAS_API_KEY = os.environ.get("ASAAS_APROVAI_API_KEY", "")
ASAAS_ENVIRONMENT = os.environ.get("ASAAS_APROVAI_ENVIRONMENT", "production")
# Mesma conta dashboard-leitura das abas Comunidade e Plataforma: ganhou
# datastore.viewer + firebaseauth.viewer em aprovai-de156 tambem (10/11), entao
# nao precisa mais da service account dedicada aprovai-sa.json (11/11).
COMUNIDADE_SA_JSON = os.environ.get("COMUNIDADE_SA_JSON", "")
COMUNIDADE_SA_FILE = os.environ.get(
    "COMUNIDADE_SA_FILE", os.path.expanduser(r"~/.brada-secrets/comunidade-dashboard-sa.json")
)

# Local dev: carrega do .env dedicado se as vars nao vieram do ambiente (CI).
sync._load_local_env(os.path.expanduser(r"~/.brada-secrets/asaas-aprovai.env"))
if not ASAAS_API_KEY:
    ASAAS_API_KEY = os.environ.get("ASAAS_API_KEY", "")
if os.environ.get("ASAAS_ENVIRONMENT"):
    ASAAS_ENVIRONMENT = os.environ["ASAAS_ENVIRONMENT"]

ASAAS_BASE = ("https://api.asaas.com/v3" if ASAAS_ENVIRONMENT == "production"
              else "https://api-sandbox.asaas.com/v3")

STAFF_DOMAINS = ("@brada.social", "@criape.com.br")
STAFF_MARKERS = ("teste", "test@")

HEADER_ASAAS = [
    "sync_em", "payment_id_hash", "customer_id_hash", "status", "value",
    "billing_type", "date_created", "due_date", "e_teste_ou_staff",
]
HEADER_FUNIL = ["data", "visualizacoes_pagina", "cliques_cta", "leads_enviados", "fonte"]


# ===================================================
# ASAAS
# ===================================================

def asaas_get(path):
    req = urllib.request.Request(ASAAS_BASE + path, headers={"access_token": ASAAS_API_KEY})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read())


def asaas_paginate(path_tpl):
    out, offset = [], 0
    while True:
        data = asaas_get(path_tpl.format(offset=offset))
        out.extend(data["data"])
        if not data["hasMore"]:
            return out
        offset += 100


def is_aprovai_transaction(item):
    """Porta exata de `isAprovaiTransaction` (aprovai2/server-analytics.ts),
    pra nunca divergir do criterio que o proprio app usa pra contar assinante."""
    if not item:
        return False
    desc = str(item.get("description") or "").strip().lower()
    ext_ref = str(item.get("externalReference") or "").strip().lower()

    negativos = ("brada", "esporte", "cidadania", "dou", "nf ", "target")
    if any(n in desc for n in negativos) or any(n in ext_ref for n in ("brada", "esporte", "cidadania", "target")):
        return False

    # "aprova" cobre "aprovai" e "aprovaí" (as duas grafias usadas no app) sem
    # depender de um literal acentuado sobrevivendo intacto em todo ambiente de execucao.
    if "aprova" in desc or "aprova" in ext_ref:
        return True

    valor = item.get("value")
    try:
        valor = float(valor)
    except (TypeError, ValueError):
        valor = None
    if valor == 89 and "brada" not in desc and "nf" not in desc:
        return True
    return False


def is_teste_ou_staff(customer):
    email = str((customer or {}).get("email") or "").strip().lower()
    if not email:
        return True  # sem e-mail = nao da pra confiar, trata como nao-oficial
    if any(dom in email for dom in STAFF_DOMAINS):
        return True
    return any(marker in email for marker in STAFF_MARKERS)


def coletar_asaas_rows(now_iso):
    todos = asaas_paginate("/payments?limit=100&offset={offset}")
    aprovai = [p for p in todos if is_aprovai_transaction(p)]

    customer_cache = {}

    def get_customer(cid):
        if cid not in customer_cache:
            try:
                customer_cache[cid] = asaas_get(f"/customers/{cid}")
            except urllib.error.HTTPError:
                customer_cache[cid] = {}
        return customer_cache[cid]

    rows = []
    for p in aprovai:
        cust = get_customer(p.get("customer"))
        rows.append([
            now_iso,
            sync.hash_id(p.get("id")),
            sync.hash_id(p.get("customer")),
            p.get("status"),
            p.get("value"),
            p.get("billingType"),
            p.get("dateCreated"),
            p.get("dueDate"),
            is_teste_ou_staff(cust),
        ])
    return rows


def resumo_asaas(rows):
    reais = [r for r in rows if r[3] in ("RECEIVED", "CONFIRMED") and not r[8]]
    teste_ou_staff_sucesso = [r for r in rows if r[3] in ("RECEIVED", "CONFIRMED") and r[8]]
    mrr = round(sum(r[4] for r in reais), 2)
    return {
        "total_cobrancas_aprovai": len(rows),
        "assinantes_reais_ativos": len(reais),
        "mrr_real": mrr,
        "cobrancas_teste_ou_staff_sucesso": len(teste_ou_staff_sucesso),
    }


# ===================================================
# FIRESTORE (projeto aprovai-de156)
# ===================================================

def init_aprovai_firestore():
    from google.cloud import firestore

    creds = ga_analytics.credenciais(COMUNIDADE_SA_JSON, COMUNIDADE_SA_FILE, "COMUNIDADE_ACCESS_TOKEN")
    return firestore.Client(project=APROVAI_FIREBASE_PROJECT, credentials=creds)


def coletar_pre_approvals(db):
    """Achado do audit de 11/11: o card de assinantes/MRR so olha o Asaas, que
    mostrou 0 assinante real numa rodada ao vivo. A collection `payment_pre_approvals`
    (app do Thiago) tem 22 registros `subscriptionStatus=active`, 18 deles com
    `migratedFromKiwify=true` e SEM `subscriptionExpiresAt`/`initialSubscriptionDate`
    (nenhuma assinatura recorrente foi de fato provisionada no Asaas pra eles -
    parecem artefato de migracao, nao assinante cobrando hoje). Os outros 4 tem
    data de inicio/expiracao real. Nao da pra arbitrar qual numero e o assinante
    'de verdade' sem o Thiago; o certo e mostrar os dois lados, nao escolher um."""
    docs = [d.to_dict() for d in db.collection("payment_pre_approvals").stream()]
    return resumo_pre_approvals(docs)


def resumo_pre_approvals(docs):
    ativos = [d for d in docs if d.get("subscriptionStatus") == "active"]
    migrados_sem_billing = sum(1 for d in ativos if d.get("migratedFromKiwify") and not d.get("subscriptionExpiresAt"))
    return {
        "total_ativos_no_app": len(ativos),
        "migrados_kiwify_sem_billing_asaas": migrados_sem_billing,
        "com_billing_asaas_real": len(ativos) - migrados_sem_billing,
    }


def coletar_funil_rows(db):
    eventos = list(db.collection("analytics_funnel_events").stream())
    por_dia = {}
    for e in eventos:
        d = e.to_dict()
        dia = str(d.get("timestamp", ""))[:10]
        if not dia:
            continue
        por_dia.setdefault(dia, Counter())
        t = d.get("type")
        if t in ("page_view", "cta_click", "lead_captured"):
            por_dia[dia][t] += 1

    rows = []
    for dia in sorted(por_dia.keys()):
        c = por_dia[dia]
        rows.append([dia, c["page_view"], c["cta_click"], c["lead_captured"],
                     "Firestore analytics_funnel_events (aprovai-de156)"])
    return rows


# ===================================================
# DASHBOARD
# ===================================================

def montar_secoes(funil_rows, resumo, pre_approvals):
    total_views = sum(r[1] for r in funil_rows)
    total_clicks = sum(r[2] for r in funil_rows)
    total_leads = sum(r[3] for r in funil_rows)

    sec1 = kpi.Secao(
        "Funil da landing (via Firestore, historico completo)",
        [
            (total_views, "Visualizacoes da pagina", None),
            (total_clicks, "Clicaram no CTA", None),
            (total_leads, "Leads enviados", None),
            (resumo["assinantes_reais_ativos"], "Compraram (confirmado no Asaas)", None),
        ],
        nota=(
            "Views/cliques/leads vem do Firestore (analytics_funnel_events), nao do contador local "
            "do servidor do app (reseta a cada redeploy). Compras vem direto da API do Asaas."
        ),
    )

    pct_confirmadas = (round(100 * resumo["assinantes_reais_ativos"] / resumo["total_cobrancas_aprovai"], 1)
                        if resumo["total_cobrancas_aprovai"] else 0)
    sec2 = kpi.Secao(
        "Conversao e assinantes (Asaas)",
        [
            (pct_confirmadas / 100, "% Cobrancas confirmadas (das criadas)", {"type": "PERCENT", "pattern": "0.0%"}),
            (resumo["assinantes_reais_ativos"], "Assinantes ativos reais (nao staff/teste)", None),
            (resumo["mrr_real"], "MRR recorrente real", {"type": "CURRENCY", "pattern": "R$#,##0.00"}),
            (resumo["cobrancas_teste_ou_staff_sucesso"], "Cobrancas 'sucesso' de teste/staff (fora da contagem)", None),
        ],
        nota=(
            f"{resumo['total_cobrancas_aprovai']} cobrancas do AprovAI identificadas na Asaas no total "
            "(conta compartilhada com Elaboracao/Assessoria, filtradas pelo mesmo criterio que o app usa). "
            "Detalhe linha a linha (sem PII) em raw_asaas_aprovai."
        ),
        nota_aviso=True,
    )

    sec3 = kpi.Secao(
        "Assinantes no app (Firestore) vs cobranca confirmada (Asaas) - divergencia a validar com o Thiago",
        [
            (pre_approvals["total_ativos_no_app"], "Marcados 'ativo' no app (payment_pre_approvals)", None),
            (pre_approvals["migrados_kiwify_sem_billing_asaas"], "Migrados do Kiwify sem assinatura Asaas provisionada", None),
            (pre_approvals["com_billing_asaas_real"], "Com data de inicio/expiracao real no Asaas", None),
            (resumo["assinantes_reais_ativos"], "Confirmados por cobranca recebida no Asaas", None),
        ],
        nota=(
            "O app marca 22 usuarios como assinante ativo, mas 18 vieram da migracao do Kiwify sem "
            "'subscriptionExpiresAt'/'initialSubscriptionDate' preenchido - nunca tiveram assinatura "
            "recorrente provisionada no Asaas, o que sugere artefato de migracao, nao cobranca real "
            "em curso. So 4 tem data real, e mesmo esses nao aparecem confirmados na rodada do Asaas. "
            "Numero de assinante/MRR confiavel depende de o Thiago validar qual lado esta certo."
        ),
        nota_aviso=True,
    )
    return [sec1, sec2, sec3]


# ===================================================
# MAIN
# ===================================================

def main():
    ap = argparse.ArgumentParser(description="Sync AprovAI -> Sheets")
    ap.add_argument("--dry-run", action="store_true", help="le tudo, imprime, nao escreve")
    args = ap.parse_args()

    if not ASAAS_API_KEY:
        raise SystemExit("ASAAS_APROVAI_API_KEY (ou ASAAS_API_KEY local) nao configurada.")
    if (not COMUNIDADE_SA_JSON and not os.path.exists(COMUNIDADE_SA_FILE)
            and not os.environ.get("COMUNIDADE_ACCESS_TOKEN")):
        raise SystemExit("Credenciais do Firestore do AprovAI (dashboard-leitura) nao encontradas.")

    now = datetime.datetime.now(sync.BRT)
    now_iso = now.isoformat(timespec="seconds")

    print("Puxando Asaas (cobrancas do AprovAI)...")
    asaas_rows = coletar_asaas_rows(now_iso)
    resumo = resumo_asaas(asaas_rows)
    print(f"  {resumo['total_cobrancas_aprovai']} cobrancas do AprovAI | "
          f"{resumo['assinantes_reais_ativos']} assinantes reais | MRR R${resumo['mrr_real']}")

    print("Puxando Firestore (analytics_funnel_events, aprovai-de156)...")
    db = init_aprovai_firestore()
    funil_rows = coletar_funil_rows(db)
    print(f"  {len(funil_rows)} dias com evento")

    pre_approvals = coletar_pre_approvals(db)
    print(f"  payment_pre_approvals: {pre_approvals['total_ativos_no_app']} ativos no app, "
          f"{pre_approvals['migrados_kiwify_sem_billing_asaas']} migrados do Kiwify sem billing real")

    tabs = {
        "raw_asaas_aprovai": (HEADER_ASAAS, asaas_rows),
        "raw_funil_aprovai": (HEADER_FUNIL, funil_rows),
    }
    sync.pii_guard(tabs)
    print("pii_guard: OK (zero hits)")

    secoes = montar_secoes(funil_rows, resumo, pre_approvals)
    subtitulo = (f"Foto: {now.strftime('%d/%m/%Y %H:%M')} (Brasilia) - "
                 "views/cliques/leads do Firestore (aprovai-de156); compras/assinantes/MRR da API do Asaas")

    if args.dry_run:
        print("\n--dry-run: nada escrito na Sheet.")
        for nome, (header, rows) in tabs.items():
            print(f"  {nome}: {len(rows)} linhas")
        for secao in secoes:
            print(f"  [{secao.titulo}]", secao.metricas)
        return

    gc = sync.get_sheets_client()
    sh = gc.open_by_key(sync.SPREADSHEET_ID)
    for nome, (header, rows) in tabs.items():
        sync.write_overwrite(sh, nome, header, rows)

    svc = build_sheets_service()
    kpi.publish(sh, svc, sync.SPREADSHEET_ID, "AprovAI", "AprovAI — Funil de conversao e assinantes (Asaas)",
                subtitulo, secoes)
    print("AprovAI: aba e raws atualizadas.")


def build_sheets_service():
    """gspread (sync.get_sheets_client) nao expoe as credenciais carregadas;
    o builder de KPI precisa do service raw (googleapiclient) pra batchUpdate
    de formatacao/merge. Carrega a MESMA credencial de novo, mesmo caminho de
    sync.get_sheets_client (JSON no ambiente pro CI, arquivo local pro dev)."""
    import json as _json
    from googleapiclient.discovery import build
    from google.oauth2.service_account import Credentials

    scopes = ["https://www.googleapis.com/auth/spreadsheets", "https://www.googleapis.com/auth/drive"]
    if sync.SHEETS_SA_JSON:
        info = _json.loads(sync.SHEETS_SA_JSON.encode("utf-8").decode("utf-8-sig"))
        creds = Credentials.from_service_account_info(info, scopes=scopes)
    else:
        creds = Credentials.from_service_account_file(sync.SHEETS_SA_FILE, scopes=scopes)
    return build("sheets", "v4", credentials=creds)


if __name__ == "__main__":
    main()
