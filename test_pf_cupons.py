import datetime

import dashboard_painel as dp
import pf_cupons as pc

D = datetime.date
HOJE = D(2026, 11, 25)
REG = [("PRIMEIRAFILA100", "12_meses", D(2026, 11, 1), D(2027, 11, 1), "rede", D(2026, 11, 1), "Casa Azul (PF-2026-1)", 1),
       ("PRIMEIRAFILA100", "12_meses", D(2026, 11, 2), D(2026, 11, 20), "", D(2026, 11, 2), None, 0),
       ("PRIMEIRAFILA100LEAD", "12_meses", D(2026, 11, 3), None, "basico", D(2026, 11, 3), "Galpao (PF-2026-2)", 0)]


def test_resumo_conta_anfitrioes_guia_e_cupom_valendo_por_cupom():
    assert pc.resumo(REG, HOJE) == [["PRIMEIRAFILA100", 2, 1, 1], ["PRIMEIRAFILA100LEAD", 1, 0, 1]]


def test_lista_identifica_pelo_espaco_e_nunca_pela_pessoa():
    linhas = pc.lista(REG)
    assert linhas[0][:2] == ["PRIMEIRAFILA100", "Casa Azul (PF-2026-1)"] and linhas[0][-1] == "sim"
    assert linhas[1][1] == pc.SEM_ESPACO and linhas[1][4] == "20/11/26"
    assert linhas[2][4] == "sem fim"


def test_blocos_sem_cupom_nao_desenha_nada_e_com_cupom_fecha_a_grade():
    p = dp.Painel("Primeira Fila", "t", "s")
    pc.blocos(p, [], HOJE)
    assert p.linhas == []
    pc.blocos(p, REG, HOJE)
    assert [c.titulo.split(":")[0] for linha in p.linhas for c in linha] == ["Cupons", "Cupons"]
