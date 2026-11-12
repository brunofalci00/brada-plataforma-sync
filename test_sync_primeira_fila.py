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


def test_funis_de_organizador_e_hunter():
    painel, resumo = pf.montar_painel(_dados(), AGORA)
    org = _componente(painel, "Organizadores: do clique a reserva").etapas
    hunter = _componente(painel, "Hunter: da captacao ao cadastro").etapas
    assert org == [("Cliques em anuncios", 702), ("Conversas no chat", 4), ("Pedidos de reserva", 11),
                   ("Reservas pagas", 0)]
    assert hunter[1] == ("Abordados no WhatsApp", 4) and hunter[3] == ("Cadastraram", 1)
    assert resumo["pf_cliques_total"] == 702


def test_rosca_de_espacos_reparte_o_total():
    painel, _ = pf.montar_painel(_dados(), AGORA)
    fatias = dict(_componente(painel, "Espacos cadastrados").fatias)
    assert sum(fatias.values()) == 28 and fatias["Pausados ou ocultos"] == 5


def test_por_semana_soma_colunas_pedidas():
    diario = [["2026-11-03", 10, 1, 2, 0], ["2026-11-09", 5, 0, 1, 3], ["2026-11-10", 7, 2, 0, 0]]
    semanas = pf.por_semana(diario, AGORA.date(), [1, 2], n=2)
    assert semanas == [["02/11", 10, 1], ["09/11", 12, 2]]  # 09/11/2026 e segunda-feira
