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
    assert card == {"envios": 1, "entregues": 1, "abriram": 1, "engajaram": 1, "responderam": 0, "resultado": 0}
    # o card nasce sozinho ao abrir a agenda: resultado do Incentivador e reuniao (HubSpot)
    reuniao = dict(_e(sdr.INCENTIVADOR, D(2026, 11, 3), card=True), reuniao=True)
    assert ex.etapas_do_envio(reuniao)["resultado"] == 1
    # elaboracao termina na assinatura do AprovAI, nao no cadastro
    assert ex.etapas_do_envio(_e(sdr.ELABORACAO, D(2026, 11, 3), cadastro=True))["resultado"] == 0
    assert ex.etapas_do_envio(dict(_e(sdr.ELABORACAO, D(2026, 11, 3)), assinou=True))["resultado"] == 1
    # cadastro nao e resultado do Incentivador
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
    assert sum("addChart" in r for r in reqs) == 8  # rosca por frente saiu (repetia a capacidade semanal)


def test_serie_diaria_reserva_espaco_para_a_sequencia_se_espalhar():
    serie = next(c for linha in _painel().linhas for c in linha if getattr(c, "titulo", "") == "Envios no período A")
    bloco = serie.bloco()
    assert len(bloco) == ex.MAX_PONTOS + 1 and "SEQUENCE" in bloco[1][0]


def test_divisao_por_canal_separa_email_e_whatsapp_por_semana():
    envios = [_e(ex.sdr.INCENTIVADOR, D(2026, 11, 10), canal="email"),
              _e(ex.sdr.ELABORACAO, D(2026, 11, 11), canal="whatsapp"),
              _e(ex.sdr.APTOS, D(2026, 11, 3), canal="email")]
    assert ex.divisao_por_canal(envios, D(2026, 11, 12), n=2) == [["02/11", 1, 0], ["09/11", 1, 1]]


def test_divisao_por_semana_e_fila_da_visao_geral():
    envios = [_e(sdr.INCENTIVADOR, D(2026, 11, 10)), _e(sdr.APTOS, D(2026, 11, 11)), _e(sdr.APTOS, D(2026, 11, 3))]
    assert ex.divisao_por_semana(envios, D(2026, 11, 12), n=2) == [["02/11", 0, 0, 1], ["09/11", 1, 0, 1]]
    fila = {sdr.ELABORACAO: {"enviados": 0, "na_fila": 239, "canal_fila": "whatsapp"}}
    linhas = ex.linhas_fila(envios, fila, D(2026, 11, 12), janela=28)
    assert linhas[1] == ["Elaboracao", 0, 0, 239, "WhatsApp"] and linhas[2][1] == 2 / 28
    assert len(ex.componentes_visao_geral(envios, fila, D(2026, 11, 12))) == 3


def test_fonte_do_banco_nao_injeta_formula():
    import pytest
    assert ex.literal("esporte-federal") == '"esporte-federal"'
    with pytest.raises(ValueError):
        ex.literal('x");IMPORTXML("http://y')
    linhas = [[46329, "Incentivador", "E-mail", 'x");IMPORTXML("h', 9, 0, 0, 0, 0, 0],
              [46329, "Incentivador", "E-mail", "salic", 1, 0, 0, 0, 0, 0]]
    assert ex.fontes(linhas) == ["salic"]


def test_saude_marca_frente_parada_por_dias_uteis():
    import sdr_automatize as s
    hoje = datetime.date(2026, 11, 18)  # quarta
    env = [{"modalidade": s.INCENTIVADOR, "dia": datetime.date(2026, 11, 13), "canal": "email"},
           {"modalidade": s.APTOS, "dia": hoje, "canal": "email"}]
    linhas = {l[0]: l for l in ex.linhas_saude(env, hoje, fila={})}
    assert linhas["Incentivador"][2:] == [3, 1, 0, "PARADA"]  # sexta 13 > seg, ter, qua
    assert linhas["Plataforma"][-1] == "Disparando" and linhas["Elaboracao"][-1] == "SEM ENVIO"


def test_cartao_de_contagem_nao_compara_base_pequena():
    import painel_filtros as fl
    import dashboard_painel as dp
    c = fl.CartaoVivo("x", "A1", "B1")
    assert "B1)<20" in str(c.bloco()[1][2])
    assert "B1)=0" in str(fl.CartaoVivo("t", "A1", "B1", dp.FMT_PCT).bloco()[1][2])


def test_hash_do_lead_bate_com_o_do_sync():
    import sync
    assert sdr.hash_lead("abc-123") == sync.hash_id("abc-123") and sdr.hash_lead(None) == ""


def test_resultado_conta_uma_vez_por_lead():
    envios = [dict(_e(sdr.APTOS, D(2026, 11, d), cadastro=True), lead_hash="abc") for d in (3, 4, 5)]
    linhas = ex.base(envios)
    assert sum(l[-1] for l in linhas) == 1 and sum(l[4] for l in linhas) == 3  # 3 envios, 1 cadastro


def test_cadastro_pelo_link_so_vale_na_frente_plataforma():
    import explorar_disparos as ed
    import sdr_automatize as sdr
    base_envio = {"cadastro": False, "lead_hash": "h1"}
    envios = [dict(base_envio, modalidade=sdr.APTOS), dict(base_envio, modalidade=sdr.INCENTIVADOR),
              dict(base_envio, modalidade=sdr.APTOS, lead_hash="h2")]
    marcados = ed.marcar_resultados(envios, frozenset(), frozenset(), frozenset({"h1"}))
    assert [e["cadastro"] for e in marcados] == [True, False, False]


def test_link_usa_o_mesmo_hash_do_lead():
    import explorar_disparos as ed
    import sdr_automatize as sdr

    class Aba:
        def get_all_values(self):
            return [["user_hash", "utm_term"], ["x", " ABC-1 "], ["y", ""], ["z"]]

    class Planilha:
        def worksheet(self, _):
            return Aba()
    assert ed.cadastros_pelo_link(Planilha()) == frozenset({sdr.hash_lead("abc-1")})


def test_fontes_por_frente_mostram_contatos_conversao_e_se_parou():
    """Revisao do Bruno (25/11): de onde vem os contatos de cada frente, desde o inicio."""
    envios = [dict(_e(sdr.APTOS, D(2026, 8, 14), fonte="icms-rj"), lead_hash="a"),
              dict(_e(sdr.APTOS, D(2026, 8, 20), fonte="icms-rj", cadastro=True), lead_hash="b"),
              dict(_e(sdr.APTOS, D(2026, 8, 21), fonte="icms-rj"), lead_hash="b"),
              dict(_e(sdr.INCENTIVADOR, D(2026, 11, 20), fonte="patrocinadores-brada"), lead_hash="c"),
              dict(_e(sdr.ELABORACAO, D(2026, 11, 20), fonte=None), lead_hash="d")]
    linhas = ex.linhas_fontes(envios, D(2026, 11, 25))
    assert [l[:2] for l in linhas] == [["Incentivador", "Patrocinadores da Brada (patrocinadores-brada)"],
                                       ["Elaboracao", "sem fonte"],
                                       ["Plataforma", "ICMS RJ (icms-rj)"]]
    assert linhas[2][2:] == [2, 3, 0, "14/08/26", "21/08/26", 1, "parou em 21/08"]
    assert linhas[0][-1] == "ativa"
