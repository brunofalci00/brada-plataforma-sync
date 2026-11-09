"""Builder reutilizavel de blocos de KPI em Sheets: titulo laranja, par de linhas
numero-grande/rotulo, nota de contexto. Mesmo padrao visual da aba "Plataforma
Brada" (ver dashboard_analytics.py), generalizado pra qualquer aba nova que
precise do mesmo estilo sem reimplementar merge/cor/fonte.

Cada aba e uma sequencia de "blocos": titulo da pagina (1x) + N secoes, cada
secao com ate 4 metricas (par valor/rotulo) e uma nota opcional em caixa de
aviso. Quem monta o conteudo (sync_aprovai.py, sync_sdr_incentivador.py) so
descreve os blocos; este modulo cuida da geometria e do estilo.
"""

ORANGE = {"red": 0.77254903, "green": 0.3529412, "blue": 0.06666667}
WHITE = {"red": 1, "green": 1, "blue": 1}
GRAY = {"red": 0.26666668, "green": 0.26666668, "blue": 0.26666668}
VALUE_BG = {"red": 0.972549, "green": 0.9607843, "blue": 0.94509804}
WARNING_BG = {"red": 1, "green": 0.9490196, "blue": 0.8666667}

N_COLS = 8  # A..H, 4 metricas de 2 colunas cada


def _cell(value=None, bg=None, bold=None, font_size=None, fg=None, number_format=None):
    ued = {}
    if value is not None:
        if isinstance(value, bool):
            ued["userEnteredValue"] = {"boolValue": value}
        elif isinstance(value, (int, float)):
            ued["userEnteredValue"] = {"numberValue": value}
        else:
            ued["userEnteredValue"] = {"stringValue": str(value)}
    fmt = {}
    if bg is not None:
        fmt["backgroundColor"] = bg
    tf = {}
    if bold is not None:
        tf["bold"] = bold
    if font_size is not None:
        tf["fontSize"] = font_size
    if fg is not None:
        tf["foregroundColor"] = fg
    if tf:
        fmt["textFormat"] = tf
    if number_format is not None:
        fmt["numberFormat"] = number_format
    if fmt:
        ued["userEnteredFormat"] = fmt
    return ued


def _row(cells):
    return {"values": cells}


def _merge(requests, sheet_id, r0, r1, c0, c1):
    requests.append({"mergeCells": {
        "range": {"sheetId": sheet_id, "startRowIndex": r0, "endRowIndex": r1,
                  "startColumnIndex": c0, "endColumnIndex": c1},
        "mergeType": "MERGE_ALL",
    }})


class Secao:
    """Um bloco: titulo + ate 4 metricas (valor, rotulo, number_format opcional) + nota."""

    def __init__(self, titulo, metricas, nota=None, nota_aviso=False):
        self.titulo = titulo
        self.metricas = metricas  # [(valor, rotulo, number_format|None), ...] ate 4
        self.nota = nota
        self.nota_aviso = nota_aviso


def montar_requests(sheet_id, titulo_pagina, subtitulo, secoes):
    """Monta a lista de requests de batchUpdate para desenhar a aba inteira.

    `secoes`: lista de `Secao`. Retorna `(requests, n_rows)`.
    """
    requests = [{"unmergeCells": {"range": {"sheetId": sheet_id}}},
                {"updateCells": {"range": {"sheetId": sheet_id},
                                  "fields": "userEnteredValue,userEnteredFormat"}}]
    rows = []
    merges = []  # (r0, r1, c0, c1) 0-indexed, relativo ao topo da aba

    rows.append(_row([_cell(titulo_pagina, bg=ORANGE, bold=True, font_size=16, fg=WHITE)] + [{}] * 7))
    rows.append(_row([_cell(subtitulo, font_size=11, fg=GRAY)] + [{}] * 7))
    merges.append((0, 1, 0, 8))
    merges.append((1, 2, 0, 8))
    rows.append(_row([{}] * 8))
    r = 3

    for secao in secoes:
        rows.append(_row([_cell(secao.titulo, bold=True, font_size=12, fg=ORANGE)] + [{}] * 7))
        merges.append((r, r + 1, 0, 8))
        r += 1

        val_row, lab_row = [], []
        metricas = (secao.metricas + [(None, "", None)] * 4)[:4]
        for valor, rotulo, nf in metricas:
            val_row.append(_cell(valor, bg=VALUE_BG, bold=True, font_size=20, fg=ORANGE,
                                  number_format=nf or {"type": "NUMBER", "pattern": "#,##0"}))
            val_row.append({})
            lab_row.append(_cell(rotulo, bg=VALUE_BG, font_size=11, fg=GRAY))
            lab_row.append({})
        rows.append(_row(val_row))
        rows.append(_row([{"userEnteredFormat": {"backgroundColor": VALUE_BG}}] * 8))
        rows.append(_row(lab_row))
        for i in range(4):
            c0 = i * 2
            merges.append((r, r + 2, c0, c0 + 2))     # valor, 2 linhas
            merges.append((r + 2, r + 3, c0, c0 + 2))  # rotulo, 1 linha
        r += 3

        if secao.nota:
            bg = WARNING_BG if secao.nota_aviso else None
            rows.append(_row([_cell(secao.nota, bg=bg, font_size=11, fg=GRAY)] + [{}] * 7))
            merges.append((r, r + 1, 0, 8))
            r += 1

        rows.append(_row([{}] * 8))
        r += 1

    requests.append({"updateCells": {
        "rows": rows, "fields": "userEnteredValue,userEnteredFormat",
        "start": {"sheetId": sheet_id, "rowIndex": 0, "columnIndex": 0},
    }})
    for r0, r1, c0, c1 in merges:
        _merge(requests, sheet_id, r0, r1, c0, c1)
    requests.append({"updateSheetProperties": {
        "properties": {"sheetId": sheet_id, "gridProperties": {"hideGridlines": True}},
        "fields": "gridProperties.hideGridlines",
    }})
    return requests, r


def get_or_create_ws(sh, title, rows=30, cols=N_COLS):
    import gspread
    try:
        ws = sh.worksheet(title)
        if ws.row_count < rows or ws.col_count < cols:
            ws.resize(rows=max(ws.row_count, rows), cols=max(ws.col_count, cols))
        return ws
    except gspread.exceptions.WorksheetNotFound:
        return sh.add_worksheet(title=title, rows=rows, cols=cols)


def publish(sh, svc, spreadsheet_id, aba, titulo_pagina, subtitulo, secoes):
    """Desenha/atualiza uma aba de KPIs. `svc` = googleapiclient Sheets service
    (precisa de batchUpdate; gspread sozinho nao faz merge+cor numa chamada so)."""
    ws = get_or_create_ws(sh, aba)
    requests, n_rows = montar_requests(ws.id, titulo_pagina, subtitulo, secoes)
    if n_rows > ws.row_count:
        ws.resize(rows=n_rows + 5)
    svc.spreadsheets().batchUpdate(spreadsheetId=spreadsheet_id, body={"requests": requests}).execute()
    return ws
