"""Visitas (GA4) e Busca no Google (Search Console) por produto: AprovAI, Primeira Fila e Comunidade.

Cada produto publica duas bases escondidas que seguem o periodo A/B da propria aba:
- `base_<slug>_visitas`: dia x origem (canal agrupado) -> sessoes, engajadas, novos usuarios;
- `base_<slug>_busca`: dia x tipo de busca (marca ou nome / generica) -> impressoes, cliques e
  posicao x impressoes (x10, para a media ponderada sair por formula).

Credencial: a conta dashboard-leitura (mesma de ga_analytics), com Leitor no GA4 e usuario no
Search Console. So agregados: GA4 e Search Console nunca devolvem pessoa.
Falha de API nao derruba a aba: o bloco sai com o aviso no lugar dos numeros.
"""

import datetime
import os
import time

import dashboard_painel as dp
import ga_analytics
import painel_filtros as fl

ESCOPOS = ga_analytics.ESCOPOS + ["https://www.googleapis.com/auth/webmasters.readonly"]
URL_GSC = "https://www.googleapis.com/webmasters/v3/sites/{}/searchAnalytics/query"
INICIO_GSC = datetime.date(2026, 9, 1)
FMT_POS = {"type": "NUMBER", "pattern": "0.0"}
TOP_BUSCAS = 10
DIAS_TOP = 28
ESCALA_POS = 10

GENERICAS = ("espaco", "espaço", "salao", "salão", "evento", "festa", "aluguel", "alugar", "locacao",
             "locação", "casamento", "aniversario", "aniversário", "auditorio", "auditório", "teatro para",
             "projeto cultural", "lei rouanet", "edital", "captacao", "captação", "incentivo", "patrocinio",
             "patrocínio", "elaborar", "como fazer")

PRODUTOS = {
    "aprovai": {"ga": "564546144", "site": "sc-domain:aprovai-ai.brada.social", "pagina": None,
                "marca": ("aprovai", "aprovaí", "aprova ai", "brada"), "desde": datetime.date(2026, 11, 16)},
    "pf": {"ga": "564549121", "site": "sc-domain:primeira-fila.com", "pagina": None,
           "marca": ("primeira fila", "primeira-fila", "primeirafila"), "desde": datetime.date(2026, 11, 17)},
    # Comunidade: a propriedade ligada ao Firebase tem historico desde o lancamento; a do Grupo Brada
    # (564558519) so comecou em 16/11 e mede o mesmo site. sc-domain:brada.social cobre os subdominios
    # (match., aprovai-ai.): o filtro de pagina deixa so a Comunidade.
    "comunidade": {"ga": "544471428", "site": "sc-domain:brada.social", "pagina": "https://brada.social/",
                   "marca": ("brada",), "desde": None},
}

CANAIS = ["Google organico", "Direto", "Redes sociais", "Anuncio", "E-mail", "IA", "Outros"]
TIPOS = ["Marca ou nome", "Generica", OCULTA := "Busca oculta"]


def canal(origem, midia):
    o, m = (origem or "").lower(), (midia or "").lower()
    if m in ("cpc", "paid", "paid_social", "ppc") or "paid" in m:
        return "Anuncio"
    if m == "organic" and o in ("google", "bing", "yahoo", "duckduckgo") or "search" in o:
        return "Google organico"
    if o in ("(direct)",) or m in ("(none)",):
        return "Direto"
    if m in ("social", "social-network") or o in ("ig", "instagram", "facebook.com", "l.instagram.com",
                                                  "m.facebook.com", "linkedin.com", "lnkd.in", "t.co"):
        return "Redes sociais"
    if m == "email" or "mail" in o:
        return "E-mail"
    if m == "ai-assistant" or "chatgpt" in o or "perplexity" in o or "gemini" in o:
        return "IA"
    return "Outros"


def tipo_busca(consulta, marca):
    q = consulta.lower()
    if any(t in q for t in marca):
        return "Marca ou nome"
    return "Generica" if any(t in q for t in GENERICAS) else "Marca ou nome"


def bases(slug):
    return (fl.Base(f"base_{slug}_visitas", [("origem", "Origem", fl.TODAS)], [
                ("sessoes", "Sessoes"), ("engajadas", "Sessoes engajadas"), ("novos", "Novos usuarios")]),
            fl.Base(f"base_{slug}_busca", [("tipo", "Tipo de busca", fl.TODAS)], [
                ("impressoes", "Impressoes"), ("cliques", "Cliques"), ("pos_imp", "Posicao x impressoes x10")]))


# ---------- coleta ----------

def credenciais():
    return ga_analytics.credenciais(
        os.environ.get("COMUNIDADE_SA_JSON", ""),
        os.environ.get("COMUNIDADE_SA_FILE", os.path.expanduser(r"~/.brada-secrets/comunidade-dashboard-sa.json")),
        "COMUNIDADE_ACCESS_TOKEN").with_scopes(ESCOPOS)


def _post(sessao, url, corpo, tentativas=3):
    """Repete queda de conexao (ConnectionReset ja apareceu no GA4); erro HTTP sobe na hora."""
    import requests
    for i in range(tentativas):
        try:
            r = sessao.post(url, json=corpo, timeout=60)
            r.raise_for_status()
            return r
        except requests.exceptions.ConnectionError:
            if i == tentativas - 1:
                raise
            time.sleep(2 ** i)


def _ga(sessao, prop):
    corpo = {"dateRanges": [{"startDate": "120daysAgo", "endDate": "today"}],
             "dimensions": [{"name": "date"}, {"name": "sessionSource"}, {"name": "sessionMedium"},
                            {"name": "hostName"}],
             "metrics": [{"name": "sessions"}, {"name": "engagedSessions"}, {"name": "newUsers"}],
             "limit": 100000}
    r = _post(sessao, ga_analytics.URL_RELATORIO.format(prop), corpo)
    out = []
    for row in r.json().get("rows", []):
        d, o, m, host = (x["value"] for x in row["dimensionValues"])
        if host.endswith(".run.app") or host in ("localhost", ""):
            continue  # ambiente de desenvolvimento medido junto
        out.append((datetime.datetime.strptime(d, "%Y%m%d").date(), canal(o, m),
                    [int(float(x["value"])) for x in row["metricValues"]]))
    return out


def _paginas(sessao, prop):
    """[(dia, caminho_sem_query, aba_da_query, sessoes)] dos ultimos 120 dias, fora de dev."""
    corpo = {"dateRanges": [{"startDate": "120daysAgo", "endDate": "today"}],
             "dimensions": [{"name": "date"}, {"name": "pagePathPlusQueryString"}, {"name": "hostName"}],
             "metrics": [{"name": "sessions"}], "limit": 100000}
    r = _post(sessao, ga_analytics.URL_RELATORIO.format(prop), corpo)
    out = []
    for row in r.json().get("rows", []):
        d, caminho, host = (x["value"] for x in row["dimensionValues"])
        if host.endswith(".run.app") or host in ("localhost", ""):
            continue
        out.append((datetime.datetime.strptime(d, "%Y%m%d").date(), caminho, int(float(row["metricValues"][0]["value"]))))
    return out


def _gsc(sessao, cfg, inicio, fim, dimensoes, limite=25000):
    corpo = {"startDate": inicio.isoformat(), "endDate": fim.isoformat(), "dimensions": dimensoes,
             "rowLimit": limite}
    if cfg["pagina"]:
        corpo["dimensionFilterGroups"] = [{"filters": [
            {"dimension": "page", "operator": "contains", "expression": cfg["pagina"]}]}]
    import urllib.parse
    r = _post(sessao, URL_GSC.format(urllib.parse.quote(cfg["site"], safe="")), corpo)
    return r.json().get("rows", [])


def coletar(slug, hoje, creds=None):
    """{'ga': [(dia, canal, [sessoes, engajadas, novos])], 'gsc': [(dia, tipo, imp, cliques, pos)],
    'top': [[consulta, imp, cliques, posicao, tipo]], 'erros': [...]}"""
    from google.auth.transport.requests import AuthorizedSession
    cfg = PRODUTOS[slug]
    sessao = AuthorizedSession(creds or credenciais())
    dados = {"ga": [], "gsc": [], "top": [], "paginas": [], "erros": []}
    try:
        dados["ga"] = _ga(sessao, cfg["ga"])
        dados["paginas"] = _paginas(sessao, cfg["ga"])
    except Exception as erro:  # noqa: BLE001 - aba sai sem o bloco de visitas
        dados["erros"].append(f"GA4: {erro}")
    try:
        por_busca = [(datetime.date.fromisoformat(r["keys"][0]), tipo_busca(r["keys"][1], cfg["marca"]),
                      r["impressions"], r["clicks"], r["position"])
                     for r in _gsc(sessao, cfg, INICIO_GSC, hoje, ["date", "query"])]
        totais = _gsc(sessao, cfg, INICIO_GSC, hoje, ["date"])
        dados["gsc"] = por_busca + resto_oculto(por_busca, totais)
        top = _gsc(sessao, cfg, hoje - datetime.timedelta(days=DIAS_TOP), hoje, ["query"], TOP_BUSCAS * 3)
        dados["top"] = [[r["keys"][0], r["impressions"], r["clicks"], round(r["position"], 1),
                         tipo_busca(r["keys"][0], cfg["marca"])] for r in sorted(top, key=lambda r: -r["impressions"]) if fl.seguro(r["keys"][0])][:TOP_BUSCAS]
    except Exception as erro:  # noqa: BLE001
        dados["erros"].append(f"Search Console: {erro}")
    for e in dados["erros"]:
        print(f"::warning::{slug} {e}")
    return dados


def resto_oculto(por_busca, totais):
    """O Google esconde as buscas raras: o total do dia menos o que veio por busca vira 'Busca oculta'.
    A posicao do resto sai da media ponderada (total x posicao menos a parte conhecida)."""
    conhecido = {}
    for d, _, i, c, p in por_busca:
        acc = conhecido.setdefault(d, [0, 0, 0.0])
        acc[0] += i
        acc[1] += c
        acc[2] += p * i
    out = []
    for r in totais:
        d = datetime.date.fromisoformat(r["keys"][0])
        i0, c0, pi0 = conhecido.get(d, [0, 0, 0.0])
        imp, cli = r["impressions"] - i0, max(r["clicks"] - c0, 0)
        if imp > 0:
            out.append((d, OCULTA, imp, cli, max(r["position"] * r["impressions"] - pi0, 0) / imp))
    return out


def linhas_bases(slug, dados):
    b_vis, b_bus = bases(slug)
    vis = b_vis.agregar((d, {"origem": c}, {"sessoes": v[0], "engajadas": v[1], "novos": v[2]})
                        for d, c, v in dados["ga"])
    bus = b_bus.agregar((d, {"tipo": t}, {"impressoes": i, "cliques": c, "pos_imp": round(p * i * ESCALA_POS)})
                        for d, t, i, c, p in dados["gsc"])
    return [(b_vis, vis), (b_bus, bus)]


# ---------- painel ----------

def blocos(p, ctl, slug, dados):
    """Acrescenta ao painel `p` as linhas de Visitas e Busca no Google, seguindo o periodo da aba."""
    b_vis, b_bus = bases(slug)
    desde = PRODUTOS[slug]["desde"]
    aviso = f" · o GA4 so coleta desde {desde.strftime('%d/%m')}: nao compare com antes" if desde else ""
    v, b = fl.Filtros(ctl, b_vis), fl.Filtros(ctl, b_bus)

    def ab(flt, etapa):
        return flt.soma_p(etapa, "A"), flt.soma_p(etapa, "B")

    if dados["ga"]:
        p.linha(fl.CartaoVivo("Sessoes no site", *ab(v, "sessoes")),
                fl.CartaoVivo("Novos usuarios", *ab(v, "novos")),
                fl.CartaoVivo("Sessoes engajadas", v.taxa("engajadas", "A"), v.taxa("engajadas", "B"), dp.FMT_PCT))
        canais = [c for c in CANAIS if any(c == x for _, x, _ in dados["ga"])]
        p.linha(fl.evolucao("Visitas por origem no periodo A", v, "sessoes", "origem", canais,
                            subtitulo="Sessoes por dia ou semana, empilhadas por canal (GA4)" + aviso))
        p.linha(fl.por_dimensao("De onde vem a visita (periodo A)", v, "origem", canais, [
                    ("Sessoes", "soma", "sessoes"), ("Novos", "soma", "novos"),
                    ("Engajadas", "taxa", "engajadas")], gradiente=[1], cabecalho_dim="Origem"))
    else:
        p.linha(dp.Tabela("Visitas no site (GA4)", ["Aviso"], [["Sem dado do GA4 nesta rodada"]]))

    if dados["gsc"]:
        imp, cli = ab(b, "impressoes"), ab(b, "cliques")
        pos = [f"IFERROR({b.soma_p('pos_imp', x)}/{b.soma_p('impressoes', x)}/{ESCALA_POS};0)" for x in "AB"]
        ger = [b.soma_p("impressoes", x, tipo="Generica") for x in "AB"]
        vis = [f'({b.soma_p("impressoes", x, tipo=False)}-{b.soma_p("impressoes", x, tipo=OCULTA)})' for x in "AB"]
        p.linha(fl.CartaoVivo("Aparicoes no Google", *imp),
                fl.CartaoVivo("Cliques vindos do Google", *cli),
                fl.CartaoVivo("Posicao media", *pos, FMT_POS),
                fl.CartaoVivo("Busca generica (das visiveis)", *[f"IFERROR({g}/{t};0)" for g, t in zip(ger, vis)],
                              dp.FMT_PCT))
        tipos = [t for t in TIPOS if any(t == x for _, x, *_ in dados["gsc"])]
        linha = [fl.evolucao("Aparicoes no Google por tipo de busca (periodo A)", b, "impressoes", "tipo", tipos,
                             subtitulo="Generica = quem procura o servico sem saber o nome; oculta = busca rara que "
                                       "o Google nao mostra. Search Console atrasa 2 a 3 dias")]
        if dados["top"]:
            linha.append(dp.Tabela(f"Buscas que mais trazem aparicao ({DIAS_TOP} dias, sem filtro)",
                                   ["Busca", "Aparicoes", "Cliques", "Posicao", "Tipo"], dados["top"],
                                   formatos=[None, dp.FMT_INT, dp.FMT_INT, FMT_POS, None], gradiente=[1]))
        for c in linha:
            p.linha(c)
    else:
        p.linha(dp.Tabela("Busca no Google (Search Console)", ["Aviso"], [["Sem dado do Search Console ainda"]]))
