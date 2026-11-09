"""Testes da logica pura de sync_primeira_fila.py (sem banco)."""

from sync_primeira_fila import montar_secoes


def _dados(abordados=4, cadastraram=1):
    return {
        "espacos": {"cadastrados": 28, "no_ar": 22, "em_revisao": 1},
        "anfitrioes": {"perfis": 40, "plano_rede": 22, "plano_basico": 18, "contas_recebimento": 16},
        "procura": {"cliques_total": 702, "cliques_30d": 629, "conversas": 4, "pedidos_reserva": 11},
        "reservas": {"pagas": 0, "valor_pago": 0.0, "comissao": 0.0, "canceladas_expiradas": 4},
        "hunter": {"captados": 154, "abordados": abordados, "responderam": 2, "cadastraram": cadastraram},
    }


def test_cinco_blocos_com_quatro_metricas():
    secoes = montar_secoes(_dados())
    assert len(secoes) == 5
    assert [len(s.metricas) for s in secoes] == [3, 4, 4, 4, 4]


def test_taxa_de_cadastro_do_hunter():
    hunter = montar_secoes(_dados(abordados=4, cadastraram=1))[4]
    assert hunter.metricas[3][0] == 0.25


def test_sem_abordados_nao_divide_por_zero():
    hunter = montar_secoes(_dados(abordados=0, cadastraram=0))[4]
    assert hunter.metricas[3][0] == 0
