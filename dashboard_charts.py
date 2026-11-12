"""Grafico de linha nativo do Google Sheets (addChart), reaproveitavel por
qualquer aba que ja tenha uma tabela diaria pronta (1a coluna = dia/data,
colunas seguintes = series numericas). Achado do audit de 11/11: nenhuma aba
do dashboard usava grafico nativo, so cards de numero/texto.

Idempotente: cada publicacao apaga os charts existentes na aba de destino e
redesenha do zero, igual ao resto do projeto (write_overwrite, kpi.publish).
Sem isso, cada rodada do cron empilharia um grafico novo por cima do anterior.
"""


def _charts_existentes(svc, spreadsheet_id, dest_sheet_id):
    meta = svc.spreadsheets().get(
        spreadsheetId=spreadsheet_id,
        fields="sheets(properties(sheetId),charts(chartId))",
    ).execute()
    for sheet in meta.get("sheets", []):
        if sheet["properties"]["sheetId"] == dest_sheet_id:
            return [c["chartId"] for c in sheet.get("charts", [])]
    return []


def _sources_coluna(source_sheet_id, n_rows, col, data_start_row):
    """1 ou 2 GridRange concatenados: cabecalho (linha 0, nomeia a serie no
    grafico) + janela de dados a partir de `data_start_row`. `data_start_row`
    == 1 e o range contiguo de sempre (sem janela, sem stitching)."""
    if data_start_row <= 1:
        return [{"sheetId": source_sheet_id, "startRowIndex": 0, "endRowIndex": n_rows,
                  "startColumnIndex": col, "endColumnIndex": col + 1}]
    return [
        {"sheetId": source_sheet_id, "startRowIndex": 0, "endRowIndex": 1,
         "startColumnIndex": col, "endColumnIndex": col + 1},
        {"sheetId": source_sheet_id, "startRowIndex": data_start_row, "endRowIndex": n_rows,
         "startColumnIndex": col, "endColumnIndex": col + 1},
    ]


def _line_chart_request(source_sheet_id, n_rows, series_cols, domain_col, title,
                         dest_sheet_id, anchor_row, anchor_col, width, height,
                         right_axis_cols=(), data_start_row=1):
    domain_source = _sources_coluna(source_sheet_id, n_rows, domain_col, data_start_row)
    series = [
        {"series": {"sourceRange": {"sources": _sources_coluna(
            source_sheet_id, n_rows, col, data_start_row)}},
         "targetAxis": "RIGHT_AXIS" if col in right_axis_cols else "LEFT_AXIS"}
        for col in series_cols
    ]
    axis = [{"position": "BOTTOM_AXIS"}, {"position": "LEFT_AXIS"}]
    if right_axis_cols:
        axis.append({"position": "RIGHT_AXIS"})
    return {"addChart": {"chart": {
        "spec": {
            "title": title,
            "basicChart": {
                "chartType": "LINE",
                "legendPosition": "BOTTOM_LEGEND",
                "axis": axis,
                "domains": [{"domain": {"sourceRange": {"sources": domain_source}}}],
                "series": series,
                "headerCount": 1,
            },
        },
        "position": {"overlayPosition": {
            "anchorCell": {"sheetId": dest_sheet_id, "rowIndex": anchor_row, "columnIndex": anchor_col},
            "widthPixels": width, "heightPixels": height,
        }},
    }}}


def publish_line_chart(svc, spreadsheet_id, source_ws, dest_ws, n_rows, series_cols,
                        domain_col=0, title="", anchor_row=0, anchor_col=0,
                        width=680, height=320, right_axis_cols=(), window_rows=None):
    """`series_cols`: indices de coluna (0-based) na aba fonte, com os dados JA
    escritos (write_overwrite) antes desta chamada. `n_rows` inclui a linha de
    cabecalho (header_count=1: a 1a linha nomeia cada serie no grafico).
    `right_axis_cols`: subconjunto de `series_cols` que vai pro eixo direito -
    serie de escala muito maior (ex.: leads, na casa de dezenas de milhares)
    deixa as outras achatadas em zero se dividir o mesmo eixo esquerdo.
    `window_rows`: limita o grafico as ultimas N linhas de dado (cabecalho
    sempre entra, pra nomear a serie) - sem isso, serie historica longa vira
    eixo X ilegivel (um rotulo por dia, se sobrepondo) mesmo com a escala
    certa. O raw continua com o historico INTEIRO; so o grafico encurta.
    Sem linha de dado (n_rows <= 1), so limpa chart antigo e nao desenha nada
    novo — nao faz sentido um grafico vazio confundir com "zero sem contexto"."""
    data_start_row = max(1, n_rows - window_rows) if window_rows else 1
    # Sheets empurra o grafico pra CIMA (sobrepondo conteudo) quando a aba nao
    # tem linhas suficientes depois da ancora pra caber a altura inteira -
    # achado ao verificar visualmente apos a 1a publicacao (11/11): o anchor
    # ficava correto na API, mas o render clampava pra dentro das linhas
    # existentes. ~21px por linha e a altura padrao sem formatacao custom.
    linhas_necessarias = anchor_row + (height // 21) + 3
    if dest_ws.row_count < linhas_necessarias:
        dest_ws.resize(rows=linhas_necessarias)

    requests = [{"deleteEmbeddedObject": {"objectId": cid}}
                for cid in _charts_existentes(svc, spreadsheet_id, dest_ws.id)]
    if n_rows > 1:
        requests.append(_line_chart_request(
            source_ws.id, n_rows, series_cols, domain_col, title,
            dest_ws.id, anchor_row, anchor_col, width, height, right_axis_cols, data_start_row))
    if requests:
        svc.spreadsheets().batchUpdate(spreadsheetId=spreadsheet_id, body={"requests": requests}).execute()
