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

ABA = "Plataforma Brada"

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


def montar_painel(m, tendencia_rows, leads_sdr, envios_sdr, agora):
    import sdr_automatize as sdra
    hoje = agora.date()
    ha_30 = hoje - datetime.timedelta(days=30)
    p = dp.Painel(ABA, "Plataforma Brada · aquisicao e oferta",
                  f"Atualizado {agora.strftime('%d/%m/%Y %H:%M')} · Firestore da plataforma + banco do SDR Brada")
    p.linha(dp.Cartao("Projetos aptos a captar", m["proj_ativos"],
                      base=_valor_em(tendencia_rows, 1, ha_30), descricao_base="vs 30 dias atras"),
            dp.Cartao("Cadastros novos no mes", m["novos_mes"]),
            dp.Cartao("Acessaram em 30 dias", m["ativos_30d"],
                      base=_valor_em(tendencia_rows, 2, ha_30), descricao_base="vs 30 dias atras"),
            dp.Cartao("Prazo vencido, a reativar", m["proj_a_reativar"]))

    estoque = dp.Rosca("Estoque de projetos", [
        ("Aptos a captar", m["proj_ativos"]), ("Prazo vencido", m["proj_a_reativar"]),
        ("Sem prazo", m["proj_sem_prazo"]), ("Rascunho", m["st_rascunho"]), ("Concluido", m["st_concluido"])],
        altura=340)
    funil_novos = dp.Funil("Novos proponentes", [
        ("Cadastraram", m["funil_cadastraram"]), ("Criaram projeto", m["funil_com_projeto"]),
        ("Publicaram", m["funil_publicaram"]), ("Aptos hoje", m["funil_aptos"])],
        subtitulo="Sem migrados · % sobre quem cadastrou", altura=340)
    if leads_sdr is not None:
        p.linha(dp.Funil("SDR de aptos (Automatize)", funil_aptos(leads_sdr), altura=340), estoque)
        ultimo = sdra.ultimo_disparo(envios_sdr)
        p.linha(dp.Serie("Disparos do SDR de aptos por dia", ["Dia", "E-mail", "WhatsApp"],
                         sdra.por_dia(envios_sdr, hoje, 30), empilhado=True, rotulos=False,
                         subtitulo=f"Ultimos 30 dias · ultimo disparo {ultimo.strftime('%d/%m') if ultimo else '-'}"),
                funil_novos)
    else:
        p.linha(funil_novos, estoque)

    p.linha(dp.Serie("Aptos a captar e usuarios ativos", ["Dia", "Aptos a captar", "Ativos em 30 dias"],
                     _tendencia(tendencia_rows), tipo="LINE", rotulos=False, subtitulo="Ultimos 60 dias"),
            dp.Serie("Cadastros novos por semana", ["Semana", "Cadastros"], m["semanas"]))
    canais = sorted(((rot, m["canais"].get(k, 0)) for k, rot in CANAIS), key=lambda x: -x[1])
    p.linha(dp.Serie("Origem dos cadastros novos", ["Canal", "Pessoas"], [[c, n] for c, n in canais if n],
                     tipo="BAR", subtitulo="Sem migrados, desde o lancamento"),
            dp.Serie("Aptos que perdem o prazo", ["Quando", "Projetos"], [
                ["Ate 30 dias", m["vence_30d"]], ["31 a 60", m["vence_60d"] - m["vence_30d"]],
                ["61 a 90", m["vence_90d"] - m["vence_60d"]], ["91 a 180", m["vence_180d"] - m["vence_90d"]]]))

    ga = m.get("ga")
    if ga:
        p.linha(dp.Cartao("Visitantes no site (30 dias)", ga["ativos"]),
                dp.Cartao("Parte que voltou", ga["taxa_retorno"], dp.FMT_PCT),
                dp.Cartao("Paginas vistas (30 dias)", ga["paginas_vistas"]))
        p.linha(dp.Tabela("Paginas mais vistas (30 dias)", ["Pagina", "Visualizacoes"],
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
    return p, resumo


def publicar(sh, svc, spreadsheet_id, metrics, user_records, project_records, tendencia_rows, agora):
    leads, envios = coletar_sdr_aptos(user_records, project_records)
    painel, resumo = montar_painel(metrics, tendencia_rows, leads, envios, agora)
    painel.publicar(sh, svc, spreadsheet_id)
    dp.gravar_resumo(sh, svc, spreadsheet_id, resumo, agora)
    return resumo
