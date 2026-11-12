"""Testes da montagem pura de request do grafico (sem chamada de rede)."""

from dashboard_charts import _line_chart_request


def test_line_chart_request_domain_e_series_apontam_pra_sheet_fonte():
    req = _line_chart_request(
        source_sheet_id=111, n_rows=5, series_cols=[1, 2], domain_col=0,
        title="Teste", dest_sheet_id=222, anchor_row=10, anchor_col=0, width=600, height=300)
    spec = req["addChart"]["chart"]["spec"]
    assert spec["title"] == "Teste"
    domain_src = spec["basicChart"]["domains"][0]["domain"]["sourceRange"]["sources"][0]
    assert domain_src == {"sheetId": 111, "startRowIndex": 0, "endRowIndex": 5,
                           "startColumnIndex": 0, "endColumnIndex": 1}
    series = spec["basicChart"]["series"]
    assert len(series) == 2
    assert series[0]["series"]["sourceRange"]["sources"][0]["startColumnIndex"] == 1
    assert series[1]["series"]["sourceRange"]["sources"][0]["startColumnIndex"] == 2
    anchor = req["addChart"]["chart"]["position"]["overlayPosition"]["anchorCell"]
    assert anchor == {"sheetId": 222, "rowIndex": 10, "columnIndex": 0}


def test_line_chart_request_right_axis_cols_separa_escala():
    req = _line_chart_request(
        source_sheet_id=111, n_rows=5, series_cols=[1, 2, 3], domain_col=0,
        title="Teste", dest_sheet_id=222, anchor_row=0, anchor_col=0, width=600, height=300,
        right_axis_cols=[3])
    spec = req["addChart"]["chart"]["spec"]["basicChart"]
    assert [s["targetAxis"] for s in spec["series"]] == ["LEFT_AXIS", "LEFT_AXIS", "RIGHT_AXIS"]
    assert {a["position"] for a in spec["axis"]} == {"BOTTOM_AXIS", "LEFT_AXIS", "RIGHT_AXIS"}
