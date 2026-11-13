"""Testes da logica pura de sync_primeira_fila.py (sem banco)."""

import datetime

import sync_primeira_fila as pf

AGORA = datetime.datetime(2026, 11, 12, 10, 0)


def _dados(abordados=4, cadastraram=1, diario=None):
    return {
        "espacos": {"cadastrados": 28, "no_ar": 22, "em_revisao": 1},
        "anfitrioes": {"perfis": 40, "plano_rede": 22, "plano_basico": 18, "contas_recebimento": 16},
        "procura": {"cliques_total": 702, "cliques_30d": 629, "conversas": 4, "pedidos_reserva": 11},
        "reservas": {"pagas": 0, "valor_pago": 0.0, "comissao": 0.0, "canceladas_expiradas": 4},
        "hunter": {"captados": 154, "abordados": abordados, "responderam": 2, "cadastraram": cadastraram},
        "diario": diario or [],
    }


def _componente(painel, titulo):
    return next(c for linha in painel.linhas for c in linha if getattr(c, "titulo", "") == titulo)


D = datetime.date(2026, 11, 10)


def test_hunter_etapas_cumulativas_e_rotulos_vazios():
    linhas = pf.linhas_hunter([(D, None, "salao", "whatsapp", False, False, False, False, True),
                               (D, None, "salao", None, False, False, False, False, False)])
    por = {tuple(l[1:4]): l[4:] for l in linhas}
    assert por[("sem cidade", "salao", "WhatsApp")] == [1, 1, 1, 1, 1, 1]
    assert por[("sem cidade", "salao", "Sem envio")] == [1, 0, 0, 0, 0, 0]


def test_demanda_conta_cada_etapa_no_seu_dia():
    linhas = pf.linhas_demanda([(D, "Rio", "cliques"), (D, "Rio", "cliques"), (D, "Rio", "pagas")])
    assert linhas == [[(D - datetime.date(1899, 12, 30)).days, "Rio", 2, 0, 0, 1]]


def test_funis_vivos_e_bases_no_retorno():
    dados = dict(_dados(), hunter_linhas=[(D, "Rio", "salao", "whatsapp", True, False, False, False, False)],
                 demanda_linhas=[(D, "Rio", "cliques")])
    painel, resumo, bases = pf.montar_painel(dados, AGORA)
    titulos = [getattr(c, "titulo", "") for linha in painel.linhas for c in linha]
    assert "Anfitriao: Hunter da captacao ao cadastro (periodo A)" in titulos
    assert "Produtor (aguardando dado)" in titulos
    assert [b.aba for b, _ in bases] == ["base_pf_hunter", "base_pf_demanda"]
    assert resumo["pf_cliques_total"] == 702


def test_rosca_de_espacos_reparte_o_total():
    painel, _, _ = pf.montar_painel(_dados(), AGORA)
    fatias = dict(_componente(painel, "Espacos cadastrados (foto de hoje)").fatias)
    assert sum(fatias.values()) == 28 and fatias["Pausados ou ocultos"] == 5


def test_por_semana_soma_colunas_pedidas():
    diario = [["2026-11-03", 10, 1, 2, 0], ["2026-11-09", 5, 0, 1, 3], ["2026-11-10", 7, 2, 0, 0]]
    semanas = pf.por_semana(diario, AGORA.date(), [1, 2], n=2)
    assert semanas == [["02/11", 10, 1], ["09/11", 12, 2]]  # 09/11/2026 e segunda-feira
