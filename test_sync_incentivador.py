import datetime

import sdr_automatize as sdra
import sync_incentivador as si

D = datetime.date
REUNIAO = "1246562476"


def _leads():
    envios = []
    for lead, marcas in [("a", {}), ("b", {"clicou": True}), ("c", {"agenda": True}),
                         ("d", {"respondeu": True}), ("e", {})]:
        e = {"lead_id": lead, "dia": D(2026, 11, 10), "canal": "email"}
        e.update({m: marcas.get(m, False) for m in sdra.MARCAS})
        envios.append(e)
    # "e" tem card no HubSpot sem nenhuma marca de clique: o card puxa as etapas anteriores
    return si.completar(sdra.por_lead(envios), {"c": "d1", "e": "d2"}, {"d1": REUNIAO, "d2": "closedlost"})


def test_funil_cumulativo_nunca_sobe():
    etapas = [v for _, v in si.funil(_leads())]
    assert etapas == [5, 4, 2, 2, 1]
    assert etapas == sorted(etapas, reverse=True)


def test_onde_parou_reparte_todos_os_leads():
    fatias = dict(si.onde_parou(_leads()))
    assert sum(fatias.values()) == 5
    assert fatias["Sem resposta"] == 1 and fatias["Reuniao ou alem"] == 1 and fatias["Card, sem reuniao"] == 1


def test_pipeline_hubspot_conta_reuniao_por_quem_trabalha():
    hs = {"total_automatize": 123, "total_executivo": 656,
          "rows": [["1", "x", "Automatize"], ["2", "y", "Executivo Brada"], ["3", "z", "Executivo Brada"]]}
    assert si.pipeline_hubspot(hs) == [["Automatize", 123, 1], ["Executivo Brada", 656, 2]]


def test_painel_e_resumo():
    hs = {"total_automatize": 1, "total_executivo": 2, "rows": []}
    leads = _leads()
    envios = [{"lead_id": k, "dia": D(2026, 11, 10), "canal": "email"} for k in leads]
    painel, resumo = si.montar_painel(leads, envios, hs, datetime.datetime(2026, 11, 12, 9))
    assert resumo["incentivador_disparados"] == 5 and resumo["incentivador_cards"] == 2
    assert len(painel.linhas) == 4
