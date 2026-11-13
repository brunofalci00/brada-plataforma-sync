"""Sync Comunidade Brada (Firestore + Auth do projeto brada-comunidade-c1ba5) -> aba "Comunidade".

So leitura. So agregados saem daqui: nenhum nome, e-mail, telefone ou uid vai para a Sheet.
Equipe (campo `isBradaTeam`, e-mail @brada/@criape, papel admin) fica fora de tudo.

Credencial:
- CI: COMUNIDADE_SA_JSON (conta dashboard-leitura, papeis datastore.viewer + firebaseauth.viewer).
- Local: COMUNIDADE_ACCESS_TOKEN (token de quem ja tem acesso ao projeto) para testar.
"""

import argparse
import collections
import datetime
import os

import sync
import dashboard_painel as dp
import ga_analytics
import painel_filtros as fl

PROJETO = "brada-comunidade-c1ba5"
DATABASE = "default"
ESCOPOS = ga_analytics.ESCOPOS

# Propriedades do Google Analytics com acesso de Leitor para a conta dashboard-leitura.
# 544471428 = G-HE11117R0R (ligada ao Firebase, dados desde nov/26). Falta a do G-WKKG4NJ3TV.
GA_PROPRIEDADES = [p for p in os.environ.get("COMUNIDADE_GA_PROPRIEDADES", "544471428").split(",") if p]
URL_AUTH = f"https://identitytoolkit.googleapis.com/v1/projects/{PROJETO}/accounts:batchGet"
COMUNIDADE_SA_FILE = os.environ.get(
    "COMUNIDADE_SA_FILE", os.path.expanduser(r"~/.brada-secrets/comunidade-dashboard-sa.json"))

DIA_MS = 86_400_000

HEADER_DIARIO = ["dia", "cadastros", "primeiras_acoes", "perfis_completos"]


# ---------------------------------------------------------------- credencial

def _credenciais():
    return ga_analytics.credenciais(os.environ.get("COMUNIDADE_SA_JSON", ""),
                                     COMUNIDADE_SA_FILE, "COMUNIDADE_ACCESS_TOKEN")


def _docs(db, colecao):
    return [d.to_dict() or {} for d in db.collection(colecao).stream()]


def _contas_auth(creds):
    from google.auth.transport.requests import AuthorizedSession
    sessao = AuthorizedSession(creds)
    contas, pagina = [], None
    while True:
        params = {"maxResults": 1000, **({"nextPageToken": pagina} if pagina else {})}
        r = sessao.get(URL_AUTH, params=params, headers={"x-goog-user-project": PROJETO}, timeout=60)
        r.raise_for_status()
        corpo = r.json()
        contas += corpo.get("users", [])
        pagina = corpo.get("nextPageToken")
        if not pagina:
            return contas


def coletar():
    from google.cloud import firestore
    creds = _credenciais()
    db = firestore.Client(project=PROJETO, database=DATABASE, credentials=creds)
    nomes = ["users", "activation_events", "community_posts", "user_connections", "articles",
             "courses", "opportunities", "events", "direct_messages"]
    dados = {n: _docs(db, n) for n in nomes}
    dados["auth"] = _contas_auth(creds)
    dados["ga"] = _analytics(creds)
    return dados


def _analytics(creds):
    return ga_analytics.coletar(creds, GA_PROPRIEDADES, dimensao_pagina="unifiedScreenName")


# ---------------------------------------------------------------- regras (puras, testadas)

def e_equipe_email(email):
    e = (email or "").strip().lower()
    return "@brada" in e or "@criape" in e


def e_equipe(usuario):
    return (usuario.get("isBradaTeam") is True or usuario.get("role") == "admin"
            or e_equipe_email(usuario.get("email")))


def _texto_data(v):
    if v is None:
        return ""
    if hasattr(v, "isoformat"):
        return v.isoformat()
    return str(v)


def _taxa(parte, todo):
    return parte / todo if todo else 0


def calcular(dados, agora_ms):
    reais = [u for u in dados["users"] if not e_equipe(u)]
    papeis = collections.Counter(u.get("role") for u in reais)
    auth = [a for a in dados["auth"] if not e_equipe_email(a.get("email"))]

    def dias_desde(a, campo):
        return (agora_ms - int(a.get(campo, 0) or 0)) / DIA_MS

    elegiveis_7d = [a for a in auth if dias_desde(a, "createdAt") >= 7]
    voltaram_7d = [a for a in elegiveis_7d
                   if (int(a.get("lastLoginAt", 0) or 0) - int(a.get("createdAt", 0) or 0)) / DIA_MS >= 7]

    eventos = dados["activation_events"]
    acoes = collections.Counter(e.get("actionType") for e in eventos if e.get("event") == "first_relevant_action")
    conexoes = collections.Counter(c.get("status") for c in dados["user_connections"])
    artigos = collections.Counter(a.get("status") for a in dados["articles"])
    editais = collections.Counter(str(o.get("status", "")).upper() for o in dados["opportunities"])
    cursos_com_inscritos = sum(1 for c in dados["courses"] if int(c.get("enrolledUsersCount") or 0) > 0)
    inscritos = sum(int(c.get("enrolledUsersCount") or 0) for c in dados["courses"])

    mes_atual = datetime.datetime.fromtimestamp(agora_ms / 1000, sync.BRT).strftime("%Y-%m")
    return {
        "membros": len(reais),
        "cadastros_mes": sum(1 for u in reais if _texto_data(u.get("createdAt")).startswith(mes_atual)),
        "equipe": len(dados["users"]) - len(reais),
        "explorador": papeis.get("explorador", 0),
        "idealizador": papeis.get("idealizador", 0),
        "incentivador": papeis.get("incentivador", 0),
        "viabilizador": papeis.get("viabilizador", 0),
        "onboarding": _taxa(sum(1 for u in reais if u.get("onboardingCompleted") is True), len(reais)),
        "quiz": sum(1 for u in reais if u.get("impactQuizCompleted") is True),
        "tour": sum(1 for u in reais if u.get("tourStatus") == "completed"),
        "login_7d": sum(1 for a in auth if dias_desde(a, "lastLoginAt") <= 7),
        "login_30d": sum(1 for a in auth if dias_desde(a, "lastLoginAt") <= 30),
        "voltaram_7d": _taxa(len(voltaram_7d), len(elegiveis_7d)),
        "primeira_acao": sum(acoes.values()),
        "acao_evento": acoes.get("event_external_click", 0),
        "posts": len(dados["community_posts"]),
        "conexoes_aceitas": conexoes.get("accepted", 0),
        "conexoes_pendentes": conexoes.get("pending", 0),
        "mensagens": len(dados["direct_messages"]),
        "noticias_publicadas": artigos.get("published", 0),
        "noticias_pendentes": artigos.get("pending_approval", 0),
        "leituras": sum(int(a.get("views") or 0) for a in dados["articles"]),
        "cursos": len(dados["courses"]),
        "cursos_com_inscritos": cursos_com_inscritos,
        "inscritos_cursos": inscritos,
        "editais_abertos": editais.get("ABERTO", 0),
        "editais_breve": editais.get("BREVE", 0),
        "eventos": len(dados["events"]),
        "ga": resumo_ga(dados.get("ga")),
    }


def resumo_ga(ga):
    return ga_analytics.resumo(ga, limpar_pagina=lambda nome: nome.replace(" | Brada", "").strip())


def diario(dados):
    contagem = collections.defaultdict(lambda: [0, 0, 0])
    for u in dados["users"]:
        if not e_equipe(u):
            dia = _texto_data(u.get("createdAt"))[:10]
            if dia:
                contagem[dia][0] += 1
    for e in dados["activation_events"]:
        dia = _texto_data(e.get("timestamp"))[:10]
        if not dia:
            continue
        if e.get("event") == "first_relevant_action":
            contagem[dia][1] += 1
        elif e.get("event") == "profile_completed":
            contagem[dia][2] += 1
    return [[d, *v] for d, v in sorted(contagem.items())]


def por_semana(linhas, hoje, n=10):
    """Soma o diario (dia, cadastros, primeiras_acoes, perfis) por semana, segunda a domingo."""
    ini = hoje - datetime.timedelta(days=hoje.weekday())
    semanas = [ini - datetime.timedelta(weeks=i) for i in range(n - 1, -1, -1)]
    cont = {s: [0, 0] for s in semanas}
    for dia, cadastros, acoes, _ in linhas:
        try:
            d = datetime.date.fromisoformat(dia)
        except ValueError:
            continue
        s = d - datetime.timedelta(days=d.weekday())
        if s in cont:
            cont[s][0] += cadastros
            cont[s][1] += acoes
    return [[s.strftime("%d/%m"), *v] for s, v in cont.items()]


ABA = "Comunidade"
PERFIS = {"explorador": "Explorador", "idealizador": "Idealizador", "incentivador": "Incentivador",
          "viabilizador": "Viabilizador"}
BASE_COM = fl.Base("base_comunidade", [("perfil", "Perfil", fl.TODOS)], [
    ("cadastros", "Cadastros"), ("perfis", "Perfis completos"), ("acoes", "Primeiras acoes")])
ETAPAS_COM = [("Cadastros", "cadastros"), ("Perfis completos", "perfis"), ("Primeiras acoes", "acoes")]


def _dia(v):
    try:
        return datetime.date.fromisoformat(_texto_data(v)[:10])
    except ValueError:
        return None


def _perfil(papel):
    return PERFIS.get(str(papel or "").strip().lower(), "Sem perfil")


def linhas_base(dados):
    """Cada evento na data em que aconteceu, pelo perfil. Os eventos nao trazem o membro, entao nao
    da para seguir a mesma pessoa do cadastro a primeira acao: sao contagens do periodo, nao coorte."""
    def registros():
        for u in dados["users"]:
            if not e_equipe(u):
                yield _dia(u.get("createdAt")), {"perfil": _perfil(u.get("role"))}, {"cadastros": 1}
        for e in dados["activation_events"]:
            etapa = {"first_relevant_action": "acoes", "profile_completed": "perfis"}.get(e.get("event"))
            if etapa:
                yield _dia(e.get("timestamp")), {"perfil": _perfil(e.get("role"))}, {etapa: 1}
    return BASE_COM.agregar(registros())


def montar_painel(m, linhas, agora, base_linhas=()):
    ga = m.get("ga")
    base_linhas = list(base_linhas)
    perfis = BASE_COM.opcoes(base_linhas, "perfil") or ["Sem perfil"]
    ctl = fl.controles(ABA, agora.date(), [fl.campo_lista("perfil", "Perfil", perfis, todos=fl.TODOS, span=2)])
    flt = fl.Filtros(ctl, BASE_COM)
    p = dp.Painel(ABA, "Comunidade Brada · da visita a rede",
                  f"Atualizado {agora.strftime('%d/%m/%Y %H:%M')} · Firestore e Auth da Comunidade + Google Analytics"
                  f" · sem as {m['equipe']} contas da equipe · mude as células claras abaixo")
    p.linha(ctl)
    p.linha(fl.CartaoVivo("Cadastros", flt.soma_p("cadastros", "A"), flt.soma_p("cadastros", "B")),
            fl.CartaoVivo("Perfis completos", flt.soma_p("perfis", "A"), flt.soma_p("perfis", "B")),
            fl.CartaoVivo("Primeiras acoes", flt.soma_p("acoes", "A"), flt.soma_p("acoes", "B")),
            fl.CartaoVivo("Primeiras acoes por cadastro", flt.taxa("acoes", "A"), flt.taxa("acoes", "B"),
                          dp.FMT_PCT))
    p.linha(fl.comparacao_ab("Periodo A contra periodo B (cada evento na data em que aconteceu)", flt, ETAPAS_COM))
    p.linha(fl.evolucao("Cadastros no periodo A", flt, "cadastros", "perfil", perfis,
                        subtitulo="Por dia ou semana, empilhado por perfil"),
            fl.evolucao("Primeiras acoes no periodo A", flt, "acoes", "perfil", perfis,
                        subtitulo="Por dia ou semana, empilhado por perfil"))
    p.linha(dp.Cartao("Visitantes (30 dias)", ga["ativos"] if ga else 0),
            dp.Cartao("Membros (hoje)", m["membros"]),
            dp.Cartao("Entraram em 30 dias", m["login_30d"]),
            dp.Cartao("Visitantes que voltaram", ga["taxa_retorno"] if ga else 0, dp.FMT_PCT))
    p.linha(dp.Funil("Jornada do membro (foto de hoje, sem filtro)", [
                ("Cadastraram", m["membros"]), ("Concluiram o onboarding", round(m["onboarding"] * m["membros"])),
                ("Fizeram uma primeira acao", m["primeira_acao"])],
                subtitulo="% sobre os membros", altura=320),
            dp.Rosca("Perfis dos membros (foto de hoje)", [
                ("Explorador", m["explorador"]), ("Idealizador", m["idealizador"]),
                ("Incentivador", m["incentivador"]), ("Viabilizador", m["viabilizador"])], altura=320))
    p.linha(dp.Serie("Rede e conteudo (total ate hoje)", ["Item", "Total"], [
        ["Posts no feed", m["posts"]], ["Conexoes aceitas", m["conexoes_aceitas"]],
        ["Mensagens diretas", m["mensagens"]], ["Noticias publicadas", m["noticias_publicadas"]],
        ["Inscricoes em cursos", m["inscritos_cursos"]], ["Cliques em eventos", m["acao_evento"]]], tipo="BAR"))
    if ga and ga["top"]:
        p.linha(dp.Tabela("Telas mais vistas (30 dias)", ["Tela", "Visualizacoes"],
                          [[nome, vistas] for vistas, nome in ga["top"]], formatos=[None, dp.FMT_INT],
                          gradiente=[1]))
    resumo = {"comunidade_membros": m["membros"], "comunidade_cadastros_mes": m["cadastros_mes"],
              "comunidade_primeira_acao": m["primeira_acao"], "comunidade_login_30d": m["login_30d"],
              "comunidade_voltaram_7d": m["voltaram_7d"]}
    if ga:
        resumo.update({"comunidade_visitantes_30d": ga["ativos"], "comunidade_retorno": ga["taxa_retorno"]})
    return p, resumo


def main():
    ap = argparse.ArgumentParser(description="Sync Comunidade -> Sheets")
    ap.add_argument("--dry-run", action="store_true", help="le tudo, imprime, nao escreve")
    args = ap.parse_args()

    agora = datetime.datetime.now(sync.BRT)
    print("Puxando Firestore e Auth da Comunidade (so leitura)...")
    dados = coletar()
    metricas = calcular(dados, int(agora.timestamp() * 1000))
    linhas = diario(dados)
    tabs = {"raw_comunidade_diario": (HEADER_DIARIO, linhas)}
    sync.pii_guard(tabs)
    base_linhas = linhas_base(dados)
    painel, resumo = montar_painel(metricas, linhas, agora, base_linhas)

    if args.dry_run:
        print("  resumo:", resumo)
        print(f"  raw_comunidade_diario: {len(linhas)} dias")
        return

    gc = sync.get_sheets_client()
    sh = gc.open_by_key(sync.SPREADSHEET_ID)
    sync.write_overwrite(sh, "raw_comunidade_diario", HEADER_DIARIO, linhas)
    svc = sync.build_sheets_service()
    BASE_COM.publicar(sh, svc, sync.SPREADSHEET_ID, base_linhas)
    painel.publicar(sh, svc, sync.SPREADSHEET_ID)
    dp.gravar_resumo(sh, svc, sync.SPREADSHEET_ID, resumo, agora)
    print("Comunidade: aba e raw atualizados.")


if __name__ == "__main__":
    main()
