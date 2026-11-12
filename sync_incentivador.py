"""
Canal Incentivador -> aba `Incentivador` (junta as antigas `SDR Incentivador` e
`Disparo Incentivador`).

Funil por lead: disparo da Brenda -> clicou ou respondeu -> foi a agenda -> card
no HubSpot -> reuniao ou alem (estagio ATUAL do card, ao vivo no HubSpot). Cada
etapa conta quem chegou PELO MENOS ate ela, entao o funil nunca sobe.

PII: nome/e-mail/telefone nunca saem do banco; o raw publica lead_id em hash.
Rodar local: python sync_incentivador.py --dry-run
"""

import argparse
import datetime

import sync
import dashboard_painel as dp
import hubspot_incentivador as hub
import sdr_automatize as sdra

ABA = "Incentivador"
SEMANAS = 10

CONSULTA_CARDS = "select lead_id, deal_id from dados.hubspot_cards where deal_id is not null"

HEADER_RAW = ["lead_id_hash", "primeiro_disparo", "canais", "engajou", "foi_agenda", "card_hubspot",
              "reuniao_ou_alem", "estagio_atual", "bounce"]


def completar(leads, cards_por_lead, estagios):
    """Marca card/reuniao pelo HubSpot e torna as etapas cumulativas."""
    for lead_id, l in leads.items():
        deal = cards_por_lead.get(lead_id)
        l["card"] |= deal is not None
        l["estagio"] = estagios.get(deal) if deal else None
        l["reuniao"] = l["estagio"] in hub.BEYOND_REUNIAO
        l["agenda"] |= l["card"]
        l["engajou"] = l["clicou"] or l["respondeu"] or l["agenda"]
    return leads


def funil(leads):
    v = list(leads.values())
    return [
        ("Disparados", len(v)),
        ("Clicaram ou responderam", sum(l["engajou"] for l in v)),
        ("Foram a agenda", sum(l["agenda"] for l in v)),
        ("Card no HubSpot", sum(l["card"] for l in v)),
        ("Reuniao ou alem", sum(l["reuniao"] for l in v)),
    ]


def onde_parou(leads):
    """Cada lead na etapa mais funda que alcancou (as fatias somam o total disparado)."""
    v = list(leads.values())
    return [
        ("Sem resposta", sum(not l["engajou"] for l in v)),
        ("Engajou, nao foi a agenda", sum(l["engajou"] and not l["agenda"] for l in v)),
        ("Foi a agenda, sem card", sum(l["agenda"] and not l["card"] for l in v)),
        ("Card, sem reuniao", sum(l["card"] and not l["reuniao"] for l in v)),
        ("Reuniao ou alem", sum(l["reuniao"] for l in v)),
    ]


def pipeline_hubspot(hs):
    rows = hs["rows"]
    return [["Automatize", hs["total_automatize"], sum(1 for r in rows if r[2] == "Automatize")],
            ["Executivo Brada", hs["total_executivo"], sum(1 for r in rows if r[2] == "Executivo Brada")]]


def montar_painel(leads, envios, hs, agora):
    hoje = agora.date()
    etapas = funil(leads)
    topo, cards, reuniao = etapas[0][1], etapas[3][1], etapas[-1][1]
    p = dp.Painel(ABA, "Incentivador · da Brenda a reuniao",
                  f"Atualizado {agora.strftime('%d/%m/%Y %H:%M')} · banco do SDR Brada + HubSpot ao vivo")
    p.linha(dp.Cartao("Leads disparados", topo),
            dp.Cartao("Cards no HubSpot", cards),
            dp.Cartao("Reuniao ou alem (agora)", reuniao),
            dp.Cartao("Disparo ate reuniao", dp.taxa(reuniao, topo), dp.FMT_PCT))
    p.linha(dp.Funil("Funil do canal", etapas, altura=330),
            dp.Rosca("Onde cada lead parou", onde_parou(leads), altura=330))
    p.linha(dp.Serie(f"Disparos por semana (ultimas {SEMANAS})", ["Semana", "E-mail", "WhatsApp"],
                     sdra.por_semana(envios, hoje, SEMANAS), empilhado=True),
            dp.Serie("Pipeline Incentivador no HubSpot", ["Quem trabalha", "Cards no pipeline", "Reuniao ou alem"],
                     pipeline_hubspot(hs), tipo="BAR"))
    p.linha(dp.Tabela("Conversao por semana de disparo",
                      ["Semana", "Disparados", "Engajaram", "Agenda", "Cards", "Reuniao+", "Disparo > card"],
                      [l[:-1] + [dp.taxa(l[5], l[1])] for l in
                       sdra.coorte(leads, hoje, ["engajou", "agenda", "card", "reuniao"], SEMANAS)],
                      formatos=[None] + [dp.FMT_INT] * 5 + [dp.FMT_PCT], gradiente=[6]))
    resumo = {"incentivador_disparados": topo, "incentivador_cards": cards, "incentivador_reuniao": reuniao,
              "incentivador_pipeline_reuniao": len(hs["rows"])}
    return p, resumo


def montar_raw(leads):
    return [[sync.hash_id(lid), l["dia"].isoformat() if l["dia"] else "", "+".join(sorted(l["canais"])),
             "sim" if l["engajou"] else "nao", "sim" if l["agenda"] else "nao", "sim" if l["card"] else "nao",
             "sim" if l["reuniao"] else "nao", hub.STAGE_LABELS.get(l["estagio"], l["estagio"] or ""),
             "sim" if l["bounce"] else "nao"] for lid, l in leads.items()]


def coletar():
    conn = sdra.conectar()
    try:
        envios = sdra.envios(conn, sdra.INCENTIVADOR)
        cur = conn.cursor()
        cur.execute(CONSULTA_CARDS)
        cards_por_lead = {str(lead): deal for lead, deal in cur.fetchall()}
    finally:
        conn.close()
    estagios = hub.estagios_ao_vivo(list(cards_por_lead.values()))
    return envios, cards_por_lead, estagios


def main():
    ap = argparse.ArgumentParser(description="Canal Incentivador -> Sheets")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    if not hub.HUBSPOT_TOKEN:
        raise SystemExit("HUBSPOT_TOKEN nao configurado.")

    agora = datetime.datetime.now(sync.BRT)
    print("Banco do SDR Brada (so incentivador) + estagios ao vivo...")
    envios, cards_por_lead, estagios = coletar()
    leads = completar(sdra.por_lead(envios), cards_por_lead, estagios)
    print("  funil:", funil(leads))
    print("Pipeline Incentivador no HubSpot...")
    hs = hub.coletar()
    print(f"  {hs['total_automatize']} Automatize | {hs['total_executivo']} Executivo | {len(hs['rows'])} reuniao+")

    tabs = {"raw_disparo_incentivador": (HEADER_RAW, montar_raw(leads)),
            "raw_hubspot_incentivador": (hub.HEADER_RAW, hs["rows"])}
    sync.pii_guard(tabs)
    print("pii_guard: OK (zero hits)")
    painel, resumo = montar_painel(leads, envios, hs, agora)

    if args.dry_run:
        print("--dry-run: nada escrito.", resumo)
        return

    gc = sync.get_sheets_client()
    sh = gc.open_by_key(sync.SPREADSHEET_ID)
    for nome, (header, rows) in tabs.items():
        sync.write_overwrite(sh, nome, header, rows)
    svc = sync.build_sheets_service()
    painel.publicar(sh, svc, sync.SPREADSHEET_ID)
    dp.gravar_resumo(sh, svc, sync.SPREADSHEET_ID, resumo, agora)
    print("Incentivador: aba publicada.")


if __name__ == "__main__":
    main()
