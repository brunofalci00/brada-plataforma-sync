"""Testes da prospeccao de produtores da Primeira Fila (sem banco)."""

import datetime

import dashboard_painel as dp
import painel_filtros as fl
import pf_produtor as pp

D = datetime.date(2026, 11, 20)
HOJE = D + datetime.timedelta(days=3)


def test_cadastro_conta_como_clique_e_canal_combina_email_e_whatsapp():
    linhas = pp.linhas([(D, "Rouanet", "sp", True, True, False, True, False),
                        (D, None, None, True, False, False, False, True)])
    por = {tuple(l[1:4]): l[4:] for l in linhas}
    assert por[("Rouanet", "SP", "E-mail e WhatsApp")] == [1, 1, 1, 1, 1, 0]
    assert por[("sem lei", "sem UF", "E-mail")] == [1, 1, 0, 0, 0, 1]


def test_blocos_devolvem_base_e_resumo_do_produtor():
    painel = dp.Painel("Primeira Fila", "t", "s")
    ctl = fl.controles("Primeira Fila", HOJE, [fl.campo_lista("cidade", "Cidade", ["Rio"])])
    dados = {"produtor_linhas": [(D, "Rouanet", "RJ", True, False, True, True, False),
                                 (D, "Rouanet", "RJ", True, False, False, False, False)],
             "produtor_fila": {"total": 1235, "abordados": 2, "com_telefone": 982}}
    pl, resumo = pp.blocos(painel, ctl, dados)
    assert resumo == {"pf_produtor_abordados": 2, "pf_produtor_clicaram": 1, "pf_produtor_cadastros": 1}
    cartoes = {c.titulo: c.valor for linha in painel.linhas for c in linha if isinstance(c, dp.Cartao)}
    assert cartoes["Ainda nao abordados (hoje)"] == 1233
    assert len(pl) == 1
