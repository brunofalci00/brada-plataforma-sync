import sync_disparo_incentivador as di


def _envio(lead_id="11111111-1111-1111-1111-111111111111", dia=None, canal="email",
           fonte_slug="salic", aberto=False, clicou=False, respondeu=False,
           visitou_agenda=False, clicou_agendar=False, card_hubspot=False, bounce=False):
    import datetime
    return (1, lead_id, dia or datetime.date(2026, 11, 1), canal, fonte_slug,
            aberto, clicou, respondeu, visitou_agenda, clicou_agendar, card_hubspot, bounce)


def test_montar_raw_hasheia_lead_id_e_converte_booleanos():
    linhas = di.montar_raw([_envio(aberto=True, clicou=True)])
    assert len(linhas) == 1
    linha = linhas[0]
    assert linha[0] != "11111111-1111-1111-1111-111111111111"
    assert len(linha[0]) == 12
    assert linha[1] == "2026-11-01"
    assert linha[4] == "sim" and linha[5] == "sim" and linha[6] == "nao"


def test_resumo_envios_conta_cada_estagio():
    envios = [
        _envio(aberto=True, clicou=True, respondeu=True, card_hubspot=True),
        _envio(aberto=True),
        _envio(),
    ]
    r = di.resumo_envios(envios)
    assert r == {"total": 3, "abriram": 2, "clicaram": 1, "responderam": 1,
                 "visitaram_agenda": 0, "clicaram_agendar": 0, "card_hubspot": 1, "bounce": 0}


def test_resumo_cards_conta_so_quem_chegou_em_reuniao_ou_alem():
    deal_ids = ["1", "2", "3", "4"]
    estagios = {"1": "appointmentscheduled", "2": "1246562476", "3": "contractsent"}
    r = di.resumo_cards(deal_ids, estagios)
    assert r == {"total": 4, "encontrados_no_hubspot": 3, "em_reuniao_ou_alem": 2}


def test_resumo_cards_sem_deals_nao_quebra_divisao():
    assert di.resumo_cards([], {}) == {"total": 0, "encontrados_no_hubspot": 0, "em_reuniao_ou_alem": 0}


def test_montar_secoes_sem_leads_nem_divide_por_zero():
    resumo_env = di.resumo_envios([])
    resumo_crd = di.resumo_cards([], {})
    secoes = di.montar_secoes({}, resumo_env, resumo_crd)
    assert [len(s.metricas) for s in secoes] == [4] * 4
    assert secoes[1].metricas[1][0] == 0  # taxa de abertura sem divisao por zero
