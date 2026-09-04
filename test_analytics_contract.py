import datetime
import json
from pathlib import Path

import analytics_contract as a
import sync


def fixture():
    return json.loads((Path(__file__).parent / "analytics_contract.fixture.json").read_text(encoding="utf-8"))


def test_shared_funnel_contract():
    f = fixture()
    assert a.funil_pessoas(f["users"], f["projects"]) == f["expected"]


def test_test_proposals_and_missing_deadline():
    assert a.proposta_teste({"edital": "Edital de Testes"})
    assert not a.proposta_teste({"edital": "Edital Testemunha"})
    assert not a.apto({"status": "Disponível", "expiracao_situacao": "sem_data"})


def test_recompute_old_leads_even_without_new_pull_and_multiple_users():
    leads = [{"lead_id": "old", "cadastrou_plataforma": "nao", "tem_projeto_plataforma": "nao"}]
    users = [{"utm_term": "old", "origem_canal": "automatize", "tem_projeto": "sim", "data_cadastro": "2026-08-01"},
             {"utm_term": "old", "origem_canal": "automatize", "tem_projeto": "nao", "data_cadastro": "2026-09-01"}]
    result = a.merge_leads(leads, [], users)
    assert result[0]["cadastrou_plataforma"] == "sim"
    assert result[0]["tem_projeto_plataforma"] == "sim"
    assert result[0]["data_cadastro_plataforma"] == "2026-08-01"
    assert a.merge_leads(result, [], users) == result
    assert a.merge_leads(leads, [], [{**users[0], "origem_canal": "outro"}])[0]["cadastrou_plataforma"] == "nao"


def test_observations_never_retrodate_or_regress():
    p = {"project_hash": "p", "status": "Disponível", "expiracao_situacao": "vigente"}
    first = a.observe_projects([p], [], "2026-09-04T10:00:00-03:00")
    next_rows = a.observe_projects([{**p, "expiracao_situacao": "expirado"}], first, "2026-09-05T10:00:00-03:00")
    assert next_rows[0]["primeira_aptidao_observada"] == first[0]["primeira_aptidao_observada"]
    assert not a.apto(next_rows[0])


def test_health_preserves_last_success_on_failure():
    health = a.source_health({"automatize_ultima_ingestao": "2026-09-01"}, False, "2026-09-04", {"http_502": 1})
    assert health["automatize_ultima_ingestao"] == "2026-09-01"
    assert health["automatize_estado"] == "indisponivel"
    assert a.source_health({}, True, "today", {})["automatize_cobertura"] == "nao_comprovada"


def test_partial_source_aborts():
    import pytest
    with pytest.raises(ValueError, match="Fonte parcial"):
        a.validate_source_counts({"n_users": 20, "n_projects": 20}, {"n_users": 100, "n_projects": 100})


def test_replace_clears_tail_atomically_and_never_treats_text_as_formula():
    request = a.atomic_replace_request(12, 100, 3, ["id", "value"], [["=not_a_formula", 0]])["updateCells"]
    assert request["range"]["endRowIndex"] == 100
    assert request["fields"] == "userEnteredValue"
    assert request["rows"][1]["values"][0]["userEnteredValue"] == {"stringValue": "=not_a_formula"}
    assert request["rows"][1]["values"][1]["userEnteredValue"] == {"numberValue": 0}


def test_no_snapshot_is_not_zero():
    now = datetime.datetime(2026, 9, 4, tzinfo=sync.BRT)
    series = sync.serie_semanal([], "users_ativos_30d", now)
    assert all(value == "" for _, value in series)
    assert sync.serie_semanal([["2026-09-04", "users_ativos_30d", "(todos)", 0]], "users_ativos_30d", now)[-1][1] == 0


def test_unknown_origin_is_not_organic():
    assert sync.origem_canal("", False) == "sem_atribuicao"
    assert sync.origem_canal("", True) == "migrado"


def test_money_and_snapshot_exclude_test_data():
    row = ["test", "p", "Edital Teste", "aprovado", "", 50000, "2026-09-01", "2026-09", ""]
    metrics = sync.compute_dashboard_metrics([], [], [row], datetime.datetime(2026, 9, 4, tzinfo=sync.BRT))
    assert metrics["prop_aprovadas"] == 0
    assert metrics["prop_valor"] == 0
    snap = sync.build_snapshot([], [], [row], "2026-09-04")
    assert [r[3] for r in snap if r[1] == "valor_aprovado_total"] == [0]


def test_missing_approved_value_is_not_zero():
    assert a.valor_aprovado([{'edital': 'real', 'status': 'aprovado', 'valor_aprovado': ''}]) == ''
    assert a.valor_aprovado([]) == 0


def test_layout_request_rectangles_and_visible_rows():
    import dashboard_analytics as d
    from collections import defaultdict
    m = defaultdict(int)
    m.update(funil={k: 0 for k in fixture()['expected']}, automatize={k: 0 for k in fixture()['expected']}, fontes=a.source_health({}, False, '2026-09-04', {}),
             canais={}, semanas=[], semanas_ativos=[], mes_atual_rotulo='Parcial', mes_anterior_rotulo='Completo')
    reqs = d.requests(1, m, datetime.datetime(2026, 9, 4), {'sheets': [{'properties': {'sheetId': 1, 'gridProperties': {'rowCount': 100}}}]}, True)
    assert any(r.get('updateDimensionProperties', {}).get('properties', {}).get('hiddenByUser') is False for r in reqs)
    for r in reqs:
        if 'updateCells' in r:
            v = r['updateCells']
            area = v['range']
            assert len(v['rows']) == area['endRowIndex'] - area['startRowIndex']
            assert all(len(row['values']) == area['endColumnIndex'] - area['startColumnIndex'] for row in v['rows'])
