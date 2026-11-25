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


def test_celula_com_cara_de_dado_pessoal_vira_so_protocolo():
    reg = [("C", "t", HOJE, None, "", HOJE, "fulano@gmail.com (PF-2026-9), Sala (PF-2026-8)", 0),
           ("C", "t", HOJE, None, "", HOJE, "Tel 21 99876-5432 (PF-2026-7)", 0)]
    linhas = pc.lista(reg)
    assert linhas[0][1] == "PF-2026-9, PF-2026-8"
    assert linhas[1][1] == "PF-2026-7"


def test_so_espaco_publicado_e_visivel_mostra_o_nome():
    assert "revisao_status = 'publicado' and not coalesce(e.revisao_oculto, false)" in pc.CONSULTA
    assert "else e.protocol end" in pc.CONSULTA
