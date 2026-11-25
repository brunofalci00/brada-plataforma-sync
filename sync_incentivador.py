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
    ("reuniao", "Tiveram reuniao"), ("ganho", "Ganho")])
# A Automatize cria o card no HubSpot sozinha quando o lead abre a agenda: "card" nao e conversao,
# entao agenda e card viram uma etapa so (a base guarda as duas colunas para conferencia).
ETAPA_AGENDA = "Abriram a agenda"
FUNIL_VIVO = [("Disparados", "disparados"), ("Entregues", "entregues"), ("Abriram", "abriram"),
              ("Clicaram ou responderam", "engajaram"), (ETAPA_AGENDA, "agenda"),
              ("Tiveram reuniao", "reuniao"), ("Ganho", "ganho")]
CANAIS = ["E-mail", "WhatsApp", "E-mail + WhatsApp"]

CONSULTA_CARDS = "select lead_id, deal_id from dados.hubspot_cards where deal_id is not null"

HEADER_RAW = ["lead_id_hash", "primeiro_disparo", "canais", "engajou", "foi_agenda", "card_hubspot",
              "reuniao_ou_alem", "estagio_atual", "bounce"]


def completar(leads, cards_por_lead, estagios, passou=None):
    """Marca card/reuniao pelo HubSpot e torna as etapas cumulativas.

    Reuniao = o card PASSOU por reuniao ou alem em algum momento (historico do dealstage): o card
    que teve reuniao e depois foi perdido nao some do funil."""
    passou = passou or {}
    for lead_id, l in leads.items():
        deal = cards_por_lead.get(lead_id)
        l["card"] |= deal is not None
        l["estagio"] = estagios.get(deal) if deal else None
        l["reuniao"] = l["estagio"] in hub.BEYOND_REUNIAO or bool(deal and passou.get(deal))
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
        (ETAPA_AGENDA, sum(l["agenda"] for l in v)),
        ("Tiveram reuniao", sum(l["reuniao"] for l in v)),
    ]


DIAS_PARADO = 7  # card em aberto ha mais que isso no mesmo estagio conta como parado
SEM_DONO = "Sem executivo"
# Revisao do Bruno (25/11): o card nasce quando o lead ABRE a agenda; nao e agendamento.
CAB_EXECUTIVOS = ["Executivo", "Cards", "Sem reuniao nem perda", "Parados 7+ dias", "Dias parado (mediana)",
                  "Tiveram reuniao", "Ganho", "Perdido", "% virou reuniao"]


def _data(iso):
    return datetime.date.fromisoformat(iso[:10]) if iso else None


def por_executivo(cards, nomes, hoje):
    """Uma linha por dono do card: quanto recebeu, quanto esta parado e quanto avancou."""
    grupos = {}
    for c in cards.values():
        dono = nomes.get(c["dono"], f"Dono {c['dono']}") if c["dono"] else SEM_DONO
        grupos.setdefault(dono, []).append(c)
    linhas = []
    for dono, cs in grupos.items():
        reuniao = sum(c["estagio"] in hub.BEYOND_REUNIAO or bool(c.get("passou_reuniao")) for c in cs)
        perdido = sum(c["estagio"] == "closedlost" for c in cs)
        abertos = [c for c in cs if c["estagio"] not in hub.BEYOND_REUNIAO and c["estagio"] != "closedlost"]
        dias = sorted((hoje - _data(c["entrou_estagio"])).days for c in abertos if c["entrou_estagio"])
        mediana = dias[len(dias) // 2] if dias else ""
        linhas.append([dono, len(cs), len(abertos), sum(d >= DIAS_PARADO for d in dias), mediana, reuniao,
                       sum(c["estagio"] in hub.GANHO_OU_ALEM for c in cs), perdido, reuniao / len(cs)])
    return sorted(linhas, key=lambda r: (-r[1], r[0]))


def pipeline_hubspot(hs):
    rows = hs["rows"]
    return [["Agendados pela Brenda", hs["total_automatize"], sum(1 for r in rows if r[2] == "Automatize")],
            ["Trazidos pelos executivos", hs["total_executivo"], sum(1 for r in rows if r[2] == "Executivo Brada")]]


def montar_painel(leads, hs, agora, executivos=(), envios=None, sem_contato=0):
    """Filtros no topo (periodo A/B, canal, fonte), funil do disparo ao ganho e a foto do pipeline."""
    linhas = linhas_base(leads)
    fontes = BASE.opcoes(linhas, "fonte")
    canais = [c for c in CANAIS if c in BASE.opcoes(linhas, "canal")] or CANAIS[:1]
    ctl = fl.controles(ABA, agora.date(), [fl.campo_lista("canal", "Canal", canais, todos=fl.TODOS, span=2),
                                           fl.campo_lista("fonte", "Fonte do lead", fontes, span=2)])
    flt = fl.Filtros(ctl, BASE)
    p = dp.Painel(ABA, "Incentivador · do disparo da Brenda ao patrocinio fechado",
                  f"Atualizado {agora.strftime('%d/%m/%Y %H:%M')} · banco do SDR Brada + HubSpot ao vivo · "
                  "leads contados na data do 1o disparo; mude as células claras abaixo"
                  + (f" · {sem_contato} card(s) sem contato ligado no banco da Automatize: contam nos cards e reunioes"
                     " da Brenda, mas nao no funil por disparo" if sem_contato else ""))
    p.linha(ctl)

    def ab(etapa):
        return flt.soma_p(etapa, "A"), flt.soma_p(etapa, "B")

    p.linha(fl.CartaoVivo("Leads disparados", *ab("disparados")),
            fl.CartaoVivo("Clicaram ou responderam", *ab("engajaram")),
            fl.CartaoVivo("Abriram a agenda", *ab("agenda")),
            fl.CartaoVivo("Viraram card no HubSpot", *ab("card")),
            fl.CartaoVivo("Reunioes (contatos deste periodo)", *ab("reuniao")))
    d, e, ag, r = (flt.soma_p(k) for k in ("disparados", "engajaram", "agenda", "reuniao"))
    p.linha(fl.FunilVivo("Do disparo ao ganho (periodo A)", flt, FUNIL_VIVO, altura=360,
                         subtitulo="% sobre os contatados · abrir a agenda nao e reuniao marcada: so conta a reuniao que aconteceu"),
            fl.RoscaViva("Onde cada lead do periodo A parou", [
                ("Sem resposta", F(f"{d}-{e}")), ("Engajou sem agenda", F(f"{e}-{ag}")),
                ("Agenda sem reuniao", F(f"{ag}-{r}")),
                ("Tiveram reuniao", F(r))], altura=360))
    if executivos:
        cards = sum(r[1] for r in executivos)
        sem_dono = next((r[1] for r in executivos if r[0] == SEM_DONO), 0)
        p.linha(dp.Cartao("Cards da Brenda no HubSpot (desde o inicio)", cards),
                dp.Cartao("Cards sem executivo dono (hoje)", sem_dono),
                dp.Cartao(f"Cards no mesmo estagio ha {DIAS_PARADO}+ dias (hoje)", sum(r[3] for r in executivos)),
                dp.Cartao("Reunioes da Brenda (desde o inicio)", sum(r[5] for r in executivos)))
        # O card nasce quando o lead ABRE a agenda (robo da Automatize), nao quando marca a reuniao.
        p.linha(dp.Tabela(f"Cards da Brenda por executivo (desde o inicio) · parado = {DIAS_PARADO}+ dias no mesmo estagio",
                          CAB_EXECUTIVOS,
                          executivos, formatos=[None] + [dp.FMT_INT] * 7 + [dp.FMT_PCT], gradiente=[3],
                          spans=[2, 1, 2, 1, 2, 1, 1, 1, 1]))
    p.linha(fl.comparacao_ab("Periodo A contra periodo B", flt, FUNIL_VIVO, maturacao=fl.MATURACAO_DIAS))
    p.linha(fl.evolucao("Leads disparados no periodo A", flt, "disparados", "canal", canais,
                        subtitulo="Por dia ou semana (filtro 'Agrupar por'), empilhado por canal"))
    recentes = agora.date() - datetime.timedelta(days=2 * fl.JANELA_PADRAO - 1)
    p.linha(fl.por_dimensao("Por fonte do lead no periodo A", flt, "fonte",
                            BASE.opcoes(linhas, "fonte", desde=recentes) or fontes[:1], [
        ("Disparados", "soma", "disparados"), ("Engajamento", "taxa", "engajaram"),
        ("Abriram a agenda", "soma", "agenda"), ("Reunioes", "soma", "reuniao"),
        ("Disparo ate reuniao", "taxa", "reuniao")], gradiente=[1, 5], cabecalho_dim="Fonte"))
    p.linha(dp.Serie("Pipeline Incentivador no HubSpot (foto de hoje, sem filtro)",
                     ["Quem trabalha", "Negocios no pipeline", "Tiveram reuniao"], pipeline_hubspot(hs), tipo="BAR",
                     subtitulo="Todo o pipeline, nao so os leads da Brenda"))
    etapas = funil(leads)
    resumo = {"incentivador_disparados": etapas[0][1], "incentivador_engajaram": etapas[1][1],
              "incentivador_cards": etapas[2][1],
              "incentivador_reuniao": etapas[-1][1], "incentivador_pipeline_reuniao": len(hs["rows"]),
              # mesmo numero do cartao "Cards que chegaram a reuniao" (por card, inclui lead fora da base de envios)
              "incentivador_cards_reuniao": sum(r[5] for r in executivos) if executivos else etapas[-1][1],
              "incentivador_cards_parados": sum(r[3] for r in executivos) if executivos else 0,
              "incentivador_cards_sem_dono": next((r[1] for r in executivos if r[0] == SEM_DONO), 0) if executivos else 0}
    return p, resumo, linhas


def montar_raw(leads):
    return [[sync.hash_id(lid), l["dia"].isoformat() if l["dia"] else "", "+".join(sorted(l["canais"])),
             "sim" if l["engajou"] else "nao", "sim" if l["agenda"] else "nao", "sim" if l["card"] else "nao",
             "sim" if l["reuniao"] else "nao", hub.STAGE_LABELS.get(l["estagio"], l["estagio"] or ""),
             "sim" if l["bounce"] else "nao"] for lid, l in leads.items()]


def separar_cards(pares):
    """({lead_id: deal_id} dos cards ligados a um contato da Brenda, [deal_id dos cards sem contato]).

    Decisao do Bruno (25/11): todo card da tabela da Automatize veio da Brenda, com ou sem contato ligado, e card
    em "Reuniao agendada" conta como reuniao feita (a Automatize nao tem como saber se aconteceu). O card sem
    contato entra na tabela por executivo e nas reunioes da Brenda; so nao entra no funil por disparo, porque
    nao ha envio que o explique."""
    ligados = {str(lead): deal for lead, deal in pares if lead is not None}
    return ligados, [deal for lead, deal in pares if lead is None]


def coletar():
    conn = sdra.conectar()
    try:
        envios = sdra.envios(conn, sdra.INCENTIVADOR)
        cur = conn.cursor()
        cur.execute(CONSULTA_CARDS)
        pares = cur.fetchall()
    finally:
        conn.close()
    cards_por_lead, sem_contato = separar_cards(pares)
    if sem_contato:
        print(f"[AVISO] {len(sem_contato)} card(s) sem contato ligado no banco da Automatize: contam como card e "
              "reuniao da Brenda, fora do funil por disparo")
    cards = hub.cards_ao_vivo(list(cards_por_lead.values()) + sem_contato)
    return envios, cards_por_lead, cards, len(sem_contato)


def main():
    ap = argparse.ArgumentParser(description="Canal Incentivador -> Sheets")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    if not hub.HUBSPOT_TOKEN:
        raise SystemExit("HUBSPOT_TOKEN nao configurado.")

    agora = datetime.datetime.now(sync.BRT)
    print("Banco do SDR Brada (so incentivador) + estagios ao vivo...")
    envios, cards_por_lead, cards, sem_contato = coletar()
    estagios = {d: c["estagio"] for d, c in cards.items()}
    passou = {d: c.get("passou_reuniao", False) for d, c in cards.items()}
    leads = completar(sdra.por_lead(envios), cards_por_lead, estagios, passou)
    executivos = por_executivo(cards, hub.nomes_donos(), agora.date())
    for r in executivos:
        print("  ", r)
    print("  funil:", funil(leads))
    print("Pipeline Incentivador no HubSpot...")
    hs = hub.coletar()
    print(f"  {hs['total_automatize']} Automatize | {hs['total_executivo']} Executivo | {len(hs['rows'])} reuniao+")

    tabs = {"raw_disparo_incentivador": (HEADER_RAW, montar_raw(leads)),
            "raw_hubspot_incentivador": (hub.HEADER_RAW, hs["rows"])}
    sync.pii_guard(tabs)
    print("pii_guard: OK (zero hits)")
    painel, resumo, linhas = montar_painel(leads, hs, agora, executivos, envios, sem_contato)

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
