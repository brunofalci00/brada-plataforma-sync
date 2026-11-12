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
import dashboard_charts
import dashboard_kpi_blocks as kpi
import ga_analytics
from sync_aprovai import build_sheets_service

PROJETO = "brada-comunidade-c1ba5"
DATABASE = "default"
ESCOPOS = ga_analytics.ESCOPOS

# Propriedades do Google Analytics com acesso de Leitor para a conta dashboard-leitura.
# 544471428 = G-HE11117R0R (ligada ao Firebase, dados desde nov/26). Falta a do G-WKKG4NJ3TV.
GA_PROPRIEDADES = [p for p in os.environ.get("COMUNIDADE_GA_PROPRIEDADES", "544471428").split(",") if p]
URL_AUTH = f"https://identitytoolkit.googleapis.com/v1/projects/{PROJETO}/accounts:batchGet"
COMUNIDADE_SA_FILE = os.environ.get(
    "COMUNIDADE_SA_FILE", os.path.expanduser(r"~/.brada-secrets/comunidade-dashboard-sa.json"))

PCT = {"type": "PERCENT", "pattern": "0.0%"}
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


def _secoes_ga(ga):
    if not ga:
        return [kpi.Secao("Visitas (Google Analytics)", [
            ("-", "Sem leitura do Google Analytics nesta rodada", None)], nota_aviso=True,
            nota="A conta dashboard-leitura precisa do papel Leitor na propriedade.")]
    return [
        kpi.Secao("Visitas nos ultimos 30 dias (Google Analytics)", [
            (ga["ativos"], "Visitantes ativos (com e sem cadastro)", None),
            (ga["recorrentes"], "Visitantes que voltaram", None),
            (ga["taxa_retorno"], "Parte dos visitantes que voltou", PCT),
            (ga["paginas_vistas"], "Paginas vistas", None),
        ], nota="Fonte: propriedade G-HE11117R0R, com dados desde novembro. "
                "A propriedade principal (G-WKKG4NJ3TV) ainda nao foi liberada.", nota_aviso=True),
        kpi.Secao("Paginas mais vistas nos ultimos 30 dias", [
            (vistas, nome, None) for vistas, nome in ga["top"]
        ] or [(0, "Sem visitas no periodo", None)],
           nota="Pelo nome da tela: o app tem um endereco so, entao toda pagina aparece como \"/\"."),
    ]


def montar_secoes(m):
    return _secoes_ga(m.get("ga")) + [
        kpi.Secao("Membros", [
            (m["membros"], "Membros cadastrados", None),
            (m["cadastros_mes"], "Cadastros neste mes", None),
            (m["onboarding"], "Concluiram o onboarding", PCT),
            (m["quiz"], "Fizeram o quiz de impacto", None),
        ], nota=f"Sem as {m['equipe']} contas da equipe (Brada, CRIAPE e admins)."),
        kpi.Secao("Perfis", [
            (m["explorador"], "Exploradores", None),
            (m["idealizador"], "Idealizadores", None),
            (m["incentivador"], "Incentivadores", None),
            (m["viabilizador"], "Viabilizadores", None),
        ]),
        kpi.Secao("Uso", [
            (m["login_7d"], "Entraram nos ultimos 7 dias", None),
            (m["login_30d"], "Entraram nos ultimos 30 dias", None),
            (m["voltaram_7d"], "Voltaram 7 dias ou mais depois do cadastro", PCT),
            (m["primeira_acao"], "Fizeram uma primeira acao relevante", None),
        ], nota="O login so conta quando a pessoa entra de novo; sessao aberta nao conta. "
                "Por isso o retorno sai abaixo do real. O numero exato fica no Google Analytics.",
           nota_aviso=True),
        kpi.Secao("Rede", [
            (m["posts"], "Posts no feed", None),
            (m["conexoes_aceitas"], "Conexoes aceitas", None),
            (m["conexoes_pendentes"], "Conexoes pendentes", None),
            (m["mensagens"], "Mensagens diretas", None),
        ]),
        kpi.Secao("Conteudo", [
            (m["noticias_publicadas"], "Noticias publicadas", None),
            (m["noticias_pendentes"], "Noticias esperando aprovacao", None),
            (m["leituras"], "Leituras de noticias", None),
            (m["inscritos_cursos"], "Inscricoes em cursos", None),
        ], nota=f"{m['cursos_com_inscritos']} de {m['cursos']} cursos tem inscritos."),
        kpi.Secao("Oportunidades e eventos", [
            (m["editais_abertos"], "Editais abertos no radar", None),
            (m["editais_breve"], "Editais abrindo em breve", None),
            (m["eventos"], "Eventos na agenda", None),
            (m["acao_evento"], "Cliques em inscricao de evento", None),
        ]),
    ]


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
    secoes = montar_secoes(metricas)
    subtitulo = (f"Foto: {agora.strftime('%d/%m/%Y %H:%M')} (Brasilia) - base da Comunidade Brada "
                 "(brada.social). Acumulado desde o lancamento, salvo indicacao")

    if args.dry_run:
        for secao in secoes:
            print(f"  [{secao.titulo}]", [(v, r) for v, r, _ in secao.metricas])
        print(f"  raw_comunidade_diario: {len(linhas)} dias")
        return

    gc = sync.get_sheets_client()
    sh = gc.open_by_key(sync.SPREADSHEET_ID)
    raw_ws = sync.write_overwrite(sh, "raw_comunidade_diario", HEADER_DIARIO, linhas)
    svc = build_sheets_service()
    dest_ws, n_rows = kpi.publish(sh, svc, sync.SPREADSHEET_ID, "Comunidade",
                                  "Comunidade Brada - Membros, uso e conteudo", subtitulo, secoes)
    dashboard_charts.publish_line_chart(
        svc, sync.SPREADSHEET_ID, raw_ws, dest_ws, n_rows=len(linhas) + 1,
        series_cols=[1, 2, 3], title="Cadastros, primeiras acoes e perfis completos por dia",
        anchor_row=n_rows + 1)
    print("Comunidade: aba, raw e grafico atualizados.")


if __name__ == "__main__":
    main()
