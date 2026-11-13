import datetime

import painel_plataforma as pp

D = datetime.date


def _lead(**k):
    base = {"abriu": False, "clicou": False, "respondeu": False, "cadastro": False}
    base.update(k)
    return base


def _projeto(owner, status="Disponível", situacao="vigente"):
    return {"owner_hash": owner, "status": status, "expiracao_situacao": situacao, "data_expiracao_cac": "2027-01-01"}


def test_lead_vira_cadastro_projeto_e_apto_pelo_utm_term():
    leads = {"L1": _lead(), "l2": _lead(clicou=True), "l3": _lead(abriu=True)}
    users = [{"utm_term": "l1", "user_hash": "u1", "tem_projeto": "sim"},
             {"utm_term": "L2", "user_hash": "u2", "tem_projeto": "nao"}]
    projetos = [_projeto("u1")]
    pp.completar_aptos(leads, users, projetos)
    assert leads["L1"]["apto"] and leads["L1"]["cadastro"] and leads["L1"]["abriu"]
    assert leads["l2"]["cadastro"] and not leads["l2"]["projeto"]
    etapas = [v for _, v in pp.funil_aptos(leads)]
    assert etapas == [3, 3, 2, 2, 1, 1]
    assert etapas == sorted(etapas, reverse=True)


def test_valor_em_pega_o_ultimo_dia_com_dado_ate_a_data():
    serie = [["2026-10-10", 380, 60], ["2026-10-12", "", 61], ["2026-10-14", 386, 62]]
    assert pp._valor_em(serie, 1, D(2026, 10, 13)) == 380
    assert pp._valor_em(serie, 2, D(2026, 10, 13)) == 61
    assert pp._valor_em(serie, 1, D(2026, 10, 1)) is None


def test_tendencia_descarta_dia_sem_as_duas_series():
    serie = [["2026-10-10", 380, ""], ["2026-10-11", 381, 60]]
    assert pp._tendencia(serie) == [["11/10", 381, 60]]


def test_cartao_de_cadastros_diz_o_mes_e_o_grafico_semanal_explica_a_semana():
    import datetime
    from painel_plataforma import montar_painel
    m = {k: 0 for k in ("proj_ativos", "novos_mes", "ativos_30d", "proj_a_reativar", "proj_sem_prazo",
                        "st_rascunho", "st_concluido", "funil_cadastraram", "funil_com_projeto",
                        "funil_publicaram", "funil_aptos", "vence_30d", "vence_60d", "vence_90d", "vence_180d")}
    m.update({"semanas": [("09/11", 4)], "canais": {}})
    painel, _ = montar_painel(m, [], None, None, datetime.datetime(2026, 11, 13, 10))
    titulos = [c.titulo for linha in painel.linhas for c in linha]
    assert "Cadastros em novembro (ate 13/11)" in titulos
    semanal = [c for linha in painel.linhas for c in linha if c.titulo == "ONGs que criaram conta, por semana"][0]
    assert "andamento" in semanal.subtitulo
