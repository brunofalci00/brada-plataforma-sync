"""
Sync SDR Incentivador -> Google Sheets (aba `SDR Incentivador` +
raw_hubspot_incentivador na mesma Sheet `Brada_Dashboard_Produtos`).

Responde a pergunta operacional: quantos cards do pipeline Incentivador (HubSpot,
pipeline `default`) foram criados pela Automatize, e quantos avancaram ate
"[EV] Reuniao Agendada" ou alem — separando o caminho automatico (a propria
integracao move o card) do caminho manual (executivo move a mao, ou card ja
nasce la por feira/evento/indicacao).

Custo de API: so busca o historico de dealstage (1 GET por deal) dos deals que
JA estao em Reuniao Agendada ou alem — nao dos ~760 do pipeline inteiro.

PII: dealname e nome de empresa/projeto (negocio B2B), nao de pessoa fisica —
nao entra no escopo do pii_guard. deal_id e owner_id ficam na isencao de digitos
de sync.py (ver COLUNAS_ISENTAS_DIGITOS).

Rodar local: python sync_sdr_incentivador.py --dry-run
"""

import argparse
import datetime
import json
import os
import time
import urllib.error
import urllib.request

import sync
import dashboard_kpi_blocks as kpi

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


def hs_headers():
    return {"Authorization": f"Bearer {HUBSPOT_TOKEN}", "Content-Type": "application/json"}


HS_MAX_TENTATIVAS = 6


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


def montar_secoes(dados):
    total_automatize = dados["total_automatize"]
    total_executivo = dados["total_executivo"]
    total_pipeline = total_automatize + total_executivo
    pct_automatize = (total_automatize / total_pipeline) if total_pipeline else 0

    rows = dados["rows"]
    total_beyond = len(rows)
    beyond_automatize = sum(1 for r in rows if r[2] == "Automatize")
    beyond_executivo = sum(1 for r in rows if r[2] == "Executivo Brada")
    taxa_automatize = (beyond_automatize / total_automatize) if total_automatize else 0

    sec1 = kpi.Secao(
        "Volume por canal (todo o historico)",
        [
            (total_automatize, "Cards criados pela Automatize", None),
            (total_executivo, "Cards trabalhados por Executivo Brada", None),
            (pct_automatize, "% do pipeline que e Automatize", {"type": "PERCENT", "pattern": "0%"}),
            (total_pipeline, "Total no pipeline Incentivador", None),
        ],
        nota="Cards = deals no pipeline Incentivador (default), filtrados por trabalhado_por.",
    )

    sec2 = kpi.Secao(
        "Quem chega em Reuniao Agendada ou alem",
        [
            (total_beyond, "Chegaram em reuniao ou alem (total)", None),
            (beyond_automatize, "Vindos da Automatize", None),
            (beyond_executivo, "Vindos de Executivo Brada", None),
            (taxa_automatize, "Taxa de conversao Automatize (inbound -> reuniao)",
             {"type": "PERCENT", "pattern": "0.0%"}),
        ],
        nota=(
            f"Dos {total_beyond} que chegaram em reuniao+, {beyond_executivo} sao Executivo Brada e "
            f"{beyond_automatize} e Automatize. Detalhe deal a deal, com a origem de cada transicao "
            "(automatica via integracao, manual no CRM, ou importacao em lote), em raw_hubspot_incentivador."
        ),
        nota_aviso=True,
    )
    return [sec1, sec2]


def main():
    ap = argparse.ArgumentParser(description="Sync SDR Incentivador -> Sheets")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    if not HUBSPOT_TOKEN:
        raise SystemExit("HUBSPOT_TOKEN nao configurado.")

    now = datetime.datetime.now(sync.BRT)
    print("Puxando HubSpot (pipeline Incentivador)...")
    dados = coletar()
    print(f"  {dados['total_automatize']} Automatize | {dados['total_executivo']} Executivo Brada | "
          f"{len(dados['rows'])} em reuniao+")

    tabs = {"raw_hubspot_incentivador": (HEADER_RAW, dados["rows"])}
    sync.pii_guard(tabs)
    print("pii_guard: OK (zero hits)")

    secoes = montar_secoes(dados)
    subtitulo = f"Foto: {now.strftime('%d/%m/%Y %H:%M')} (Brasilia) - HubSpot, pipeline Incentivador (default), historico completo"

    if args.dry_run:
        print("\n--dry-run: nada escrito na Sheet.")
        print(f"  raw_hubspot_incentivador: {len(dados['rows'])} linhas")
        for secao in secoes:
            print(f"  [{secao.titulo}]", secao.metricas)
        return

    gc = sync.get_sheets_client()
    sh = gc.open_by_key(sync.SPREADSHEET_ID)
    sync.write_overwrite(sh, "raw_hubspot_incentivador", HEADER_RAW, dados["rows"])

    from sync_aprovai import build_sheets_service
    svc = build_sheets_service()
    kpi.publish(sh, svc, sync.SPREADSHEET_ID, "SDR Incentivador",
                "SDR Incentivador — Automatize x Executivos", subtitulo, secoes)
    print("SDR Incentivador: aba e raw atualizadas.")


if __name__ == "__main__":
    main()
