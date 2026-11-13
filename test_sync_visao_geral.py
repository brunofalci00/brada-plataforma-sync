import datetime

import sync_visao_geral as vg

AGORA = datetime.datetime(2026, 11, 12, 10, 0)


def _resumo():
    return {
        "plataforma_aptos": 390, "comunidade_membros": 131, "incentivador_pipeline_reuniao": 151,
        "aprovai_mrr": 89.0, "pf_valor_pago": 200.0,
        "incentivador_disparados": 632, "incentivador_cards": 119, "incentivador_reuniao": 0,
        "elaboracao_disparados": 707, "elaboracao_cadastros": 4, "aprovai_assinantes": 0,
        "aptos_sdr_disparados": 1986, "aptos_sdr_cadastros": 59, "aptos_sdr_aptos": 17,
        "plataforma_novos_cadastros": 156, "plataforma_novos_aptos": 57,
        "comunidade_visitantes_30d": 480, "comunidade_primeira_acao": 84,
        "aprovai_visitas": 130, "aprovai_leads": 1, "pf_cliques_total": 882, "pf_pedidos": 0, "pf_reservas_pagas": 0,
    }


def _componente(painel, titulo):
    return next(c for linha in painel.linhas for c in linha if getattr(c, "titulo", "") == titulo)


def test_conversao_dos_tres_sdrs():
    painel, faltando = vg.montar_painel(_resumo(), AGORA)
    conv = dict(_componente(painel, "Disparo ate o primeiro resultado").linhas)
    assert faltando == []
    assert round(conv["Incentivador"], 4) == round(119 / 632, 4)
    assert round(conv["Aptos"], 4) == round(59 / 1986, 4)


def test_receita_digital_soma_aprovai_e_primeira_fila():
    painel, _ = vg.montar_painel(_resumo(), AGORA)
    assert _componente(painel, "Receita digital").valor == 289.0


def test_tabela_de_produtos_fim_sobre_comeco():
    painel, _ = vg.montar_painel(_resumo(), AGORA)
    linhas = {l[0]: l for l in _componente(painel, "Funil de cada produto").linhas}
    assert linhas["SDR Aptos"][2:5] == [1986, 59, 17]
    assert linhas["Plataforma (novos)"][3] == ""  # sem etapa do meio
    assert round(linhas["Plataforma (novos)"][5], 4) == round(57 / 156, 4)


def test_chave_ausente_vira_zero_e_e_reportada():
    resumo = _resumo()
    del resumo["aprovai_visitas"]
    painel, faltando = vg.montar_painel(resumo, AGORA)
    assert faltando == ["aprovai_visitas"]
    linha = next(l for l in _componente(painel, "Funil de cada produto").linhas if l[0] == "AprovAI (app)")
    assert linha[2] == 0 and linha[5] == 0


def test_divisao_dos_disparos_entra_depois_dos_sdrs():
    import explorar_disparos as ex
    blocos = ex.componentes_visao_geral([], {}, AGORA.date())
    painel, _ = vg.montar_painel(_resumo(), AGORA, blocos)
    assert painel.linhas[2][0].titulo == "Disparos da Brenda por semana"
    assert painel.linhas[3][0].titulo == "Fila do alocador da Brenda hoje"
