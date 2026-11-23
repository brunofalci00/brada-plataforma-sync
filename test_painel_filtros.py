

def test_comparacao_com_maturacao_corta_o_fim_do_periodo():
    import datetime
    import dashboard_painel as dp
    import painel_filtros as fl
    base = fl.Base("base_x", [], [("a", "A"), ("b", "B")])
    ctl = fl.controles("X", datetime.date(2026, 11, 23))
    t = fl.comparacao_ab("Comp", fl.Filtros(ctl, base), [("A", "a"), ("B", "b")], maturacao=14)
    assert "14+ dias" in t.titulo
    assert all("TODAY()-14" in str(c) for c in t.linhas[0][1:2])
