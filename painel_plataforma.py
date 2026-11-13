"""Aba `Plataforma Brada` no formato de painel (substitui o grid de cartoes de dashboard_analytics.py).

Recebe o que o sync.py ja calculou em memoria (metrics, users/projects como
records, serie diaria) e acrescenta o funil do SDR de aptos: disparo da Automatize
(`modalidade = aprovado_selfservice`) -> abriu -> clicou ou respondeu -> cadastro
na plataforma -> criou projeto -> projeto apto hoje. A ligacao lead -> cadastro e
o `utm_term` do usuario, que a Automatize preenche com o lead_id no link.
"""

import datetime

import analytics_contract as analytics
import dashboard_painel as dp
import painel_filtros as fl

ABA = "Plataforma Brada"
BASE_SDR = fl.Base("base_plataforma_sdr", [("canal", "Canal", fl.TODOS), ("fonte", "Fonte", fl.TODAS)], [
    ("disparados", "Disparados"), ("entregues", "Entregues"), ("abriram", "Abriram"),
    ("engajaram", "Clicaram ou responderam"), ("cadastro", "Cadastro"), ("projeto", "Criaram projeto"),
    ("apto", "Projeto apto hoje")])
FUNIL_SDR = [("Disparados", "disparados"), ("Entregues", "entregues"), ("Abriram", "abriram"),
             ("Clicaram ou responderam", "engajaram"), ("Cadastro na plataforma", "cadastro"),
             ("Criaram projeto", "projeto"), ("Projeto apto hoje", "apto")]
BASE_CONTAS = fl.Base("base_plataforma_contas", [("origem", "Origem", fl.TODAS)], [
    ("contas", "Contas novas"), ("criaram", "Criaram projeto"), ("publicaram", "Publicaram"),
    ("aptos", "Aptos hoje")])
FUNIL_CONTAS = [("Criaram conta", "contas"), ("Criaram projeto", "criaram"), ("Publicaram", "publicaram"),
                ("Projeto apto hoje", "aptos")]
CANAIS_SDR = ["E-mail", "WhatsApp", "E-mail + WhatsApp"]

CANAIS = [("automatize", "Automatize"), ("leadlovers", "LeadLovers"), ("meta_ads", "Meta Ads"),
          ("instagram", "Instagram"), ("comercial", "Comercial"), ("site", "Site"), ("outro", "Outro"),
          ("sem_atribuicao", "Sem origem")]


def completar_aptos(leads, user_records, project_records):
    por_term = {}
    for u in user_records:
        termo = (u.get("utm_term") or "").strip().lower()
        if termo:
            por_term.setdefault(termo, u)
    donos_aptos = {p.get("owner_hash") for p in project_records if analytics.apto(p)}
    for lead_id, l in leads.items():
        u = por_term.get(lead_id.lower())
        l["apto"] = bool(u) and u.get("user_hash") in donos_aptos
        l["projeto"] = l["apto"] or (bool(u) and u.get("tem_projeto") == "sim")
        l["cadastro"] = l["cadastro"] or bool(u)
        l["engajou"] = l["clicou"] or l["respondeu"] or l["cadastro"]
        l["abriu"] = l["abriu"] or l["engajou"]
    return leads


def funil_aptos(leads):
    v = list(leads.values())
    return [("Disparados", len(v)), ("Abriram", sum(l["abriu"] for l in v)),
            ("Clicaram ou responderam", sum(l["engajou"] for l in v)),
            ("Cadastro na plataforma", sum(l["cadastro"] for l in v)),
            ("Criaram projeto", sum(l["projeto"] for l in v)),
            ("Projeto apto hoje", sum(l["apto"] for l in v))]


def linhas_sdr(leads):
    """Um registro por lead na data do 1o disparo; etapas cumulativas."""
    import sdr_automatize as sdra
    return BASE_SDR.agregar(
        (l["dia"], {"canal": sdra.canal_do_lead(l["canais"]), "fonte": l.get("fonte")},
         {"disparados": 1, "entregues": l["abriu"] or not l["bounce"], "abriram": l["abriu"],
          "engajaram": l["engajou"], "cadastro": l["cadastro"], "projeto": l["projeto"], "apto": l["apto"]})
        for l in leads.values())


def linhas_contas(user_records, project_records):
    """Contas novas de ONG (sem migrados) na data do cadastro, por origem; projeto/publicou/apto = foto de hoje."""
    rotulo = dict(CANAIS)
    projetos = {}
    for pr in project_records:
        projetos.setdefault(pr.get("owner_hash"), []).append(pr)

    def registro(u):
        meus = projetos.get(u.get("user_hash"), [])
        dia = datetime.date.fromisoformat(str(u["data_cadastro"])[:10])
        return (dia, {"origem": rotulo.get(u.get("origem_canal"), "Sem origem")},
                {"contas": 1, "criaram": bool(meus), "publicaram": any(analytics.publicado(x) for x in meus),
                 "aptos": any(analytics.apto(x) for x in meus)})

    return BASE_CONTAS.agregar(
        registro(u) for u in user_records
        if u.get("user_hash") and str(u.get("data_cadastro") or "")[:10]
        and analytics.normal(u.get("role")) == "ong" and analytics.normal(u.get("is_migrado")) == "nao")


def coletar_sdr_aptos(user_records, project_records):
    """(leads, envios) do SDR de aptos, ou (None, None) se o banco nao responder."""
    import sdr_automatize as sdra
    try:
        conn = sdra.conectar()
        try:
            envios = sdra.envios(conn, sdra.APTOS)
        finally:
            conn.close()
    except (Exception, SystemExit) as e:  # credencial ausente ou banco fora: a aba sai sem o funil
        print(f"[plataforma] SDR de aptos indisponivel: {str(e)[:160]}")
        return None, None
    return completar_aptos(sdra.por_lead(envios), user_records, project_records), envios


def _valor_em(tendencia_rows, col, dia):
    """Valor da serie diaria no dia pedido ou no ultimo dia anterior com dado."""
    alvo = dia.isoformat()
    for r in reversed(tendencia_rows):
        if r[0] <= alvo and r[col] not in ("", None):
            return r[col]
    return None


def _tendencia(tendencia_rows, dias=60):
    linhas = [r for r in tendencia_rows if r[1] not in ("", None) and r[2] not in ("", None)][-dias:]
    return [[datetime.date.fromisoformat(r[0]).strftime("%d/%m"), r[1], r[2]] for r in linhas]


def _controles(hoje, sdr_linhas, contas_linhas):
    canais = [c for c in CANAIS_SDR if c in BASE_SDR.opcoes(sdr_linhas, "canal")] or CANAIS_SDR[:1]
    return fl.controles(ABA, hoje, [
        fl.campo_lista("canal", "Canal da Brenda", canais, todos=fl.TODOS, span=2),
        fl.campo_lista("fonte", "Fonte do lead", BASE_SDR.opcoes(sdr_linhas, "fonte"), span=2),
        fl.campo_lista("origem", "Origem da conta", BASE_CONTAS.opcoes(contas_linhas, "origem"), span=2)]), canais


def montar_painel(m, tendencia_rows, leads_sdr, envios_sdr, agora, user_records=(), project_records=()):
    """Filtros no topo; Brenda (disparo ao projeto apto) e todas as origens (conta ao projeto apto),
    vivos pelo periodo; depois a foto de hoje (estoque, prazo, acesso, site), sem filtro."""
    hoje = agora.date()
    ha_30 = hoje - datetime.timedelta(days=30)
    sdr_linhas = linhas_sdr(leads_sdr) if leads_sdr is not None else []
    contas_linhas = linhas_contas(user_records, project_records)
    origens = BASE_CONTAS.opcoes(contas_linhas, "origem")
    ctl, canais = _controles(hoje, sdr_linhas, contas_linhas)
    fsdr, fcontas = fl.Filtros(ctl, BASE_SDR), fl.Filtros(ctl, BASE_CONTAS)
    p = dp.Painel(ABA, "Plataforma Brada · da Brenda e de todas as origens ao projeto apto",
                  f"Atualizado {agora.strftime('%d/%m/%Y %H:%M')} · Firestore da plataforma + banco do SDR Brada · "
                  "mude as células claras abaixo")
    p.linha(ctl)
    p.linha(fl.CartaoVivo("Brenda: leads disparados", fsdr.soma_p("disparados", "A"), fsdr.soma_p("disparados", "B")),
            fl.CartaoVivo("Brenda: cadastros", fsdr.soma_p("cadastro", "A"), fsdr.soma_p("cadastro", "B")),
            fl.CartaoVivo("Contas novas (todas as origens)", fcontas.soma_p("contas", "A"),
                          fcontas.soma_p("contas", "B")),
            fl.CartaoVivo("Conta ate projeto apto", fcontas.taxa("aptos", "A"), fcontas.taxa("aptos", "B"),
                          dp.FMT_PCT))
    p.linha(fl.FunilVivo("Brenda: do disparo ao projeto apto", fsdr, FUNIL_SDR, altura=360,
                         subtitulo="Leads na data do 1o disparo · filtros de canal e fonte"),
            fl.FunilVivo("Todas as origens: da conta ao projeto apto", fcontas, FUNIL_CONTAS, altura=360,
                         subtitulo="Sem migrados · contas na data do cadastro · filtro de origem"))
    p.linha(fl.evolucao("Brenda: leads disparados no periodo A", fsdr, "disparados", "canal", canais,
                        subtitulo="Por dia ou semana, empilhado por canal"),
            fl.evolucao("Contas novas no periodo A", fcontas, "contas", "origem", origens,
                        subtitulo="Por dia ou semana, empilhado por origem"))
    recentes = hoje - datetime.timedelta(days=2 * fl.JANELA_PADRAO - 1)
    p.linha(fl.por_dimensao("Brenda por fonte do lead (periodo A)", fsdr, "fonte",
                            BASE_SDR.opcoes(sdr_linhas, "fonte", desde=recentes), [
                                ("Disparados", "soma", "disparados"), ("Abertura", "taxa", "abriram"),
                                ("Engajamento", "taxa", "engajaram"), ("Cadastros", "soma", "cadastro"),
                                ("Taxa cadastro", "taxa", "cadastro"), ("Aptos", "soma", "apto")],
                            gradiente=[1, 5], cabecalho_dim="Fonte"))
    p.linha(fl.por_dimensao("Contas novas por origem (periodo A)", fcontas, "origem", origens, [
        ("Contas", "soma", "contas"), ("Criaram projeto", "taxa", "criaram"), ("Publicaram", "taxa", "publicaram"),
        ("Aptos", "soma", "aptos"), ("Conta ate apto", "taxa", "aptos")], gradiente=[1, 5], cabecalho_dim="Origem"))

    # Foto de hoje: estado atual, nao muda com o filtro.
    ga = m.get("ga")
    p.linha(dp.Cartao("Projetos aptos a captar (hoje)", m["proj_ativos"],
                      base=_valor_em(tendencia_rows, 1, ha_30), descricao_base="vs 30 dias atras"),
            dp.Cartao("Acessaram em 30 dias", m["ativos_30d"],
                      base=_valor_em(tendencia_rows, 2, ha_30), descricao_base="vs 30 dias atras"),
            dp.Cartao("Prazo vencido, a reativar", m["proj_a_reativar"]),
            dp.Cartao("Visitantes no site (30 dias)", ga["ativos"] if ga else 0))
    p.linha(dp.Rosca("Estoque de projetos (foto de hoje)", [
                ("Aptos a captar", m["proj_ativos"]), ("Prazo vencido", m["proj_a_reativar"]),
                ("Sem prazo", m["proj_sem_prazo"]), ("Rascunho", m["st_rascunho"]),
                ("Concluido", m["st_concluido"])], altura=340),
            dp.Serie("Aptos que perdem o prazo (foto de hoje)", ["Quando", "Projetos"], [
                ["Ate 30 dias", m["vence_30d"]], ["31 a 60", m["vence_60d"] - m["vence_30d"]],
                ["61 a 90", m["vence_90d"] - m["vence_60d"]], ["91 a 180", m["vence_180d"] - m["vence_90d"]]]))
    p.linha(dp.Serie("Aptos a captar e usuarios ativos", ["Dia", "Aptos a captar", "Ativos em 30 dias"],
                     _tendencia(tendencia_rows), tipo="LINE", rotulos=False, subtitulo="Ultimos 60 dias"))
    if ga:
        p.linha(dp.Tabela("Paginas mais vistas no site (30 dias)", ["Pagina", "Visualizacoes"],
                          [[nome, vistas] for vistas, nome in ga["top"]], formatos=[None, dp.FMT_INT],
                          gradiente=[1]))

    resumo = {"plataforma_aptos": m["proj_ativos"], "plataforma_cadastros_mes": m["novos_mes"],
              "plataforma_ativos_30d": m["ativos_30d"], "plataforma_a_reativar": m["proj_a_reativar"],
              "plataforma_novos_aptos": m["funil_aptos"], "plataforma_novos_cadastros": m["funil_cadastraram"]}
    if leads_sdr is not None:
        etapas = dict(funil_aptos(leads_sdr))
        resumo.update({"aptos_sdr_disparados": etapas["Disparados"],
                       "aptos_sdr_cadastros": etapas["Cadastro na plataforma"],
                       "aptos_sdr_aptos": etapas["Projeto apto hoje"]})
    if ga:
        resumo.update({"plataforma_visitantes_30d": ga["ativos"], "plataforma_retorno": ga["taxa_retorno"]})
    return p, resumo, [(BASE_SDR, sdr_linhas), (BASE_CONTAS, contas_linhas)]


def publicar(sh, svc, spreadsheet_id, metrics, user_records, project_records, tendencia_rows, agora):
    leads, envios = coletar_sdr_aptos(user_records, project_records)
    painel, resumo, bases = montar_painel(metrics, tendencia_rows, leads, envios, agora,
                                          user_records, project_records)
    for base, linhas in bases:
        base.publicar(sh, svc, spreadsheet_id, linhas)
    painel.publicar(sh, svc, spreadsheet_id)
    dp.gravar_resumo(sh, svc, spreadsheet_id, resumo, agora)
    return resumo
