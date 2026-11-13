"""Aba Explorar disparos: base agregada sem PII, formulas pt-BR e filtros."""

import datetime

import explorar_disparos as ex
import sdr_automatize as sdr

D = datetime.date
AGORA = datetime.datetime(2026, 11, 12, 10, 0)


def _e(modalidade, dia, canal="email", fonte="salic", **marcas):
    e = {"modalidade": modalidade, "dia": dia, "canal": canal, "fonte": fonte}
    e.update({m: marcas.get(m, False) for m in sdr.MARCAS})
    return e


def test_etapa_conta_quem_chegou_nela_ou_alem():
    card = ex.etapas_do_envio(_e(sdr.INCENTIVADOR, D(2026, 11, 3), card=True))
    assert card == {"envios": 1, "entregues": 1, "abriram": 1, "engajaram": 1, "responderam": 0, "resultado": 1}
    # cadastro nao e resultado do Incentivador, card nao e resultado das outras frentes
    assert ex.etapas_do_envio(_e(sdr.INCENTIVADOR, D(2026, 11, 3), cadastro=True))["resultado"] == 0
    assert ex.etapas_do_envio(_e(sdr.APTOS, D(2026, 11, 3), cadastro=True))["resultado"] == 1
    assert ex.etapas_do_envio(_e(sdr.APTOS, D(2026, 11, 3), bounce=True))["entregues"] == 0


def test_base_agrega_por_dia_frente_canal_fonte_sem_lead():
    linhas = ex.base([_e(sdr.INCENTIVADOR, D(2026, 11, 3), abriu=True), _e(sdr.INCENTIVADOR, D(2026, 11, 3)),
                      _e(sdr.APTOS, D(2026, 11, 2), "whatsapp", None), _e("outra", D(2026, 11, 2))])
    assert linhas == [[46328, "Plataforma", "WhatsApp", "sem fonte", 1, 1, 0, 0, 0, 0],
                      [46329, "Incentivador", "E-mail", "salic", 2, 2, 1, 0, 0, 0]]
    assert ex.fontes(linhas) == ["salic", "sem fonte"]


def _painel():
    return ex.montar_painel([[46329, "Incentivador", "E-mail", "salic", 2, 2, 1, 0, 0, 0]], AGORA)


def test_formulas_leem_os_filtros_com_separador_pt_br():
    flt = ex.Filtros(ex.controles(AGORA.date(), ["salic"]))
    f = flt.soma_p("abriram", frente="Incentivador", canal=False)
    assert f.startswith("SUMIFS(base_disparos!$G:$G;base_disparos!$A:$A;\">=\"&'Brenda'!$A$6")
    assert '"Incentivador"' in f and "base_disparos!$C:$C" not in f  # canal ignorado, frente fixa
    assert "IF('Brenda'!$H$6=\"Todas\";\"*\"" in f  # fonte segue o filtro
    assert "," not in f.replace('"#,##0"', "")


def test_painel_comeca_pelos_filtros_e_todo_valor_vivo_e_formula():
    p = _painel()
    assert isinstance(p.linhas[0][0], ex.dp.Controles)
    itens, _ = p.layout()
    _, valores, _ = p.blocos(itens)
    formulas = [v for linha in valores for v in linha if isinstance(v, ex.dp.Formula)]
    assert len(formulas) >= 40 and all(f.startswith("=") for f in formulas)
    reqs = p.montar_requests(1, 2, 120, len(valores) + 5, {"charts": [], "bandas": [], "condicionais": 0},
                             itens, *p.blocos(itens)[::2])
    assert sum("addChart" in r for r in reqs) == 9


def test_serie_diaria_reserva_espaco_para_a_sequencia_se_espalhar():
    serie = next(c for linha in _painel().linhas for c in linha if getattr(c, "titulo", "") == "Envios no período A")
    bloco = serie.bloco()
    assert len(bloco) == ex.MAX_PONTOS + 1 and "SEQUENCE" in bloco[1][0]


def test_divisao_por_semana_e_fila_da_visao_geral():
    envios = [_e(sdr.INCENTIVADOR, D(2026, 11, 10)), _e(sdr.APTOS, D(2026, 11, 11)), _e(sdr.APTOS, D(2026, 11, 3))]
    assert ex.divisao_por_semana(envios, D(2026, 11, 12), n=2) == [["02/11", 0, 0, 1], ["09/11", 1, 0, 1]]
    fila = {sdr.ELABORACAO: {"enviados": 0, "na_fila": 239, "canal_fila": "whatsapp"}}
    linhas = ex.linhas_fila(envios, fila, D(2026, 11, 12), janela=28)
    assert linhas[1] == ["AprovAI", 0, 0, 239, "WhatsApp"] and linhas[2][1] == 2 / 28
    assert len(ex.componentes_visao_geral(envios, fila, D(2026, 11, 12))) == 2


def test_fonte_do_banco_nao_injeta_formula():
    import pytest
    assert ex.literal("esporte-federal") == '"esporte-federal"'
    with pytest.raises(ValueError):
        ex.literal('x");IMPORTXML("http://y')
    linhas = [[46329, "Incentivador", "E-mail", 'x");IMPORTXML("h', 9, 0, 0, 0, 0, 0],
              [46329, "Incentivador", "E-mail", "salic", 1, 0, 0, 0, 0, 0]]
    assert ex.fontes(linhas) == ["salic"]
