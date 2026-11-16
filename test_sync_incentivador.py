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


def test_base_um_lead_por_linha_cumulativa_com_ganho():
    envios = [{"lead_id": "g", "dia": D(2026, 11, 11), "canal": "email", "fonte": "patrocinadores-brada",
               **{m: False for m in sdra.MARCAS}}]
    leads = dict(_leads(), **si.completar(sdra.por_lead(envios), {"g": "d3"}, {"d3": "1253324968"}))
    linhas = si.linhas_base(leads)
    # 10/11: 5 leads sem fonte; 11/11: 1 lead que fechou patrocinio (card puxa agenda, engajou e abriu)
    assert linhas[0][:3] == [46336, "E-mail", "sem fonte"] and linhas[0][3] == 5
    assert linhas[1] == [46337, "E-mail", "patrocinadores-brada", 1, 1, 1, 1, 1, 1, 1, 1]


def test_pipeline_hubspot_conta_reuniao_por_quem_trabalha():
    hs = {"total_automatize": 123, "total_executivo": 656,
          "rows": [["1", "x", "Automatize"], ["2", "y", "Executivo Brada"], ["3", "z", "Executivo Brada"]]}
    assert si.pipeline_hubspot(hs) == [["Automatize", 123, 1], ["Executivo Brada", 656, 2]]


def test_painel_e_resumo():
    hs = {"total_automatize": 1, "total_executivo": 2, "rows": []}
    leads = _leads()
    painel, resumo, linhas = si.montar_painel(leads, hs, datetime.datetime(2026, 11, 12, 9))
    assert resumo["incentivador_disparados"] == 5 and resumo["incentivador_cards"] == 2
    assert painel.linhas[0][0].__class__.__name__ == "Controles"  # filtros sempre na 1a linha
    titulos = [c.titulo for linha in painel.linhas[1:] for c in linha]
    assert "Do disparo ao ganho (periodo A)" in titulos and "Conversao por semana de disparo" not in titulos
    funil = next(c for linha in painel.linhas[1:] for c in linha if c.titulo == "Do disparo ao ganho (periodo A)")
    assert all(str(v).startswith("=") for linha in funil.bloco()[1:] for v in linha[1:])  # tudo vivo


def test_por_executivo_conta_parados_avanco_e_sem_dono():
    import datetime
    import sync_incentivador as si
    hoje = datetime.date(2026, 11, 16)
    cards = {
        "1": {"estagio": "appointmentscheduled", "dono": "", "criado": "2026-11-01", "entrou_estagio": "2026-11-01T10:00:00Z"},
        "2": {"estagio": "appointmentscheduled", "dono": "9", "criado": "2026-11-14", "entrou_estagio": "2026-11-14T10:00:00Z"},
        "3": {"estagio": "1246562476", "dono": "9", "criado": "2026-11-01", "entrou_estagio": "2026-11-05T10:00:00Z"},
        "4": {"estagio": "closedlost", "dono": "9", "criado": "2026-10-01", "entrou_estagio": "2026-10-02T10:00:00Z"},
    }
    linhas = si.por_executivo(cards, {"9": "Ana"}, hoje)
    assert linhas[0] == ["Ana", 3, 1, 0, 2, 1, 0, 1, 1 / 3]
    assert linhas[1] == [si.SEM_DONO, 1, 1, 1, 15, 0, 0, 0, 0.0]


def test_painel_com_executivos_mostra_tabela_de_cobranca():
    import datetime
    import sync_incentivador as si
    hs = {"total_automatize": 1, "total_executivo": 2, "rows": []}
    execs = [["Ana", 3, 1, 0, 2, 1, 0, 1, 1 / 3], [si.SEM_DONO, 1, 1, 1, 15, 0, 0, 0, 0.0]]
    painel, _, _ = si.montar_painel({}, hs, datetime.datetime(2026, 11, 16, 9), execs)
    titulos = [getattr(c, "titulo", "") for linha in painel.linhas for c in linha]
    assert any(t.startswith("Cobranca por executivo") for t in titulos)
    assert "Sem executivo dono" in titulos
