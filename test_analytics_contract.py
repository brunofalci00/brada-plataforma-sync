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


def _projeto(status, situacao, expira=""):
    """Linha de raw_projects com so o que as reguas de estoque leem."""
    linha = dict.fromkeys(sync.HEADER_PROJECTS, "")
    linha.update(project_hash=status + situacao, status=status,
                 expiracao_situacao=situacao, data_expiracao_cac=expira)
    return [linha[c] for c in sync.HEADER_PROJECTS]


def test_prazo_vencido_e_fila_de_reativacao_e_nao_estoque_disponivel():
    """Os 30 vencidos da medicao de 08/09 estavam escondidos dentro de "Disponiveis".

    O card antigo somava 377 = 347 aptos + 30 vencidos, um numero que nao batia
    nem com o que o incentivador ve (o Matchmaking filtra vencido) nem com o
    reporting. Aqui a separacao e afirmada nos dois sentidos.
    """
    vencido = {"status": "Disponível", "expiracao_situacao": "expirado"}
    assert a.a_reativar(vencido)
    assert not a.apto(vencido)
    assert not a.sem_prazo(vencido)

    apto = {"status": "Em Execução", "expiracao_situacao": "vigente"}
    assert a.apto(apto) and not a.a_reativar(apto)

    # Rascunho vencido era 327 dos 357 do card "Prazo vencido": publicar vem
    # antes de captar, entao ele nao e fila de reativacao de ninguem.
    assert not a.a_reativar({"status": "Rascunho", "expiracao_situacao": "expirado"})
    # Concluido ja terminou: nao volta para a vitrine por renovacao de prazo.
    assert not a.a_reativar({"status": "Concluído", "expiracao_situacao": "expirado"})


def test_valor_novo_de_expiracao_aparece_em_sem_prazo_em_vez_de_sumir():
    assert a.sem_prazo({"status": "Disponível", "expiracao_situacao": "sem_data"})
    assert a.sem_prazo({"status": "Disponível", "expiracao_situacao": "em_analise"})
    assert not a.sem_prazo({"status": "Rascunho", "expiracao_situacao": "sem_data"})


def test_cartoes_de_estoque_somam_o_total_sem_repetir_projeto():
    linhas = ([_projeto("Disponível", "vigente")] * 347
              + [_projeto("Disponível", "expirado")] * 30
              + [_projeto("Em Execução", "vigente")] * 13
              + [_projeto("Concluído", "vigente")]
              + [_projeto("Rascunho", "expirado")] * 327
              + [_projeto("Rascunho", "sem_data")] * 10
              + [_projeto("Rascunho", "vigente")] * 9)
    m = sync.compute_dashboard_metrics([], linhas, [], datetime.datetime(2026, 9, 8, tzinfo=sync.BRT))

    assert m["proj_total"] == 737
    assert m["proj_ativos"] == 360
    assert m["proj_a_reativar"] == 30
    assert m["proj_sem_prazo"] == 0
    assert m["st_rascunho"] == 346
    assert m["st_concluido"] == 1
    # A afirmacao que o texto da aba faz: os cinco grupos cobrem o estoque, cada
    # projeto em exatamente um. Sem isso, mudar um cartao quebra a soma em silencio.
    assert (m["st_rascunho"] + m["st_concluido"] + m["proj_ativos"]
            + m["proj_a_reativar"] + m["proj_sem_prazo"]) == m["proj_total"]
    # "Em execucao" e recorte, e nao grupo: cabe inteiro dentro de quem pode captar.
    assert m["st_em_execucao"] <= m["proj_ativos"]


def test_vencimento_conta_so_quem_pode_captar():
    linhas = [_projeto("Disponível", "vigente", "2026-09-20"),
              _projeto("Rascunho", "vigente", "2026-09-20"),
              _projeto("Disponível", "expirado", "2026-08-01")]
    m = sync.compute_dashboard_metrics([], linhas, [], datetime.datetime(2026, 9, 8, tzinfo=sync.BRT))
    assert m["vence_30d"] == 1


def test_status_sem_acento_nao_zera_cartao_nem_quebra_a_soma():
    """A aba afirma que os cinco grupos somam o estoque; acento nao pode decidir isso.

    Antes, `st.get("Concluído")` lia o campo cru enquanto apto/a_reativar/sem_prazo
    liam por `normal()`. Um "Concluido" sem acento zerava o cartao E furava a soma,
    com os outros tres continuando certos.
    """
    linhas = [_projeto("Concluido", "vigente"),   # sem acento, de proposito
              _projeto("CONCLUÍDO", "vigente"),   # caixa alta
              _projeto("Disponível", "vigente")]
    m = sync.compute_dashboard_metrics([], linhas, [], datetime.datetime(2026, 9, 8, tzinfo=sync.BRT))
    assert m["st_concluido"] == 2
    assert m["proj_fora_dos_grupos"] == 0
    assert (m["st_rascunho"] + m["st_concluido"] + m["proj_ativos"]
            + m["proj_a_reativar"] + m["proj_sem_prazo"]) == m["proj_total"]


def test_status_previsto_que_nao_cabe_em_nenhum_grupo_e_contado_e_nao_ignorado():
    """`Aprovado` esta em KNOWN_PROJECT_STATUS, entao o detector de drift NAO avisa.

    Sem esta conta, ligar esse status no Firestore faria a soma da aba parar de fechar
    sem nada reclamar em lugar nenhum.
    """
    linhas = [_projeto("Disponível", "vigente"),
              _projeto("Aprovado", "vigente"),
              _projeto("Em Elaboração", "sem_data")]
    assert {"Aprovado", "Em Elaboração"} <= sync.KNOWN_PROJECT_STATUS
    m = sync.compute_dashboard_metrics([], linhas, [], datetime.datetime(2026, 9, 8, tzinfo=sync.BRT))
    assert m["proj_fora_dos_grupos"] == 2
    assert (m["st_rascunho"] + m["st_concluido"] + m["proj_ativos"] + m["proj_a_reativar"]
            + m["proj_sem_prazo"] + m["proj_fora_dos_grupos"]) == m["proj_total"]


def _segmentos(snap, metrica):
    """Mapa segmento -> valor de uma metrica dentro do snap de um dia."""
    return {r[2]: r[3] for r in snap if r[1] == metrica}


def test_snap_grava_os_grupos_de_estoque_com_os_mesmos_numeros_dos_cartoes():
    """A trava contra as duas superficies divergirem.

    O cartao de hoje sai de `compute_dashboard_metrics` e a serie historica sai de
    `build_snapshot`. Se cada uma contar por conta propria, um dia a serie do
    "prazo vencido" diz uma coisa e o cartao ao lado dela diz outra — que e o
    mesmo defeito que derrubou o card "Disponiveis" em 08/09/2026.
    """
    linhas = ([_projeto("Disponível", "vigente")] * 5
              + [_projeto("Em Execução", "vigente")] * 2
              + [_projeto("Disponível", "expirado")] * 3
              + [_projeto("Em Execução", "sem_data")]
              + [_projeto("Rascunho", "expirado")] * 4
              + [_projeto("Concluído", "vigente")] * 2)
    snap = sync.build_snapshot([], linhas, [], "2026-09-08")
    grupos = _segmentos(snap, "projects_por_estoque")
    m = sync.compute_dashboard_metrics([], linhas, [], datetime.datetime(2026, 9, 8, tzinfo=sync.BRT))

    assert grupos["apto"] == m["proj_ativos"] == 7
    assert grupos["a_reativar"] == m["proj_a_reativar"] == 3
    assert grupos["sem_prazo"] == m["proj_sem_prazo"] == 1
    assert grupos["rascunho"] == m["st_rascunho"] == 4
    assert grupos["concluido"] == m["st_concluido"] == 2
    assert grupos["fora_dos_grupos"] == m["proj_fora_dos_grupos"] == 0


def test_snap_de_um_dia_reconstroi_o_estoque_sozinho():
    """Olhando so o snapshot de uma data, os cinco grupos fecham o total.

    E o que permite responder "como isso evoluiu no mes" sem ter que cruzar a
    serie com o estado de hoje do Firestore, que nao guarda historico nenhum.
    """
    linhas = ([_projeto("Disponível", "vigente")] * 5
              + [_projeto("Disponível", "expirado")] * 3
              + [_projeto("Rascunho", "")] * 2
              + [_projeto("Concluído", "vigente")])
    snap = sync.build_snapshot([], linhas, [], "2026-09-08")
    grupos = _segmentos(snap, "projects_por_estoque")
    total = _segmentos(snap, "projects_total")["(todos)"]
    assert sum(grupos.values()) == total == len(linhas)


def test_grupo_vazio_grava_zero_em_vez_de_sumir_da_serie():
    """Linha ausente e "dia sem snapshot" para `serie_semanal`, e nao zero.

    Se um grupo so fosse gravado quando tem projeto, o dia em que a fila de
    reativacao zerasse viraria buraco no grafico em vez de virar a boa noticia.
    """
    grupos = _segmentos(sync.build_snapshot([], [], [], "2026-09-08"), "projects_por_estoque")
    assert set(grupos) == set(sync.GRUPOS_ESTOQUE)
    assert all(valor == 0 for valor in grupos.values())


def test_serie_do_vencido_no_snap_nao_conta_rascunho():
    """O gap que a metrica nova fecha.

    A unica serie de expiracao que existia conta sobre TODOS os projetos, rascunho
    incluso — justamente o universo que a decisao de 08/09/2026 desautorizou. Aqui
    11 projetos tem prazo vencido no campo e so 2 sao fila de reativacao.
    """
    linhas = [_projeto("Disponível", "expirado")] * 2 + [_projeto("Rascunho", "expirado")] * 9
    coluna = sync.HEADER_PROJECTS.index("expiracao_situacao")
    assert sum(1 for r in linhas if r[coluna] == "expirado") == 11

    grupos = _segmentos(sync.build_snapshot([], linhas, [], "2026-09-08"), "projects_por_estoque")
    assert grupos["a_reativar"] == 2
    assert grupos["rascunho"] == 9


def test_soma_do_snap_nao_depende_de_acento_nem_de_caixa():
    """Rascunho e concluido sao regravados aqui, e nao lidos de `projects_por_status`.

    La o segmento e o texto CRU do Firestore: um "Concluido" sem acento vira outro
    segmento, a serie do grupo zera e a soma fura, tudo em silencio. Nestes seis a
    leitura passa toda por `normal()`.
    """
    linhas = [_projeto("Concluido", "vigente"), _projeto("CONCLUÍDO", "vigente"),
              _projeto("rascunho", "sem_data"), _projeto("Disponível", "vigente")]
    grupos = _segmentos(sync.build_snapshot([], linhas, [], "2026-09-08"), "projects_por_estoque")
    assert grupos["concluido"] == 2
    assert grupos["rascunho"] == 1
    assert grupos["apto"] == 1
    assert grupos["fora_dos_grupos"] == 0


def test_status_fora_dos_grupos_aparece_no_snap_em_vez_de_furar_a_soma():
    """`Aprovado` e status conhecido, entao o detector de drift nao acusaria."""
    linhas = [_projeto("Disponível", "vigente"), _projeto("Aprovado", "vigente")]
    grupos = _segmentos(sync.build_snapshot([], linhas, [], "2026-09-08"), "projects_por_estoque")
    assert grupos["fora_dos_grupos"] == 1
    assert sum(grupos.values()) == len(linhas)


def test_serie_semanal_le_a_metrica_nova_sem_mudanca():
    now = datetime.datetime(2026, 9, 8, tzinfo=sync.BRT)
    segunda = now.date() - datetime.timedelta(days=now.weekday())
    snap = sync.build_snapshot([], [_projeto("Disponível", "expirado")] * 3, [],
                               now.date().isoformat())

    serie = sync.serie_semanal(snap, "projects_por_estoque", now, "a_reativar")
    assert serie[-1] == (segunda.strftime("%d/%m"), 3)
    assert serie[0][1] == ""  # semana sem snapshot continua indisponivel, nao zero

    # A metrica nao tem "(todos)": o total do estoque e `projects_total`, gravado a
    # parte. Pedir a serie sem segmento devolve vazio em vez de somar grupos.
    assert sync.serie_semanal(snap, "projects_por_estoque", now)[-1][1] == ""
