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
import painel_filtros as fl
import sdr_automatize as sdra
from painel_filtros import F

ABA = "Incentivador"
SEMANAS = 10
BASE = fl.Base("base_incentivador", [("canal", "Canal", fl.TODOS), ("fonte", "Fonte", fl.TODAS)], [
    ("disparados", "Disparados"), ("entregues", "Entregues"), ("abriram", "Abriram"),
    ("engajaram", "Clicaram ou responderam"), ("agenda", "Foram a agenda"), ("card", "Card no HubSpot"),
    ("reuniao", "Reuniao ou alem"), ("ganho", "Ganho")])
FUNIL_VIVO = [("Disparados", "disparados"), ("Entregues", "entregues"), ("Abriram", "abriram"),
              ("Clicaram ou responderam", "engajaram"), ("Foram a agenda", "agenda"),
              ("Card no HubSpot", "card"), ("Reuniao ou alem", "reuniao"), ("Ganho", "ganho")]
CANAIS = ["E-mail", "WhatsApp", "E-mail + WhatsApp"]

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
        l["ganho"] = l["estagio"] in hub.GANHO_OU_ALEM
        l["agenda"] |= l["card"]
        l["engajou"] = l["clicou"] or l["respondeu"] or l["agenda"]
        l["abriu"] = l["abriu"] or l["engajou"]
    return leads


def linhas_base(leads):
    """Um registro por lead na data do 1o disparo; etapas cumulativas (o funil nunca sobe)."""
    return BASE.agregar(
        (l["dia"], {"canal": sdra.canal_do_lead(l["canais"]), "fonte": l.get("fonte")},
         {"disparados": 1, "entregues": l["abriu"] or not l["bounce"], "abriram": l["abriu"],
          "engajaram": l["engajou"], "agenda": l["agenda"], "card": l["card"], "reuniao": l["reuniao"],
          "ganho": l.get("ganho", False)})
        for l in leads.values())


def funil(leads):
    v = list(leads.values())
    return [
        ("Disparados", len(v)),
        ("Clicaram ou responderam", sum(l["engajou"] for l in v)),
        ("Foram a agenda", sum(l["agenda"] for l in v)),
        ("Card no HubSpot", sum(l["card"] for l in v)),
        ("Reuniao ou alem", sum(l["reuniao"] for l in v)),
    ]


def pipeline_hubspot(hs):
    rows = hs["rows"]
    return [["Automatize", hs["total_automatize"], sum(1 for r in rows if r[2] == "Automatize")],
            ["Executivo Brada", hs["total_executivo"], sum(1 for r in rows if r[2] == "Executivo Brada")]]


def montar_painel(leads, hs, agora):
    """Filtros no topo (periodo A/B, canal, fonte), funil do disparo ao ganho e a foto do pipeline."""
    linhas = linhas_base(leads)
    fontes = BASE.opcoes(linhas, "fonte")
    canais = [c for c in CANAIS if c in BASE.opcoes(linhas, "canal")] or CANAIS[:1]
    ctl = fl.controles(ABA, agora.date(), [fl.campo_lista("canal", "Canal", canais, todos=fl.TODOS, span=2),
                                           fl.campo_lista("fonte", "Fonte do lead", fontes, span=2)])
    flt = fl.Filtros(ctl, BASE)
    p = dp.Painel(ABA, "Incentivador · do disparo da Brenda ao patrocinio fechado",
                  f"Atualizado {agora.strftime('%d/%m/%Y %H:%M')} · banco do SDR Brada + HubSpot ao vivo · "
                  "leads contados na data do 1o disparo; mude as células claras abaixo")
    p.linha(ctl)

    def ab(etapa):
        return flt.soma_p(etapa, "A"), flt.soma_p(etapa, "B")

    p.linha(fl.CartaoVivo("Leads disparados", *ab("disparados")),
            fl.CartaoVivo("Cards no HubSpot", *ab("card")),
            fl.CartaoVivo("Reuniao ou alem", *ab("reuniao")),
            fl.CartaoVivo("Disparo ate reuniao", flt.taxa("reuniao", "A"), flt.taxa("reuniao", "B"), dp.FMT_PCT))
    d, e, ag, c, r = (flt.soma_p(k) for k in ("disparados", "engajaram", "agenda", "card", "reuniao"))
    p.linha(fl.FunilVivo("Do disparo ao ganho (periodo A)", flt, FUNIL_VIVO, altura=360,
                         subtitulo="% sobre os disparados · reuniao e ganho = estagio atual do card no HubSpot"),
            fl.RoscaViva("Onde cada lead do periodo A parou", [
                ("Sem resposta", F(f"{d}-{e}")), ("Engajou sem agenda", F(f"{e}-{ag}")),
                ("Agenda sem card", F(f"{ag}-{c}")), ("Card sem reuniao", F(f"{c}-{r}")),
                ("Reuniao ou alem", F(r))], altura=360))
    p.linha(fl.comparacao_ab("Periodo A contra periodo B", flt, FUNIL_VIVO))
    p.linha(fl.evolucao("Leads disparados no periodo A", flt, "disparados", "canal", canais,
                        subtitulo="Por dia ou semana (filtro 'Agrupar por'), empilhado por canal"))
    recentes = agora.date() - datetime.timedelta(days=2 * fl.JANELA_PADRAO - 1)
    p.linha(fl.por_dimensao("Por fonte do lead no periodo A", flt, "fonte",
                            BASE.opcoes(linhas, "fonte", desde=recentes) or fontes[:1], [
        ("Disparados", "soma", "disparados"), ("Engajamento", "taxa", "engajaram"),
        ("Cards", "soma", "card"), ("Reunioes", "soma", "reuniao"),
        ("Disparo ate reuniao", "taxa", "reuniao")], gradiente=[1, 5], cabecalho_dim="Fonte"))
    p.linha(dp.Serie("Pipeline Incentivador no HubSpot (foto de hoje, sem filtro)",
                     ["Quem trabalha", "Cards no pipeline", "Reuniao ou alem"], pipeline_hubspot(hs), tipo="BAR",
                     subtitulo="Todo o pipeline, nao so os leads da Brenda"))
    etapas = funil(leads)
    resumo = {"incentivador_disparados": etapas[0][1], "incentivador_cards": etapas[3][1],
              "incentivador_reuniao": etapas[-1][1], "incentivador_pipeline_reuniao": len(hs["rows"])}
    return p, resumo, linhas


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
    painel, resumo, linhas = montar_painel(leads, hs, agora)

    if args.dry_run:
        print("--dry-run: nada escrito.", resumo)
        return

    gc = sync.get_sheets_client()
    sh = gc.open_by_key(sync.SPREADSHEET_ID)
    for nome, (header, rows) in tabs.items():
        sync.write_overwrite(sh, nome, header, rows)
    svc = sync.build_sheets_service()
    BASE.publicar(sh, svc, sync.SPREADSHEET_ID, linhas)
    painel.publicar(sh, svc, sync.SPREADSHEET_ID)
    dp.gravar_resumo(sh, svc, sync.SPREADSHEET_ID, resumo, agora)
    print("Incentivador: aba publicada.")


if __name__ == "__main__":
    main()
