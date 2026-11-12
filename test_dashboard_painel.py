"""Montagem pura do painel (sem rede). Cada teste trava uma pegadinha do Sheets vista na planilha real."""

import dashboard_painel as dp


def _reqs(painel, existentes=None):
    itens, _ = painel.layout()
    inicio, _, formatos = painel.blocos(itens)
    return painel.montar_requests(1, 2, 80, 50, existentes or {"charts": [], "bandas": [], "condicionais": 0},
                                  itens, inicio, formatos)


def _graficos(reqs):
    return [r["addChart"]["chart"] for r in reqs if "addChart" in r]


def test_numeros_em_portugues():
    assert dp.num_br(4669) == "4.669"
    assert dp.pct_br(0.39) == "39%" and dp.pct_br(0.088) == "8,8%" and dp.pct_br(0.0026) == "0,26%"
    assert dp.pct_br(0) == "0%"


def test_componentes_da_linha_dividem_a_largura():
    p = dp.Painel("X", "t", "r").linha(dp.Cartao("a", 1), dp.Cartao("b", 2))
    (_, (x1, y1, w1, _)), (_, (x2, _, w2, _)) = p.layout()[0]
    assert w1 == w2 and x2 == w1 + dp.GAP and x2 + w2 <= dp.CONTEUDO


def test_tabela_comeca_em_linha_inteira():
    p = dp.Painel("X", "t", "r").linha(dp.Cartao("a", 1)).linha(dp.Tabela("T", ["A", "B"], [["x", 1]]))
    _, (_, y, _, _) = p.layout()[0][1]
    assert y % dp.ALTURA_LINHA == 0


def test_funil_rotula_valor_e_percentual_do_topo():
    bloco = dp.Funil("F", [("Topo", 4669), ("Meio", 1820)]).bloco()
    assert bloco[1] == ["Topo", 4669, "4.669"]
    assert bloco[2] == ["Meio", 1820, "1.820 · 39%"]


def test_funil_valores_ficam_numericos():
    # serie com texto e numero misturados some do grafico sem erro
    bloco = dp.Funil("F", [("A", "10"), ("B", None)]).bloco()
    assert all(isinstance(l[1], (int, float)) for l in bloco[1:])


def test_cartao_com_base_usa_coluna_nao_vizinha_e_sem_agregacao():
    # base em celula vizinha: o Sheets funde os intervalos e o cartao mostra valor + base somados
    c = dp.Cartao("Aptos", 390, base=386, descricao_base="vs 30 dias")
    assert c.bloco() == [["Aptos"], [390, "", 386]]
    p = dp.Painel("X", "t", "r").linha(c)
    sc = _graficos(_reqs(p))[0]["spec"]["scorecardChart"]
    chave = sc["keyValueData"]["sourceRange"]["sources"][0]
    base = sc["baselineValueData"]["sourceRange"]["sources"][0]
    assert "aggregateType" not in sc
    assert base["startColumnIndex"] - chave["endColumnIndex"] >= 1


def test_rosca_de_uma_fatia_sai_da_linha():
    p = dp.Painel("X", "t", "r").linha(dp.Serie("S", ["a", "b"], [["x", 1]]), dp.Rosca("R", [("so", 5), ("zero", 0)]))
    assert len(p.linhas[0]) == 1


def test_rotulo_so_em_serie_unica_e_eixo_com_folga():
    uma = dp.Serie("S", ["Canal", "Leads"], [["A", 1986], ["B", 632]], tipo="BAR")
    duas = dp.Serie("D", ["Semana", "X", "Y"], [["s1", 0, 0]])
    reqs = _reqs(dp.Painel("X", "t", "r").linha(uma, duas))
    bc_uma, bc_duas = (g["spec"]["basicChart"] for g in _graficos(reqs))
    assert "dataLabel" in bc_uma["series"][0]
    assert all("dataLabel" not in s for s in bc_duas["series"])
    eixo = next(a for a in bc_uma["axis"] if a["position"] == "BOTTOM_AXIS")
    assert eixo["viewWindowOptions"]["viewWindowMax"] > 1986


def test_serie_com_formato_formata_as_celulas_de_valor():
    s = dp.Serie("Conv", ["SDR", "Conversao"], [["A", 0.18], ["B", 0.03]], formato=dp.FMT_PCT)
    assert s.formatos_dados(10) == [(11, 1, dp.FMT_PCT), (12, 1, dp.FMT_PCT)]


def test_gradiente_pula_coluna_constante():
    # coluna toda zero: o gradiente pintaria tudo da cor maxima
    constante = dp.Tabela("T", ["S", "Taxa"], [["a", 0], ["b", 0]], formatos=[None, dp.FMT_PCT], gradiente=[1])
    variada = dp.Tabela("T", ["S", "Taxa"], [["a", 0], ["b", 0.3]], formatos=[None, dp.FMT_PCT], gradiente=[1])
    contar = lambda t: sum("addConditionalFormatRule" in r for r in _reqs(dp.Painel("X", "t", "r").linha(t)))  # noqa: E731
    assert contar(constante) == 0 and contar(variada) == 1


def test_republicar_apaga_graficos_faixas_e_regras_antigas():
    reqs = _reqs(dp.Painel("X", "t", "r").linha(dp.Cartao("a", 1)),
                 {"charts": [11, 12], "bandas": [7], "condicionais": 2})
    assert sum("deleteEmbeddedObject" in r for r in reqs) == 2
    assert sum("deleteBanding" in r for r in reqs) == 1
    assert sum("deleteConditionalFormatRule" in r for r in reqs) == 2


def test_dados_de_cada_componente_ficam_empilhados_sem_sobrepor():
    p = dp.Painel("X", "t", "r").linha(dp.Funil("F", [("a", 3), ("b", 1)]), dp.Cartao("C", 5))
    itens, _ = p.layout()
    inicio, valores, _ = p.blocos(itens)
    assert inicio == [0, 4]
    assert valores[4] == ["C"] and valores[5] == [5]


def test_texto_que_comeca_com_igual_nao_vira_formula():
    t = dp.Tabela("T", ["Pagina", "Vistas"], [["=IMPORTXML(\"http://x\")", 3]], formatos=[None, dp.FMT_INT])
    celulas = [c for r in _reqs(dp.Painel("X", "t", "r").linha(t)) if "updateCells" in r and "rows" in r["updateCells"]
               for linha in r["updateCells"]["rows"] for c in linha["values"] if c.get("userEnteredValue")]
    assert not any("formulaValue" in c["userEnteredValue"] for c in celulas)
