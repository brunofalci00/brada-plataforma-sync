"""Testes da montagem pura de requests do builder de KPI (sem rede).

Achado do Bruno 12/11: rotulo/nota usam wrapStrategy WRAP mas ficavam presos
na altura padrao do Sheets (21px), cortando a 2a/3a linha de texto mais longo
(metrica wrapando no meio da palavra, nota de alerta cortada no meio da
frase). Esses testes travam que toda linha de rotulo/nota/subtitulo ganha
altura extra, e que as colunas ficam mais largas que o default do Sheets."""

import dashboard_kpi_blocks as kpi


def _dim_requests(requests):
    return [r["updateDimensionProperties"] for r in requests if "updateDimensionProperties" in r]


def test_subtitulo_e_titulo_de_secao_ganham_altura_extra():
    secoes = [kpi.Secao("Titulo da secao", [(10, "Rotulo curto", None)])]
    requests, _ = kpi.montar_requests(999, "Titulo da pagina", "Subtitulo", secoes)
    dims = _dim_requests(requests)
    linhas = {d["range"]["startIndex"]: d["properties"]["pixelSize"] for d in dims if d["range"]["dimension"] == "ROWS"}
    assert linhas[1] == kpi.ALTURA_SUBTITULO   # subtitulo, linha 1
    assert linhas[3] == kpi.ALTURA_ROTULO      # titulo da 1a secao, linha 3


def test_linha_de_rotulo_das_metricas_ganha_altura_extra():
    secoes = [kpi.Secao("Titulo", [(10, "Rotulo 1", None), (20, "Rotulo 2", None)])]
    requests, _ = kpi.montar_requests(999, "T", "S", secoes)
    dims = _dim_requests(requests)
    linhas = {d["range"]["startIndex"] for d in dims if d["range"]["dimension"] == "ROWS"
              and d["properties"]["pixelSize"] == kpi.ALTURA_ROTULO}
    # linha 3 = titulo da secao, linha 6 = rotulo das metricas (titulo=3, valor=4-5, rotulo=6)
    assert {3, 6}.issubset(linhas)


def test_nota_ganha_altura_extra_quando_existe():
    com_nota = kpi.montar_requests(999, "T", "S", [kpi.Secao("Sec", [(1, "r", None)], nota="nota qualquer")])[0]
    sem_nota = kpi.montar_requests(999, "T", "S", [kpi.Secao("Sec", [(1, "r", None)])])[0]
    dims_com = _dim_requests(com_nota)
    dims_sem = _dim_requests(sem_nota)
    notas_com = [d for d in dims_com if d["properties"]["pixelSize"] == kpi.ALTURA_NOTA]
    notas_sem = [d for d in dims_sem if d["properties"]["pixelSize"] == kpi.ALTURA_NOTA]
    assert len(notas_com) == 1
    assert len(notas_sem) == 0


def test_rotulo_nota_e_subtitulo_tem_wrap_strategy_wrap():
    # Sem isso, texto mais longo que a celula so corta na borda direita -
    # aumentar a altura da linha sozinho nao adianta nada (achado do Bruno 12/11).
    secoes = [kpi.Secao("Titulo da secao", [(10, "Rotulo", None)], nota="Nota qualquer")]
    requests, _ = kpi.montar_requests(999, "Pagina", "Subtitulo", secoes)
    update_cells = next(r["updateCells"] for r in requests if "updateCells" in r and "rows" in r["updateCells"])
    linhas = update_cells["rows"]
    subtitulo_cell = linhas[1]["values"][0]
    secao_titulo_cell = linhas[3]["values"][0]
    rotulo_cell = linhas[6]["values"][0]
    nota_cell = linhas[7]["values"][0]
    for cell in (subtitulo_cell, secao_titulo_cell, rotulo_cell, nota_cell):
        assert cell["userEnteredFormat"]["wrapStrategy"] == "WRAP"


def test_colunas_ficam_mais_largas_que_o_default_do_sheets():
    requests, _ = kpi.montar_requests(999, "T", "S", [kpi.Secao("Sec", [(1, "r", None)])])
    dims = _dim_requests(requests)
    colunas = [d for d in dims if d["range"]["dimension"] == "COLUMNS"]
    assert len(colunas) == 1
    assert colunas[0]["properties"]["pixelSize"] == kpi.LARGURA_COLUNA
    assert kpi.LARGURA_COLUNA > 100  # default do Sheets e 100px
