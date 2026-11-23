import datetime

import sync_visao_geral as vg

AGORA = datetime.datetime(2026, 11, 12, 10, 0)


def _resumo():
    r = {
        "plataforma_aptos": 390, "comunidade_membros": 131, "incentivador_pipeline_reuniao": 151,
        "aprovai_mrr": 89.0, "pf_valor_pago": 2000.0, "pf_comissao_mes": 200.0,
        "incentivador_disparados": 632, "incentivador_engajaram": 64, "incentivador_cards": 119, "incentivador_reuniao": 0, "incentivador_cards_reuniao": 1,
        "elaboracao_disparados": 707, "elaboracao_cadastros": 4, "aprovai_assinantes": 0,
        "aptos_sdr_disparados": 1986, "aptos_sdr_cadastros": 59, "aptos_sdr_aptos": 17,
        "plataforma_novos_cadastros": 156, "plataforma_novos_aptos": 57,
        "comunidade_visitantes_30d": 480, "comunidade_login_30d": 120, "comunidade_primeira_acao": 84,
        "aprovai_visitas": 130, "aprovai_leads": 1, "pf_cliques_total": 882, "pf_jornada_visitas": 21,
        "pf_jornada_pag_espaco": 10, "pf_pedidos": 0, "pf_reservas_pagas": 0,
        "pf_produtor_abordados": 59, "pf_produtor_clicaram": 0, "pf_produtor_cadastros": 0,
    }
    for chave, dias in (("incentivador", 6), ("elaboracao", 0), ("plataforma", 1)):
        r[f"brenda_{chave}_dias_sem_envio"], r[f"brenda_{chave}_envios_7d"] = dias, 10
    return r


def _componente(painel, titulo):
    return next(c for linha in painel.linhas for c in linha if getattr(c, "titulo", "").startswith(titulo))


def test_visao_geral_cartoes_alerta_e_funil_por_produto():
    painel, faltando = vg.montar_painel(_resumo(), AGORA)
    assert faltando == []
    assert len(painel.linhas) == 3 and len(painel.linhas[0]) == 5
    funil = _componente(painel, "Funil de cada produto")
    assert len(funil.linhas) == len(vg.PRODUTOS) and funil.gradiente == ()


def test_reunioes_separam_hubspot_da_brenda():
    painel, _ = vg.montar_painel(_resumo(), AGORA)
    assert _componente(painel, "Reunioes: todas as origens").valor == 151
    assert _componente(painel, "Reunioes: vindas da Brenda").valor == 1


def test_receita_digital_soma_aprovai_e_primeira_fila():
    painel, _ = vg.montar_painel(_resumo(), AGORA)
    assert _componente(painel, "Receita do mes").valor == 289.0  # GMV fica de fora


def test_etapas_escritas_com_percentual_sobre_a_anterior_e_periodo():
    painel, _ = vg.montar_painel(_resumo(), AGORA)
    linhas = {l[1]: l for l in _componente(painel, "Funil de cada produto").linhas}
    aptos = linhas["SDR Aptos"]
    assert aptos[2] == "desde 14/08"
    assert aptos[3] == "1.986 disparados" and aptos[4] == f"59 cadastraram ({vg.dp.pct_br(59 / 1986)})" and aptos[5].startswith("17 projetos aptos (")
    plat = linhas["Plataforma (sem migrados)"]
    assert plat[4] == "" and round(plat[6], 4) == round(57 / 156, 4)


def test_alerta_marca_frente_parada():
    painel, _ = vg.montar_painel(_resumo(), AGORA)
    alerta = {l[0]: l for l in _componente(painel, "A Brenda esta disparando?").linhas}
    assert alerta["Incentivador"][-1] == "PARADA" and alerta["Elaboracao"][-1] == "Disparando"


def test_chave_ausente_vira_zero_e_e_reportada():
    resumo = _resumo()
    del resumo["aprovai_visitas"]
    painel, faltando = vg.montar_painel(resumo, AGORA)
    assert faltando == ["aprovai_visitas"]
    linha = next(l for l in _componente(painel, "Funil de cada produto").linhas if l[1] == "AprovAI (site e app)")
    assert linha[3] == "0 visitas" and linha[6] == 0


def test_foto_da_capacidade_e_da_fila_vai_para_a_aba_brenda():
    import explorar_disparos as ex
    foto = ex.componentes_visao_geral([], {}, AGORA.date())
    painel = ex.montar_painel([[46329, "Incentivador", "E-mail", "salic", 2, 2, 1, 0, 0, 0]], AGORA, foto)
    titulos = [getattr(c, "titulo", "") for linha in painel.linhas for c in linha]
    assert painel.aba == "Brenda"
    assert titulos[-1] == "Fila do alocador hoje (sem filtro)"
    assert "Capacidade da Brenda por semana (foto de hoje, sem filtro)" in titulos


def test_assinante_do_aprovai_sem_percentual_sobre_lead():
    painel, _ = vg.montar_painel(_resumo(), AGORA)
    linha = next(l for l in _componente(painel, "Funil de cada produto").linhas if l[1] == "AprovAI (site e app)")
    assert linha[5] == "0 pagando hoje"
