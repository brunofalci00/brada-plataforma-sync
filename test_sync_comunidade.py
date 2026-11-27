import datetime

import ga_analytics
import sync_comunidade as sc

AGORA = int(datetime.datetime(2026, 11, 10, 12, tzinfo=datetime.timezone.utc).timestamp() * 1000)
DIA = sc.DIA_MS


def _dados(**extra):
    base = {
        "users": [
            {"role": "explorador", "onboardingCompleted": True, "createdAt": "2026-11-02T10:00:00Z"},
            {"role": "idealizador", "onboardingCompleted": False, "createdAt": "2026-10-20T10:00:00Z"},
            {"role": "explorador", "isBradaTeam": True, "createdAt": "2026-11-01"},
            {"role": "idealizador", "email": "Fulano@Brada.social", "createdAt": "2026-11-01"},
            {"role": "admin", "createdAt": "2026-11-01"},
        ],
        "auth": [
            {"email": "a@x.com", "createdAt": AGORA - 20 * DIA, "lastLoginAt": AGORA - 2 * DIA},
            {"email": "b@x.com", "createdAt": AGORA - 10 * DIA, "lastLoginAt": AGORA - 10 * DIA},
            {"email": "c@brada.social", "createdAt": AGORA - 30 * DIA, "lastLoginAt": AGORA},
        ],
        "activation_events": [
            {"event": "first_relevant_action", "actionType": "event_external_click", "timestamp": "2026-11-03T01:00:00Z"},
            {"event": "profile_completed", "timestamp": "2026-11-03T02:00:00Z"},
        ],
        "community_posts": [], "user_connections": [{"status": "accepted"}, {"status": "pending"}],
        "articles": [{"status": "published", "views": 10}, {"status": "pending_approval"}],
        "courses": [{"enrolledUsersCount": "5"}, {"enrolledUsersCount": "0"}],
        "opportunities": [{"status": "ABERTO"}, {"status": "breve"}], "events": [{}], "direct_messages": [],
    }
    base.update(extra)
    return base


def test_equipe_fica_fora_de_membros_e_logins():
    m = sc.calcular(_dados(), AGORA)
    assert (m["membros"], m["equipe"]) == (2, 3)
    assert m["login_7d"] == 1 and m["login_30d"] == 2


def test_taxas_e_contagens():
    m = sc.calcular(_dados(), AGORA)
    assert m["onboarding"] == 0.5
    assert m["voltaram_7d"] == 0.5  # 2 elegiveis, 1 voltou 18 dias depois
    assert m["cadastros_mes"] == 1
    assert (m["editais_abertos"], m["editais_breve"]) == (1, 1)
    assert (m["inscritos_cursos"], m["cursos_com_inscritos"]) == (5, 1)
    assert m["acao_evento"] == 1


def test_base_vazia_nao_divide_por_zero():
    vazio = {k: [] for k in _dados()}
    m = sc.calcular(vazio, AGORA)
    assert m["onboarding"] == 0 and m["voltaram_7d"] == 0


AGORA_DT = datetime.datetime(2026, 11, 10, 12, tzinfo=datetime.timezone.utc)


def _componente(painel, titulo):
    return next(c for linha in painel.linhas for c in linha if getattr(c, "titulo", "") == titulo)


def test_diario_sem_equipe():
    linhas = sc.diario(_dados())
    # perfil completo conta a conta no dia do cadastro; o evento profile_completed de 03/11 nao entra
    assert ["2026-11-03", 0, 1, 0] in linhas
    assert ["2026-11-02", 1, 0, 1] in linhas and ["2026-10-20", 1, 0, 0] in linhas
    assert not any(l[0] == "2026-11-01" for l in linhas)


def test_por_semana_soma_cadastros_e_acoes_na_semana_certa():
    linhas = [["2026-11-02", 2, 1, 0], ["2026-11-08", 3, 0, 0], ["2026-11-09", 1, 4, 0], ["lixo", 9, 9, 9]]
    semanas = dict((s, (c, a)) for s, c, a in sc.por_semana(linhas, datetime.date(2026, 11, 10), n=2))
    assert semanas == {"02/11": (5, 1), "09/11": (1, 4)}


def test_base_conta_cadastro_e_eventos_por_perfil_sem_equipe():
    dados = {"users": [{"createdAt": "2026-11-10T10:00:00", "role": "idealizador", "onboardingCompleted": True},
                       {"createdAt": "2026-11-10T09:00:00", "role": "idealizador", "onboardingCompleted": False},
                       {"createdAt": "2026-11-10T11:00:00", "role": "admin", "onboardingCompleted": True}],
             "activation_events": [{"event": "first_relevant_action", "timestamp": "2026-11-11T09:00:00",
                                    "role": "idealizador"},
                                   {"event": "profile_completed", "timestamp": "2026-11-11T09:00:00"},
                                   {"event": "outro", "timestamp": "2026-11-11T09:00:00"}]}
    linhas = sc.linhas_base(dados)
    por = {(l[0], l[1]): l[2:] for l in linhas}
    import painel_filtros as fl
    d10, d11 = fl.serial(datetime.date(2026, 11, 10)), fl.serial(datetime.date(2026, 11, 11))
    assert por[(d10, "Idealizador")] == [2, 1, 0]  # 2 cadastros, 1 perfil completo, a equipe fora
    assert por[(d11, "Idealizador")] == [0, 0, 1]
    assert (d11, "Sem perfil") not in por  # o evento profile_completed nao conta mais


def test_painel_comeca_pelos_controles():
    m = sc.calcular(_dados(), AGORA)
    painel, _ = sc.montar_painel(m, [], AGORA_DT, sc.linhas_base(_dados()))
    import dashboard_painel as dp
    assert isinstance(painel.linhas[0][0], dp.Controles)


def test_painel_sem_ga_tem_jornada_que_nunca_sobe():
    m = sc.calcular(_dados(), AGORA)
    painel, resumo = sc.montar_painel(m, sc.diario(_dados()), AGORA_DT)
    barras = _componente(painel, "O que os membros ja fizeram (foto de hoje, pessoas)").linhas
    assert barras[0] == ["Cadastraram", 2] and not any("acao" in b[0] for b in barras)
    assert resumo["comunidade_membros"] == 2 and "comunidade_visitantes_30d" not in resumo


GA = {
    "propriedade": "1",
    "totais": [([], [100.0, 150.0, 400.0])],
    "novo_recorrente": [(["new"], [80.0]), (["returning"], [25.0]), (["(not set)"], [0.0])],
    "paginas": [(["Entrar ou Criar Conta | Brada"], [50.0]), (["(not set)"], [9.0]),
                (["Eventos | Brada"], [30.0]), (["A"], [3.0]), (["B"], [2.0]), (["C"], [1.0])],
}


def test_resumo_ga_retorno_e_top_paginas():
    r = sc.resumo_ga(GA)
    assert (r["ativos"], r["recorrentes"], r["taxa_retorno"]) == (100.0, 25.0, 0.25)
    assert r["top"][:2] == [(50.0, "Entrar ou Criar Conta"), (30.0, "Eventos")]
    assert len(r["top"]) == ga_analytics.TOP_PAGINAS


def test_painel_com_ga_tem_tabela_de_telas_e_retorno_no_resumo():
    m = sc.calcular(_dados(ga=GA), AGORA)
    painel, resumo = sc.montar_painel(m, [], AGORA_DT)
    tabela = _componente(painel, "Telas mais vistas (30 dias)")
    assert tabela.linhas[0] == ["Entrar ou Criar Conta", 50.0]
    assert resumo["comunidade_retorno"] == 0.25
    assert sc.resumo_ga(None) is None


def test_primeira_acao_conta_uma_por_membro_quando_o_evento_traz_o_userid():
    dados = {"users": [{"_id": "u1", "role": "idealizador"}, {"_id": "eq", "role": "admin"}],
             "activation_events": [
                 {"event": "first_relevant_action", "timestamp": "2026-11-24T10:00:00Z", "userId": "u1"},
                 {"event": "first_relevant_action", "timestamp": "2026-11-24T11:00:00Z", "userId": "u1"},
                 {"event": "first_relevant_action", "timestamp": "2026-11-24T12:00:00Z", "userId": "eq"},
                 {"event": "first_relevant_action", "timestamp": "2026-11-20T12:00:00Z"}]}
    acoes = sc.primeiras_acoes(dados)
    assert len(acoes) == 2  # u1 uma vez, a equipe fora, o evento antigo sem membro fica
    assert ("idealizador" in [p for _, p in acoes])


def test_visitante_deslogado_nao_conta_como_primeira_acao():
    dados = {"users": [], "activation_events": [
        {"event": "first_relevant_action", "timestamp": "2026-11-24T10:00:00Z", "userId": None, "role": "visitante"},
        {"event": "first_relevant_action", "timestamp": "2026-11-24T10:05:00Z", "userId": "", "role": ""},
        {"event": "first_relevant_action", "timestamp": "2026-11-20T10:00:00Z"}]}
    assert len(sc.primeiras_acoes(dados)) == 1  # so o evento antigo, sem a chave userId


def test_voltou_conta_uso_depois_do_dia_do_cadastro():
    import datetime
    dia = 86400000
    agora = int(datetime.datetime(2026, 11, 24, tzinfo=datetime.timezone.utc).timestamp() * 1000)
    so_cadastro = {"createdAt": str(agora - 5 * dia), "lastRefreshAt": datetime.datetime.fromtimestamp(
        (agora - 5 * dia + 3600000) / 1000, datetime.timezone.utc).isoformat()}
    voltou = {"createdAt": str(agora - 10 * dia), "lastRefreshAt": "2026-11-23T10:00:00Z"}
    assert not sc.voltou_30d(so_cadastro, agora) and sc.voltou_30d(voltou, agora)
    assert not sc.voltou_30d({"createdAt": "0"}, agora)


def test_voltou_pelo_last_active_at_do_app_num_dia_seguinte():
    agora = int(datetime.datetime(2026, 11, 25, 15, tzinfo=datetime.timezone.utc).timestamp() * 1000)
    cadastro = int(datetime.datetime(2026, 11, 25, 1, tzinfo=datetime.timezone.utc).timestamp() * 1000)  # 24/11 22h BRT
    conta = {"createdAt": str(cadastro)}
    assert not sc.voltou_30d(conta, agora)
    assert not sc.voltou_30d(conta, agora, "2026-11-25T02:00:00Z")  # mesmo dia de Brasilia
    assert sc.voltou_30d(conta, agora, "2026-11-25T11:00:00Z")  # dia seguinte, menos de 24h depois


def test_calcular_le_last_active_at_dos_usuarios():
    agora = int(datetime.datetime(2026, 11, 25, 15, tzinfo=datetime.timezone.utc).timestamp() * 1000)
    criado = str(agora - 5 * sc.DIA_MS)
    vazias = ["activation_events", "community_posts", "user_connections", "articles",
              "courses", "opportunities", "events", "direct_messages"]
    dados = {"users": [{"_id": "u1", "lastActiveAt": "2026-11-25T11:00:00Z"}, {"_id": "u2"}],
             "auth": [{"localId": "u1", "createdAt": criado}, {"localId": "u2", "createdAt": criado}],
             "ga": None, **{n: [] for n in vazias}}
    assert sc.calcular(dados, agora)["voltaram_30d"] == 1


def test_perfil_completo_vem_da_conta_e_nao_do_evento():
    assert sc.perfil_completo({"onboardingCompleted": True})
    assert not sc.perfil_completo({"onboardingCompleted": False}) and not sc.perfil_completo({})
    assert not sc.perfil_completo({"onboardingCompleted": "true"})  # so booleano verdadeiro


def test_sem_b_antes_esvazia_a_comparacao_quando_b_comeca_antes_da_fonte():
    import painel_filtros as fl
    ctl = fl.controles(sc.ABA, AGORA_DT.date())
    cartao = sc.sem_b_antes(fl.CartaoVivo("Visitas", "A1", "B1"), ctl, sc.INICIO_GA)
    assert cartao.fb == f"IF({ctl.ref('de_B')}<{fl.serial(sc.INICIO_GA)};0;B1)"
    assert cartao.fa == "A1" and cartao.titulo == "Visitas"
    sem = fl.CartaoVivo("Posicao", "A1", None)
    assert sc.sem_b_antes(sem, ctl, sc.INICIO_GA) is sem  # cartao sem comparacao passa direto


WEB = {"ga": [(datetime.date(2026, 11, 5), "Direto", [10, 5, 3, 0])],
       "gsc": [(datetime.date(2026, 9, 10), "Generica", 100, 2, 5.0)],
       "top": [], "paginas": [], "consultas": [], "erros": []}


def test_cartoes_do_ga_nao_comparam_antes_de_01_11_e_busca_segue_comparando():
    import painel_filtros as fl
    m = sc.calcular(_dados(), AGORA)
    painel, _ = sc.montar_painel(m, [], AGORA_DT, sc.linhas_base(_dados()), WEB)
    serial = str(fl.serial(sc.INICIO_GA))
    ga = [_componente(painel, t) for t in ("Visitas medidas pelo Google", "Visitantes novos",
                                            "Visitas com interacao")]
    assert all(c.fb.startswith("IF(") and f"<{serial};0;" in c.fb for c in ga)
    busca = _componente(painel, "Aparicoes no Google")
    assert f"<{serial};0;" not in busca.fb  # Search Console tem dado desde 09/2026
    acoes = _componente(painel, "Primeiras acoes no app (eventos, nao pessoas)")
    assert f"<{fl.serial(sc.INICIO_ACOES)};0;" in acoes.fb
    cadastros = _componente(painel, "Cadastros no periodo")
    assert not cadastros.fb.startswith("IF(")  # cadastros existem desde 07/2026
    taxa = _componente(painel, "Perfis completos por cadastro")
    assert taxa.fb.startswith("IF(SUMIFS(") and f"<{fl.MIN_BASE_COMPARACAO};0;" in taxa.fb  # base B minuscula
    titulos = [getattr(c, "titulo", "") for linha in painel.linhas for c in linha]
    assert titulos.index("Visitas medidas pelo Google") < titulos.index("Aparicoes no Google")
