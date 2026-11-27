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
import dashboard_painel as dp
import ga_analytics
import painel_filtros as fl
import web_visitas
from painel_filtros import F

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

STAFF_DOMAINS = ("@brada.social", "@criape.com.br", "@somosbrada.com.br", "brada.social.com")
STAFF_MARKERS = ("teste", "test@")

HEADER_ASAAS = [
    "sync_em", "payment_id_hash", "customer_id_hash", "status", "value",
    "billing_type", "date_created", "due_date", "e_teste_ou_staff",
]
# Auditoria de 26/11: visitas sao PESSOAS (visitorId), cada uma no dia da primeira visita; dia pelo horario de Brasilia.
HEADER_FUNIL = ["data", "visitantes_novos", "cliques_comprar", "leads_enviados", "fonte", "checkout_aberto",
                "compras"]


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


DIAS_CICLO_ATIVO = 35  # ciclo mensal + folga de atraso


def resumo_asaas(rows, hoje=None):
    """Assinante = CLIENTE distinto com cobranca paga vencida nos ultimos 35 dias; MRR = ultimo valor
    pago de cada um. Contar cobranca paga somava o mesmo cliente a cada mes (17/11 + 17/12 = 2)."""
    hoje = hoje or datetime.date.today()
    pagas = [r for r in rows if r[3] in ("RECEIVED", "CONFIRMED")]
    teste_ou_staff_sucesso = [r for r in pagas if r[8]]
    ativos = {}
    for r in sorted((r for r in pagas if not r[8]), key=lambda r: str(r[7] or "")):
        venc = str(r[7] or "")[:10]
        if venc and 0 <= (hoje - datetime.date.fromisoformat(venc)).days <= DIAS_CICLO_ATIVO:
            ativos[r[2]] = float(r[4] or 0)
    mrr = round(sum(ativos.values()), 2)
    return {
        "total_cobrancas_aprovai": len(rows),
        "assinantes_reais_ativos": len(ativos),
        "mrr_real": mrr,
        "cobrancas_teste_ou_staff_sucesso": len(teste_ou_staff_sucesso),
    }


def coletar_assinaturas():
    """Assinaturas do AprovAI no Asaas, inclusive as apagadas (cancelar a recorrencia apaga a assinatura).
    So o necessario para contar: situacao, se foi apagada e se e equipe ou teste. Nada de cliente sai daqui."""
    subs = [x for x in asaas_paginate("/subscriptions?includeDeleted=true&limit=100&offset={offset}")
            if is_aprovai_transaction(x)]
    clientes = {}
    for x in subs:
        cid = x.get("customer")
        if cid not in clientes:
            try:
                clientes[cid] = asaas_get(f"/customers/{cid}")
            except urllib.error.HTTPError:
                clientes[cid] = {}
    return [{"status": x.get("status"), "apagada": bool(x.get("deleted")),
             "equipe": is_teste_ou_staff(clientes[x.get("customer")])} for x in subs]


def resumo_assinaturas(assinaturas, asaas_rows):
    """Revisao do Bruno (25/11): assinantes ativos, quem cancelou a recorrencia e quem pediu o dinheiro de volta,
    cada um separado. Ativa = ACTIVE e nao apagada; cancelada = apagada ou inativa; estorno = cobranca devolvida."""
    reais = [a for a in assinaturas if not a["equipe"]]
    ativas = sum(1 for a in reais if a["status"] == "ACTIVE" and not a["apagada"])
    return {"assinaturas_ativas": ativas, "assinaturas_canceladas": len(reais) - ativas,
            "estornos": sum(1 for r in asaas_rows if not r[8] and r[3] in ("REFUNDED", "REFUND_REQUESTED"))}


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
    # Sem equipe e teste (auditoria de 24/11: 14 dos 25 "ativos" eram da equipe ou de teste).
    # Auditoria de 26/11: "Planos contratados" mostrava 4 com 2 pagantes no Asaas. Um era o mesmo cliente Asaas
    # gravado em dois documentos (dois e-mails, mesmo asaasCustomerId) e o outro um migrado do Kiwify COM data de
    # expiracao (passava no corte antigo). Agora: pagante unico por cliente/assinatura Asaas, e migrado do Kiwify
    # nunca conta, tenha ou nao data.
    docs = [d for d in (x.to_dict() for x in db.collection("payment_pre_approvals").stream())
            if not is_teste_ou_staff(d)]
    return dict(resumo_pre_approvals(docs), lead_ids=leads_que_assinaram(docs), origens=assinantes_por_origem(docs))


def _assinatura_real(d):
    """Ativa no app e nao migrada do Kiwify (o migrado nao paga no Asaas, com ou sem data de expiracao)."""
    return d.get("subscriptionStatus") == "active" and not d.get("migratedFromKiwify")


def _chave_pagante(d):
    """Quem paga: cliente Asaas, depois assinatura Asaas, depois e-mail. So em memoria, nunca sai daqui."""
    for campo in ("asaasCustomerId", "asaasSubscriptionId"):
        if d.get(campo):
            return (campo, str(d[campo]))
    email = str(d.get("email") or "").strip().lower()
    return ("email", email) if email else ("doc", id(d))


def assinaturas_reais(docs):
    """Uma por pagante. Documentos do mesmo pagante viram um so, juntando os campos preenchidos
    (a origem/utm pode estar so num deles). Nao altera os documentos de entrada."""
    grupos = {}
    for d in docs:
        if _assinatura_real(d):
            grupos.setdefault(_chave_pagante(d), []).append(d)
    juntos = []
    for grupo in grupos.values():
        junto = {}
        for d in grupo:
            junto.update({k: v for k, v in d.items() if v and not junto.get(k)})
        juntos.append(junto)
    return juntos


def _utm(d, campo):
    att = d.get("attribution") if isinstance(d.get("attribution"), dict) else {}
    return str(d.get(campo) or att.get(campo) or "").strip()


def leads_que_assinaram(docs):
    """lead_id da Brenda (a Automatize manda no utm_term do link) de cada assinatura real. Fica so em memoria:
    o cruzamento com o SDR de elaboracao sai agregado; na planilha vai so o hash."""
    return frozenset(_utm(d, "utm_term") for d in docs if _assinatura_real(d) and _utm(d, "utm_term"))


def assinantes_por_origem(docs):
    """[(utm_source, pagantes unicos)] — origem gravada pelo site desde 23/11."""
    def legivel(o):
        return f"link vindo de {o[4:]}" if o.startswith("ref:") else (o or "Antes do rastreio (ate 23/11)")
    cont = Counter(legivel(_utm(d, "utm_source")) for d in assinaturas_reais(docs))
    return sorted(cont.items(), key=lambda x: (-x[1], x[0]))


def resumo_pre_approvals(docs):
    """total_ativos_no_app = contas com acesso; com_billing_asaas_real = pagantes unicos (sem Kiwify, sem duplicado)."""
    ativos = [d for d in docs if d.get("subscriptionStatus") == "active"]
    return {
        "total_ativos_no_app": len(ativos),
        "migrados_kiwify_sem_billing_asaas": sum(1 for d in ativos if d.get("migratedFromKiwify")),
        "com_billing_asaas_real": len(assinaturas_reais(docs)),
    }


def cpf_valido(cpf):
    """Digitos verificadores do CPF. Lead com CPF invalido e preenchimento de teste
    (o unico lead do app ate 13/11 tinha CPF 1234567890)."""
    d = [int(c) for c in str(cpf or "") if c.isdigit()]
    if len(d) != 11 or len(set(d)) == 1:
        return False
    for n in (9, 10):
        resto = sum(d[i] * (n + 1 - i) for i in range(n)) * 10 % 11
        if resto % 10 != d[n]:
            return False
    return True


def lead_de_teste(evento):
    """Lead com CPF preenchido e invalido nao conta. Sem CPF, conta (nao da pra saber)."""
    lead = evento.get("lead")
    cpf = lead.get("cpf") if isinstance(lead, dict) else None
    return bool(cpf) and not cpf_valido(cpf)


# Botoes que sao cta_click mas NAO levam ao checkout: login ("Ja sou Membro", "Entrar com ...") e
# "Ja tenho uma conta" (hero_existing_account, auditoria de 26/11).
CTA_FORA_DA_COMPRA = ("login", "existing_account")


def clique_de_compra(evento):
    """So o botao que leva ao checkout ("Garantir Meu Acesso")."""
    cta = str(evento.get("ctaId") or "").lower()
    return not any(m in cta for m in CTA_FORA_DA_COMPRA)


def _n(r, i):
    return r[i] if len(r) > i and isinstance(r[i], (int, float)) else 0


try:
    from zoneinfo import ZoneInfo
    FUSO_BRASILIA = ZoneInfo("America/Sao_Paulo")
except Exception:  # sem base de fusos (tzdata) no ambiente: Brasilia e UTC-3 fixo desde 2019
    FUSO_BRASILIA = sync.BRT


def dia_brasilia(ts):
    """Dia do evento pelo horario de Brasilia. O app grava ISO em UTC ("...Z"): 21h de Brasilia ja e o dia
    seguinte em UTC, entao cortar a string jogava o fim da noite no dia errado (auditoria de 26/11)."""
    if isinstance(ts, datetime.datetime):
        dt = ts
    else:
        txt = str(ts or "").strip()
        if not txt:
            return ""
        try:
            dt = datetime.datetime.fromisoformat(txt.replace("Z", "+00:00"))
        except ValueError:
            return txt[:10]
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=datetime.timezone.utc)
    return dt.astimezone(FUSO_BRASILIA).date().isoformat()


def coletar_funil_rows(db):
    """Linhas por dia (Brasilia). Visitas = pessoas: cada visitorId conta uma vez, no dia da primeira visita,
    para que a soma de qualquer periodo nao repita a mesma pessoa (auditoria de 26/11: 268 eventos, ~107 pessoas).
    Num recorte que comeca depois da primeira visita, quem voltou nao entra de novo."""
    eventos = list(db.collection("analytics_funnel_events").stream())
    por_dia, checkout, primeira_visita = {}, {}, {}
    for e in eventos:
        d = e.to_dict()
        dia = dia_brasilia(d.get("timestamp"))
        if not dia:
            continue
        por_dia.setdefault(dia, Counter())
        t = d.get("type")
        quem = d.get("visitorId") or getattr(e, "id", None) or id(e)
        if t == "lead_captured" and lead_de_teste(d):
            continue
        if t == "cta_click" and not clique_de_compra(d):
            continue
        if t == "page_view":
            primeira_visita[quem] = min(primeira_visita.get(quem, dia), dia)
            continue
        if t == "checkout_view":  # meta da Tamyris e visitante do checkout, nao abertura do modal
            checkout.setdefault(dia, set()).add(quem)
            continue
        if t in ("cta_click", "lead_captured", "purchase_completed"):
            por_dia[dia][t] += 1
    for dia in primeira_visita.values():
        por_dia[dia]["page_view"] += 1

    rows = []
    for dia in sorted(por_dia.keys()):
        c = por_dia[dia]
        rows.append([dia, c["page_view"], c["cta_click"], c["lead_captured"],
                     "Firestore analytics_funnel_events (aprovai-de156)", len(checkout.get(dia, ())),
                     c["purchase_completed"]])
    return rows


# ===================================================
# DASHBOARD
# ===================================================

SITUACAO_COBRANCA = {"RECEIVED": "Pagas", "CONFIRMED": "Pagas", "RECEIVED_IN_CASH": "Pagas",
                     "REFUNDED": "Estornadas", "REFUND_REQUESTED": "Estornadas", "PENDING": "Pendentes",
                     "OVERDUE": "Vencidas"}


def completar_elaboracao(leads, assinaram=frozenset()):
    """Etapas cumulativas do SDR de elaboracao (abriu so existe no e-mail). Fim = assinou o AprovAI
    (decisao do Bruno, 23/11): o lead_id do disparo voltou no utm_term de uma assinatura real."""
    for lead_id, l in leads.items():
        l["assinou"] = lead_id in assinaram
        l["engajou"] = l["clicou"] or l["respondeu"] or l["cadastro"] or l["assinou"]
        l["abriu"] = l["abriu"] or l["engajou"]
    return leads


def funil_elaboracao(leads):
    v = list(leads.values())
    return [("Disparados", len(v)), ("Abriram", sum(l["abriu"] for l in v)),
            ("Clicaram ou responderam", sum(l["engajou"] for l in v)),
            ("Assinaram o AprovAI", sum(l.get("assinou", False) for l in v))]


def rotulo_checkout(funil_rows):
    """Meta da Tamyris (24/11): assinaturas sobre visitantes do checkout."""
    if tem_checkout(funil_rows):
        return "Checkout ate a compra"
    return "Clique em comprar ate a compra"


def funil_app(funil_rows, resumo):
    return [("Visitas a pagina", sum(r[1] for r in funil_rows)), ("Clicaram em comprar", sum(r[2] for r in funil_rows)),
            ("Leads", sum(r[3] for r in funil_rows)), ("Assinantes pagantes", resumo["assinantes_reais_ativos"])]


def cobrancas_por_situacao(asaas_rows):
    cont = Counter(SITUACAO_COBRANCA.get(r[3], "Outras") for r in asaas_rows if not r[8])
    return sorted(cont.items(), key=lambda x: -x[1])


def visitas_por_dia(funil_rows, hoje, dias=30):
    por = {r[0]: r for r in funil_rows}
    out = []
    for i in range(dias - 1, -1, -1):
        d = hoje - datetime.timedelta(days=i)
        r = por.get(d.isoformat())
        out.append([d.strftime("%d/%m"), r[1] if r else 0, r[2] if r else 0])
    return out


ABA = "AprovAI"
BASE_SITE = fl.Base("base_aprovai_site", [], [
    ("visitas", "Visitas"), ("cliques", "Clicaram em comprar"), ("leads", "Leads"), ("checkout", "Abriram o checkout"),
    ("compras", "Compraram")])


def tem_checkout(funil_rows):
    """O evento de abertura do checkout (pedido ao Thiago em 24/11) ja chegou? Ate la, o clique em comprar
    ("Garantir Meu Acesso", que leva ao checkout) faz as vezes de visitante do checkout."""
    return any(_n(r, 5) for r in funil_rows)


def funil_site(funil_rows):
    etapas = [("Visitas a pagina", "visitas"), ("Clicaram em comprar", "cliques")]
    if tem_checkout(funil_rows):
        etapas.append(("Abriram o checkout", "checkout"))
    return etapas + [("Compraram", "compras")]


def topo_do_checkout(funil_rows):
    return "checkout" if tem_checkout(funil_rows) else "cliques"
BASE_ELAB = fl.Base("base_aprovai_brenda", [("canal", "Canal", fl.TODOS), ("fonte", "Fonte", fl.TODAS)], [
    ("disparados", "Disparados"), ("entregues", "Entregues"), ("abriram", "Abriram"),
    ("engajaram", "Clicaram ou responderam"), ("cadastro", "Cadastro")])
FUNIL_ELAB = [("Disparados", "disparados"), ("Entregues", "entregues"), ("Abriram", "abriram"),
              ("Clicaram ou responderam", "engajaram"), ("Assinaram o AprovAI", "cadastro")]
CANAIS = ["E-mail", "WhatsApp", "E-mail + WhatsApp"]


def linhas_site(funil_rows):
    return BASE_SITE.agregar(
        (datetime.date.fromisoformat(r[0]), {}, {"visitas": r[1], "cliques": r[2], "leads": r[3],
                                                  "checkout": _n(r, 5), "compras": _n(r, 6)})
        for r in funil_rows if r[0])


def linhas_elab(leads):
    import sdr_automatize as sdra
    return BASE_ELAB.agregar(
        (l["dia"], {"canal": sdra.canal_do_lead(l["canais"]), "fonte": l.get("fonte")},
         {"disparados": 1, "entregues": l["abriu"] or not l.get("bounce"), "abriram": l["abriu"],
          "engajaram": l["engajou"], "cadastro": l.get("assinou", False)})
        for l in (leads or {}).values())


def _visitas_vivas(fsite):
    linha = [F(fsite.sequencia()), F(fsite.por_ponto("visitas")), F(fsite.por_ponto("cliques"))]
    return fl.SerieViva("Site: visitas e cliques no periodo A", ["Dia", "Visitas", "Cliques"], [linha],
                        n_linhas=fl.MAX_PONTOS, formato_dominio=dp.FMT_DIA, tipo="LINE", rotulos=False,
                        altura=320, subtitulo="Por dia ou semana · todas as origens")


def montar_painel(funil_rows, resumo, pre_approvals, asaas_rows, leads_elab, envios_elab, agora, web=None,
                  assinaturas=None):
    """Aba do produto: abre pelo Asaas (quem paga e quanto), depois o site e as visitas.
    O SDR de elaboracao (Brenda) nao aparece aqui: mora na aba Brenda; so os numeros dele seguem
    para a Visao Geral pelo resumo."""
    hoje = agora.date()
    app = funil_app(funil_rows, resumo)
    site, elab_l = linhas_site(funil_rows), linhas_elab(leads_elab)
    ctl = fl.controles(ABA, hoje)
    fs = fl.Filtros(ctl, BASE_SITE)
    p = dp.Painel(ABA, "AprovAI · do visitante ao assinante pagante",
                  f"Atualizado {agora.strftime('%d/%m/%Y %H:%M')} · Asaas + Firestore do app + Google Analytics"
                  " · anuncios e custo por assinante entram quando houver acesso ao Meta Ads e UTM no site"
                  " · mude as células claras abaixo")
    p.linha(ctl)
    p.linha(dp.Cartao("Assinantes pagando (Asaas, hoje)", resumo["assinantes_reais_ativos"]),
            dp.Cartao("Receita recorrente (MRR, Asaas, hoje)", resumo["mrr_real"], dp.FMT_BRL),
            fl.CartaoVivo("Site: visitas a pagina", fs.soma_p("visitas", "A"), fs.soma_p("visitas", "B")),
            fl.CartaoVivo(rotulo_checkout(funil_rows), fs.taxa("compras", "A", sobre=topo_do_checkout(funil_rows)),
                          fs.taxa("compras", "B", sobre=topo_do_checkout(funil_rows)), dp.FMT_PCT))
    sub = resumo_assinaturas(assinaturas or [], asaas_rows)
    p.linha(dp.Cartao("Assinaturas ativas (Asaas, hoje)", sub["assinaturas_ativas"]),
            dp.Cartao("Cancelaram a recorrencia (desde o inicio)", sub["assinaturas_canceladas"]),
            dp.Cartao("Estornos: dinheiro devolvido (desde o inicio)", sub["estornos"]),
            dp.Cartao("Acesso no app sem assinatura (hoje)",
                      max(pre_approvals["total_ativos_no_app"] - sub["assinaturas_ativas"], 0)))
    p.linha(dp.Tabela("Cobrancas no Asaas hoje (sem equipe)", ["Situacao", "Cobrancas"],
                      [[s, n] for s, n in cobrancas_por_situacao(asaas_rows)], formatos=[None, dp.FMT_INT]))
    brenda = sum(l.get("assinou", False) for l in (leads_elab or {}).values())
    p.linha(dp.Tabela("Planos contratados no app, por origem (hoje)", ["Origem gravada no cadastro", "Planos"],
                      [[o, n] for o, n in pre_approvals.get("origens", [])]
                      + [["Dos quais vieram de um disparo da Brenda (medido desde 23/11)", brenda]],
                      formatos=[None, dp.FMT_INT], spans=[9, 3]))
    aviso_site = ("Contagem do proprio site · visitas = pessoas, cada uma no dia da 1a visita (horario de Brasilia)"
                  " · 'Clicaram em comprar' = botao Garantir Meu Acesso, que leva ao checkout (login e 'Ja tenho uma"
                  " conta' ficam fora) · o Google Analytics, mais abaixo, conta sessoes desde 16/11")
    p.linha(fl.FunilVivo("Site do AprovAI (periodo A)", fs, funil_site(funil_rows), altura=340, subtitulo=aviso_site),
            _visitas_vivas(fs))
    elab = funil_elaboracao(leads_elab) if leads_elab is not None else None
    topo = 5 if tem_checkout(funil_rows) else 2
    resumo_kpi = {"aprovai_visitas": app[0][1], "aprovai_leads": app[2][1],
                  "aprovai_checkout": sum(_n(r, topo) for r in funil_rows),
                  "aprovai_compras": sum(_n(r, 6) for r in funil_rows),
                  "aprovai_assinantes": resumo["assinantes_reais_ativos"], "aprovai_mrr": resumo["mrr_real"],
                  "aprovai_ativos_app": pre_approvals["total_ativos_no_app"],
                  "aprovai_assinaturas_ativas": sub["assinaturas_ativas"],
                  "aprovai_canceladas": sub["assinaturas_canceladas"], "aprovai_estornos": sub["estornos"]}
    if elab:
        resumo_kpi.update({"elaboracao_disparados": elab[0][1], "elaboracao_engajaram": elab[2][1],
                           "elaboracao_assinantes": elab[-1][1]})
    extras = []
    if web is not None:
        web_visitas.blocos(p, ctl, "aprovai", web)
        extras = web_visitas.linhas_bases("aprovai", web)
    return p, resumo_kpi, [(BASE_SITE, site), (BASE_ELAB, elab_l)] + extras


def coletar_elaboracao(assinaram=frozenset()):
    """(leads, envios) do SDR de elaboracao, ou (None, None) se o banco nao responder."""
    import sdr_automatize as sdra
    try:
        conn = sdra.conectar()
        try:
            envios = sdra.envios(conn, sdra.ELABORACAO)
        finally:
            conn.close()
    except (Exception, SystemExit) as e:
        print(f"[aprovai] SDR de elaboracao indisponivel: {str(e)[:160]}")
        return None, None
    return completar_elaboracao(sdra.por_lead(envios), assinaram), envios


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
    resumo = resumo_asaas(asaas_rows, now.date())
    print(f"  {resumo['total_cobrancas_aprovai']} cobrancas do AprovAI | "
          f"{resumo['assinantes_reais_ativos']} assinantes reais | MRR R${resumo['mrr_real']}")

    print("Puxando Firestore (analytics_funnel_events, aprovai-de156)...")
    db = init_aprovai_firestore()
    funil_rows = coletar_funil_rows(db)
    print(f"  {len(funil_rows)} dias com evento")

    assinaturas = coletar_assinaturas()
    print(f"  {len(assinaturas)} assinaturas no Asaas (com apagadas e equipe)")
    pre_approvals = coletar_pre_approvals(db)
    print(f"  payment_pre_approvals: {pre_approvals['total_ativos_no_app']} ativos no app, "
          f"{pre_approvals['migrados_kiwify_sem_billing_asaas']} migrados do Kiwify sem billing real")

    tabs = {
        "raw_asaas_aprovai": (HEADER_ASAAS, asaas_rows),
        "raw_funil_aprovai": (HEADER_FUNIL, funil_rows),
    }
    print("Puxando banco do SDR Brada (so elaboracao)...")
    leads_elab, envios_elab = coletar_elaboracao(pre_approvals["lead_ids"])
    if leads_elab is not None:
        tabs["raw_elaboracao_assinantes"] = (["lead_id_hash"], [[sync.hash_id(lid)] for lid, l in leads_elab.items()
                                                                  if l.get("assinou")])
    sync.pii_guard(dict(tabs, origens_assinantes=(["origem", "n"], [list(o) for o in pre_approvals["origens"]])))
    print("pii_guard: OK (zero hits)")
    painel, resumo_kpi, bases = montar_painel(funil_rows, resumo, pre_approvals, asaas_rows, leads_elab, envios_elab, now,
                                              web_visitas.coletar("aprovai", now.date()), assinaturas)
    print("  resumo:", resumo_kpi)

    if args.dry_run:
        print("\n--dry-run: nada escrito na Sheet.")
        for nome, (header, rows) in tabs.items():
            print(f"  {nome}: {len(rows)} linhas")
        return

    gc = sync.get_sheets_client()
    sh = gc.open_by_key(sync.SPREADSHEET_ID)
    for nome, (header, rows) in tabs.items():
        sync.write_overwrite(sh, nome, header, rows)
    svc = sync.build_sheets_service()
    for base, linhas in bases:
        base.publicar(sh, svc, sync.SPREADSHEET_ID, linhas)
    painel.publicar(sh, svc, sync.SPREADSHEET_ID)
    dp.gravar_resumo(sh, svc, sync.SPREADSHEET_ID, resumo_kpi, now)
    print("AprovAI: aba e raws atualizados.")


if __name__ == "__main__":
    main()
