"""
HubSpot, pipeline Incentivador (`default`): leitura para a aba `Incentivador`.

- `coletar()`: total de cards por `trabalhado_por` (Automatize x Executivo Brada) e
  os deals que estao em "[EV] Reuniao Agendada" ou alem, com o caminho pelo qual
  chegaram la (automatico pela integracao, manual, ou ja criado na fase).
- `estagios_ao_vivo()`: estagio ATUAL de uma lista de deals (o banco da Automatize
  guarda so o estagio de quando o card foi criado).

Custo de API: o historico de dealstage (1 GET por deal) so e lido para quem ja esta
em reuniao ou alem, nao para o pipeline inteiro.

PII: dealname e nome de empresa/projeto (negocio B2B), nao de pessoa fisica.
deal_id e owner_id ficam na isencao de digitos de sync.py (COLUNAS_ISENTAS_DIGITOS).
"""

import json
import os
import time
import urllib.error
import urllib.request

import sync

HUBSPOT_TOKEN = os.environ.get("HUBSPOT_TOKEN", "")
sync._load_local_env(os.path.expanduser(r"~/.brada-secrets/hubspot.env"))
if not HUBSPOT_TOKEN:
    HUBSPOT_TOKEN = os.environ.get("HUBSPOT_TOKEN", "")

PIPELINE_INCENTIVADOR = "default"
REUNIAO_STAGE = "1246562476"
BEYOND_REUNIAO = [
    "1246562476", "1246562478", "1246602642", "1246602643", "1247329454",
    "decisionmakerboughtin", "1253324968", "contractsent", "1247329455", "1247329456",
]
STAGE_LABELS = {
    "appointmentscheduled": "[SDR] Inbound",
    "1246562475": "[SDR] Outbound",
    "1246562477": "[SDR] Qualificacao",
    "1291711757": "[EV] Prospects",
    "1246562476": "[EV] Reuniao Agendada",
    "1246562478": "[EV] Diagnostico",
    "1246602642": "[EV+Match] Politica Patrocinio",
    "1246602643": "[Match] Projetos",
    "1247329454": "[EV] Contrato Patrocinador",
    "decisionmakerboughtin": "[EV] Financeiro",
    "1253324968": "Ganho Incentivador",
    "contractsent": "[Pos Venda] Pre Projeto",
    "1247329455": "[Pos Venda] Projeto em Andamento",
    "1247329456": "[Pos Venda] Pos Projeto",
    "closedlost": "Perdido",
}

HEADER_RAW = [
    "deal_id", "dealname", "trabalhado_por", "origem_lead", "owner_id",
    "estagio_atual", "como_chegou_em_reuniao", "source_id", "data_criacao",
]

HS_MAX_TENTATIVAS = 6


def hs_headers():
    return {"Authorization": f"Bearer {HUBSPOT_TOKEN}", "Content-Type": "application/json"}


def hs_request(req):
    """Abre a requisicao repetindo em 429 (limite de taxa do HubSpot) com espera crescente."""
    for tentativa in range(HS_MAX_TENTATIVAS):
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                return json.loads(resp.read())
        except urllib.error.HTTPError as e:
            if e.code != 429 or tentativa == HS_MAX_TENTATIVAS - 1:
                raise
            espera = float(e.headers.get("Retry-After") or 2 ** tentativa)
            time.sleep(espera)


def hs_post(url, payload):
    req = urllib.request.Request(url, data=json.dumps(payload).encode(), headers=hs_headers(), method="POST")
    return hs_request(req)


def hs_get(url):
    req = urllib.request.Request(url, headers=hs_headers())
    return hs_request(req)


def hs_search_paginado(payload):
    out, after = [], None
    while True:
        if after:
            payload["after"] = after
        data = hs_post("https://api.hubapi.com/crm/v3/objects/deals/search", payload)
        out.extend(data["results"])
        paging = data.get("paging")
        if not paging:
            return out
        after = paging["next"]["after"]


def hs_total(filtros):
    payload = {"filterGroups": [{"filters": filtros}], "limit": 1}
    return hs_post("https://api.hubapi.com/crm/v3/objects/deals/search", payload)["total"]


def coletar():
    total_automatize = hs_total([
        {"propertyName": "pipeline", "operator": "EQ", "value": PIPELINE_INCENTIVADOR},
        {"propertyName": "trabalhado_por", "operator": "EQ", "value": "Automatize"},
    ])
    total_executivo = hs_total([
        {"propertyName": "pipeline", "operator": "EQ", "value": PIPELINE_INCENTIVADOR},
        {"propertyName": "trabalhado_por", "operator": "EQ", "value": "Executivo Brada"},
    ])

    deals_beyond = hs_search_paginado({
        "filterGroups": [{"filters": [
            {"propertyName": "pipeline", "operator": "EQ", "value": PIPELINE_INCENTIVADOR},
            {"propertyName": "dealstage", "operator": "IN", "values": BEYOND_REUNIAO},
        ]}],
        "properties": ["dealname", "dealstage", "createdate", "trabalhado_por",
                       "hubspot_owner_id", "origem_lead"],
        "limit": 100,
    })

    rows = []
    for d in deals_beyond:
        p = d["properties"]
        hist = hs_get(
            f"https://api.hubapi.com/crm/v3/objects/deals/{d['id']}?propertiesWithHistory=dealstage"
        )
        stage_hist = hist.get("propertiesWithHistory", {}).get("dealstage", [])
        source_type, source_id = None, None
        for h in stage_hist:
            if h["value"] == REUNIAO_STAGE:
                source_type, source_id = h.get("sourceType"), h.get("sourceId")
                break
        rows.append([
            d["id"], p.get("dealname"), p.get("trabalhado_por") or "(vazio)",
            p.get("origem_lead") or "", p.get("hubspot_owner_id") or "",
            STAGE_LABELS.get(p.get("dealstage"), p.get("dealstage")),
            source_type or "criado_direto_na_fase_ou_posterior", source_id or "",
            p.get("createdate") or "",
        ])

    return {
        "total_automatize": total_automatize,
        "total_executivo": total_executivo,
        "rows": rows,
    }


def estagios_ao_vivo(deal_ids):
    """Estagio ATUAL no HubSpot pra cada deal_id, em lotes de 100 (limite do batch/read)."""
    if not HUBSPOT_TOKEN or not deal_ids:
        return {}
    estagios = {}
    for i in range(0, len(deal_ids), 100):
        lote = deal_ids[i:i + 100]
        resp = hs_post("https://api.hubapi.com/crm/v3/objects/deals/batch/read", {
            "inputs": [{"id": d} for d in lote], "properties": ["dealstage"],
        })
        for r in resp.get("results", []):
            estagios[r["id"]] = r["properties"].get("dealstage")
    return estagios
