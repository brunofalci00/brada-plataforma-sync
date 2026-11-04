"""Testes da logica pura de sync_sdr_incentivador.py: as contas que viram KPI
na aba (taxa de conversao, split por canal). Nenhuma chamada de rede."""

from sync_sdr_incentivador import montar_secoes


def _dados(rows, total_automatize, total_executivo):
    return {"total_automatize": total_automatize, "total_executivo": total_executivo, "rows": rows}


def test_taxa_conversao_automatize():
    rows = [
        ["1", "Deal A", "Automatize", "", "", "[EV] Reuniao Agendada", "INTEGRATION", "", ""],
        ["2", "Deal B", "Executivo Brada", "", "", "[EV] Reuniao Agendada", "CRM_UI", "", ""],
        ["3", "Deal C", "Executivo Brada", "", "", "Ganho Incentivador", "IMPORT", "", ""],
    ]
    dados = _dados(rows, total_automatize=100, total_executivo=500)
    secoes = montar_secoes(dados)
    assert len(secoes) == 2

    volume, reuniao = secoes
    valores_volume = {rotulo: valor for valor, rotulo, _ in volume.metricas}
    assert valores_volume["Cards criados pela Automatize"] == 100
    assert valores_volume["Cards trabalhados por Executivo Brada"] == 500
    assert valores_volume["Total no pipeline Incentivador"] == 600

    valores_reuniao = {rotulo: valor for valor, rotulo, _ in reuniao.metricas}
    assert valores_reuniao["Chegaram em reuniao ou alem (total)"] == 3
    assert valores_reuniao["Vindos da Automatize"] == 1
    assert valores_reuniao["Vindos de Executivo Brada"] == 2
    assert valores_reuniao["Taxa de conversao Automatize (inbound -> reuniao)"] == 1 / 100


def test_sem_deals_automatize_nao_quebra_divisao():
    dados = _dados(rows=[], total_automatize=0, total_executivo=0)
    secoes = montar_secoes(dados)
    valores_reuniao = {rotulo: valor for valor, rotulo, _ in secoes[1].metricas}
    assert valores_reuniao["Taxa de conversao Automatize (inbound -> reuniao)"] == 0
