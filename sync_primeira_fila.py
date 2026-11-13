"""Sync Primeira Fila (banco da Automatize, so leitura) -> aba "Primeira Fila".

Fonte: Postgres do Supabase da Primeira Fila, usuario `leitura_dados`, esquema
`dados` (visoes liberadas pela Automatize). So agregados saem daqui: nenhuma
linha com nome, e-mail, telefone ou documento vai para a Sheet.

Credenciais: PF_DB_HOST / PF_DB_USER / PF_DB_PASSWORD no ambiente (CI); local,
cai no arquivo ~/.brada-secrets/automatize-primeira-fila-dados.env.
"""

import argparse
import datetime
import os

import sync
import dashboard_painel as dp
import painel_filtros as fl

SECRETS_FILE = os.path.expanduser("~/.brada-secrets/automatize-primeira-fila-dados.env")

HEADER_DIARIO = ["dia", "cliques_anuncios", "reservas_criadas", "perfis_criados", "leads_hunter_captados"]

# Perfis de teste e da equipe (Automatize, Brada, contas da plataforma), confirmados
# pelo Bruno em 09/11. Saem do painel junto com os espacos, reservas, cliques e conversas
# ligados a eles. Trocar por um campo no banco quando a Automatize criar.
PERFIS_TESTE = [
    "78434337-1c2b-4e75-a372-b523d9158b02",
    "3253aaad-f4a4-4113-9eac-23761586fb02",
    "3d086ae1-a362-4fad-8e41-726c9188c80b",
    "750902c6-fd8c-4621-ac65-8d37cee95fc6",
    "39aefb58-44f1-4c41-95e4-ef0e71b3552b",
    "b1948061-dfaf-4a46-9e33-78e18280d74a",
    "be837816-3b3f-4397-ac81-720bb25ef156",
    "bb56adda-030b-47a8-98d4-03da5936420d",
    "a0322f67-213d-4527-ac67-8805aae243c0",
    "cfcc07b5-a05d-43e1-9b0c-cffbd2a88919",
    "6d936fac-3bb4-47dd-a485-c1367a8486c4",
    "c01d2718-3124-4aac-9f8e-3cdcb74527be",
    "1eb9c0d9-7467-45b7-968f-02b192433b21",
]


# CTEs reutilizadas: o que e real = fora da lista de teste.
BASE = """
    with teste as (select unnest(%(teste)s::uuid[]) id),
    espacos_reais as (select * from dados.espacos where owner_id not in (select id from teste)),
    perfis_reais as (select * from dados.perfis where id not in (select id from teste)),
    reservas_reais as (
        select r.* from dados.reservas r join espacos_reais e on e.id = r.venue_id
        where r.guest_id is null or r.guest_id not in (select id from teste)),
    cliques_reais as (
        select c.* from dados.cliques_anuncios c join espacos_reais e on e.protocol = c.venue_protocol),
    conversas_reais as (
        select c.* from dados.chat_conversas c join espacos_reais e on e.id = c.venue_id
        where c.guest_id is null or c.guest_id not in (select id from teste))
"""

# Cada consulta devolve UMA linha com os numeros do bloco. Ordem das colunas = chaves.
CONSULTAS = {
    "espacos": ("""
        select count(*),
               count(*) filter (where revisao_status = 'publicado' and not coalesce(pausado, false)
                                and not coalesce(revisao_oculto, false)),
               count(*) filter (where revisao_status is distinct from 'publicado')
        from espacos_reais""",
        ["cadastrados", "no_ar", "em_revisao"]),
    "anfitrioes": ("""
        select count(*),
               count(*) filter (where plano = 'rede'),
               count(*) filter (where plano = 'basico'),
               (select count(*) from dados.contas_recebimento where status = 'ativa'
                and owner_id not in (select id from teste))
        from perfis_reais""",
        ["perfis", "plano_rede", "plano_basico", "contas_recebimento"]),
    "procura": ("""
        select (select count(*) from cliques_reais),
               (select count(*) from cliques_reais where clicked_at >= now() - interval '30 days'),
               (select count(*) from conversas_reais),
               (select count(*) from reservas_reais)""",
        ["cliques_total", "cliques_30d", "conversas", "pedidos_reserva"]),
    "reservas": ("""
        select count(*) filter (where pagamento_status = 'pago' and status <> 'cancelada'),
               coalesce(sum(valor_total) filter (where pagamento_status = 'pago' and status <> 'cancelada'), 0),
               coalesce(sum(comissao) filter (where pagamento_status = 'pago' and status <> 'cancelada'), 0),
               count(*) filter (where status in ('cancelada', 'expirada'))
        from reservas_reais""",
        ["pagas", "valor_pago", "comissao", "canceladas_expiradas"]),
    "hunter": ("""
        select count(*),
               count(*) filter (where disparado_em is not null),
               (select count(distinct lead_id) from dados.hunter_envios where respondeu_em is not null),
               count(*) filter (where cadastrado_em is not null)
        from dados.hunter_leads""",
        ["captados", "abordados", "responderam", "cadastraram"]),
}

CONSULTA_DIARIO = BASE + """
    , dias as (
        select clicked_at::date d, 'c' k from cliques_reais
        union all select created_at::date, 'r' from reservas_reais
        union all select created_at::date, 'p' from perfis_reais
        union all select created_at::date, 'h' from dados.hunter_leads)
    select d, count(*) filter (where k = 'c'), count(*) filter (where k = 'r'),
           count(*) filter (where k = 'p'), count(*) filter (where k = 'h')
    from dias group by d order by d"""


TZ = "America/Sao_Paulo"

# Um registro por espaco captado, na data da captacao; etapas = estado de hoje.
CONSULTA_HUNTER = f"""
    select (l.created_at at time zone '{TZ}')::date, l.cidade, l.categoria,
           (select e.canal from dados.hunter_envios e where e.lead_id = l.id order by e.enviado_em limit 1),
           l.disparado_em is not null or exists (select 1 from dados.hunter_envios e where e.lead_id = l.id),
           exists (select 1 from dados.hunter_envios e where e.lead_id = l.id and e.respondeu_em is not null),
           l.link_enviado_em is not null
             or exists (select 1 from dados.hunter_envios e where e.lead_id = l.id and e.link_enviado_em is not null),
           l.link_clicado_em is not null or coalesce(l.link_cliques, 0) > 0
             or exists (select 1 from dados.hunter_envios e where e.lead_id = l.id and e.link_clicado_em is not null),
           l.cadastrado_em is not null
             or exists (select 1 from dados.hunter_envios e where e.lead_id = l.id and e.cadastrado_em is not null)
    from dados.hunter_leads l"""

# Demanda: cada etapa conta na data em que aconteceu, com a cidade do espaco.
CONSULTA_DEMANDA = BASE + f"""
    select (c.clicked_at at time zone '{TZ}')::date, e.cidade, 'cliques' from cliques_reais c
      join espacos_reais e on e.protocol = c.venue_protocol
    union all select (c.criada_em at time zone '{TZ}')::date, e.cidade, 'conversas' from conversas_reais c
      join espacos_reais e on e.id = c.venue_id
    union all select (r.created_at at time zone '{TZ}')::date, e.cidade, 'pedidos' from reservas_reais r
      join espacos_reais e on e.id = r.venue_id
    union all select (r.pago_em at time zone '{TZ}')::date, e.cidade, 'pagas' from reservas_reais r
      join espacos_reais e on e.id = r.venue_id
      where r.pagamento_status = 'pago' and r.status <> 'cancelada' and r.pago_em is not null"""

ABA = "Primeira Fila"
BASE_HUNTER = fl.Base("base_pf_hunter", [("cidade", "Cidade", fl.TODAS), ("categoria", "Categoria", fl.TODAS),
                                         ("canal", "Canal", fl.TODOS)], [
    ("captados", "Captados"), ("abordados", "Abordados"), ("responderam", "Responderam"),
    ("link", "Link enviado"), ("clicou", "Clicaram no link"), ("cadastrou", "Cadastraram")])
FUNIL_HUNTER = [("Espacos captados", "captados"), ("Abordados", "abordados"), ("Responderam", "responderam"),
                ("Link enviado", "link"), ("Clicaram no link", "clicou"), ("Cadastraram", "cadastrou")]
BASE_DEMANDA = fl.Base("base_pf_demanda", [("cidade", "Cidade", fl.TODAS)], [
    ("cliques", "Cliques em anuncios"), ("conversas", "Conversas"), ("pedidos", "Pedidos de reserva"),
    ("pagas", "Reservas pagas")])
FUNIL_DEMANDA = [("Cliques em anuncios", "cliques"), ("Conversas no chat", "conversas"),
                 ("Pedidos de reserva", "pedidos"), ("Reservas pagas", "pagas")]
CANAIS = {"whatsapp": "WhatsApp", "email": "E-mail", "e-mail": "E-mail", "instagram": "Instagram"}


def _canal(c):
    if not c:
        return "Sem envio"
    return CANAIS.get(str(c).strip().lower(), str(c).strip().capitalize())


def _etapas(abordado, respondeu, link, clicou, cadastrou):
    """Cumulativas de tras para frente: quem cadastrou tambem clicou, recebeu link, respondeu e foi abordado."""
    clicou = clicou or cadastrou
    link = link or clicou
    respondeu = respondeu or link
    abordado = abordado or respondeu
    return {"captados": 1, "abordados": abordado, "responderam": respondeu, "link": link,
            "clicou": clicou, "cadastrou": cadastrou}


def linhas_hunter(registros):
    """registros: (dia, cidade, categoria, canal, abordado, respondeu, link, clicou, cadastrou)."""
    return BASE_HUNTER.agregar(
        (dia, {"cidade": cidade or "sem cidade", "categoria": categoria or "sem categoria", "canal": _canal(canal)},
         _etapas(*marcas)) for dia, cidade, categoria, canal, *marcas in registros)


def linhas_demanda(registros):
    return BASE_DEMANDA.agregar((dia, {"cidade": cidade or "sem cidade"}, {etapa: 1})
                                for dia, cidade, etapa in registros)


def _credenciais():
    env = {
        "host": os.environ.get("PF_DB_HOST"),
        "user": os.environ.get("PF_DB_USER"),
        "password": os.environ.get("PF_DB_PASSWORD"),
    }
    if all(env.values()):
        return env
    if not os.path.exists(SECRETS_FILE):
        raise SystemExit("Credenciais do banco da Primeira Fila nao encontradas (PF_DB_* ou arquivo local).")
    arq = {}
    with open(SECRETS_FILE, encoding="utf-8") as f:
        for linha in f:
            if "=" in linha and not linha.lstrip().startswith("#"):
                k, v = linha.split("=", 1)
                arq[k.strip()] = v.strip()
    return {
        "host": arq.get("AUTOMATIZE_PF_DB_HOST"),
        "user": arq.get("AUTOMATIZE_PF_DB_USER"),
        "password": arq.get("AUTOMATIZE_PF_DB_PASSWORD"),
    }


def conectar():
    import psycopg2
    cred = _credenciais()
    conn = psycopg2.connect(host=cred["host"], port=5432, dbname="postgres", user=cred["user"],
                            password=cred["password"], sslmode="require", connect_timeout=20)
    conn.set_session(readonly=True, autocommit=True)
    return conn


def _num(v):
    return float(v) if v is not None and not isinstance(v, int) else (v or 0)


def coletar(conn):
    cur = conn.cursor()
    dados = {}
    for bloco, (sql, chaves) in CONSULTAS.items():
        cur.execute(BASE + sql, {"teste": PERFIS_TESTE})
        dados[bloco] = dict(zip(chaves, (_num(v) for v in cur.fetchone())))
    cur.execute(CONSULTA_DIARIO, {"teste": PERFIS_TESTE})
    dados["diario"] = [[d.isoformat(), *row] for d, *row in cur.fetchall()]
    cur.execute(CONSULTA_HUNTER)
    dados["hunter_linhas"] = cur.fetchall()
    cur.execute(CONSULTA_DEMANDA, {"teste": PERFIS_TESTE})
    dados["demanda_linhas"] = cur.fetchall()
    return dados


def por_semana(diario, hoje, colunas, n=10):
    """Soma colunas do diario (dia ISO na coluna 0) por semana, segunda a domingo."""
    ini = hoje - datetime.timedelta(days=hoje.weekday())
    semanas = [ini - datetime.timedelta(weeks=i) for i in range(n - 1, -1, -1)]
    cont = {s: [0] * len(colunas) for s in semanas}
    for linha in diario:
        d = datetime.date.fromisoformat(linha[0])
        s = d - datetime.timedelta(days=d.weekday())
        if s in cont:
            for i, c in enumerate(colunas):
                cont[s][i] += linha[c] or 0
    return [[s.strftime("%d/%m"), *v] for s, v in cont.items()]


def montar_painel(dados, agora):
    """Filtros no topo (periodo A/B, cidade, categoria, canal); anfitriao (Hunter) e demanda vivos;
    espaco reservado para o produtor; depois a foto de hoje (espacos, planos, totais)."""
    e, a, p, r = (dados[k] for k in ("espacos", "anfitrioes", "procura", "reservas"))
    hoje = agora.date()
    hl = linhas_hunter(dados.get("hunter_linhas", []))
    dl = linhas_demanda(dados.get("demanda_linhas", []))
    cidades = list(dict.fromkeys(BASE_HUNTER.opcoes(hl, "cidade") + BASE_DEMANDA.opcoes(dl, "cidade"))) or ["sem cidade"]
    canais = BASE_HUNTER.opcoes(hl, "canal") or ["Sem envio"]
    ctl = fl.controles(ABA, hoje, [fl.campo_lista("cidade", "Cidade", cidades, span=2),
                                   fl.campo_lista("categoria", "Categoria (Hunter)",
                                                  BASE_HUNTER.opcoes(hl, "categoria"), span=2),
                                   fl.campo_lista("canal", "Canal (Hunter)", canais, todos=fl.TODOS)])
    fh, fd = fl.Filtros(ctl, BASE_HUNTER), fl.Filtros(ctl, BASE_DEMANDA)
    painel = dp.Painel(ABA, "Primeira Fila · do espaco captado a reserva paga",
                       f"Atualizado {agora.strftime('%d/%m/%Y %H:%M')} · banco da Primeira Fila"
                       f" · sem os {len(PERFIS_TESTE)} perfis de teste e da equipe · mude as células claras abaixo")
    painel.linha(ctl)
    painel.linha(fl.CartaoVivo("Hunter: espacos captados", fh.soma_p("captados", "A"), fh.soma_p("captados", "B")),
                 fl.CartaoVivo("Hunter: cadastraram", fh.soma_p("cadastrou", "A"), fh.soma_p("cadastrou", "B")),
                 fl.CartaoVivo("Cliques em anuncios", fd.soma_p("cliques", "A"), fd.soma_p("cliques", "B")),
                 fl.CartaoVivo("Reservas pagas", fd.soma_p("pagas", "A"), fd.soma_p("pagas", "B")))
    painel.linha(fl.FunilVivo("Anfitriao: Hunter da captacao ao cadastro (periodo A)", fh, FUNIL_HUNTER, altura=360,
                              subtitulo="Espacos na data da captacao · etapas = estado de hoje"),
                 fl.FunilVivo("Demanda: do clique a reserva paga (periodo A)", fd, FUNIL_DEMANDA, altura=360,
                              subtitulo="Cada etapa na data em que aconteceu · so o filtro de cidade vale aqui"))
    painel.linha(fl.comparacao_ab("Hunter: periodo A contra periodo B", fh, FUNIL_HUNTER))
    painel.linha(fl.comparacao_ab("Demanda: periodo A contra periodo B", fd, FUNIL_DEMANDA))
    painel.linha(fl.evolucao("Hunter: espacos captados no periodo A", fh, "captados", "canal", canais,
                             subtitulo="Por dia ou semana, empilhado pelo canal da abordagem"),
                 fl.evolucao("Demanda: cliques em anuncios no periodo A", fd, "cliques", "cidade", cidades[:6],
                             subtitulo="Por dia ou semana, empilhado pelas 6 cidades com mais volume"))
    recentes = hoje - datetime.timedelta(days=2 * fl.JANELA_PADRAO - 1)
    painel.linha(fl.por_dimensao("Hunter por cidade (periodo A)", fh, "cidade",
                                 BASE_HUNTER.opcoes(hl, "cidade", desde=recentes) or cidades[:1], [
        ("Captados", "soma", "captados"), ("Abordados", "taxa", "abordados"), ("Responderam", "taxa", "responderam"),
        ("Clicaram no link", "soma", "clicou"), ("Cadastraram", "soma", "cadastrou")],
        gradiente=[1], cabecalho_dim="Cidade"))
    painel.linha(dp.Tabela("Produtor (aguardando dado)", ["Etapa", "Situacao"], [
        ["Disparos para produtores (SALIC, a partir de 16/11)", "A Automatize ainda nao registrou onde ficam"],
        ["Resposta, cadastro e reserva do produtor", "Entra quando o registro existir"]]))
    painel.linha(dp.Cartao("Espacos no ar (hoje)", e["no_ar"]), dp.Cartao("Anfitrioes (hoje)", a["perfis"]),
                 dp.Cartao("Reservas pagas (desde o lancamento)", r["pagas"]),
                 dp.Cartao("Valor pago (desde o lancamento)", r["valor_pago"], dp.FMT_BRL))
    painel.linha(dp.Rosca("Espacos cadastrados (foto de hoje)", [
                     ("No ar", e["no_ar"]), ("Em revisao", e["em_revisao"]),
                     ("Pausados ou ocultos", e["cadastrados"] - e["no_ar"] - e["em_revisao"])]),
                 dp.Rosca("Anfitrioes por plano (foto de hoje)", [
                     ("Rede", a["plano_rede"]), ("Basico", a["plano_basico"]),
                     ("Sem plano", a["perfis"] - a["plano_rede"] - a["plano_basico"])]))
    resumo = {"pf_espacos_no_ar": e["no_ar"], "pf_cliques_30d": p["cliques_30d"], "pf_cliques_total": p["cliques_total"],
              "pf_pedidos": p["pedidos_reserva"], "pf_reservas_pagas": r["pagas"], "pf_valor_pago": r["valor_pago"],
              "pf_anfitrioes": a["perfis"]}
    return painel, resumo, [(BASE_HUNTER, hl), (BASE_DEMANDA, dl)]


def main():
    ap = argparse.ArgumentParser(description="Sync Primeira Fila -> Sheets")
    ap.add_argument("--dry-run", action="store_true", help="le tudo, imprime, nao escreve")
    args = ap.parse_args()

    now = datetime.datetime.now(sync.BRT)
    print("Puxando banco da Primeira Fila (esquema dados, so leitura)...")
    conn = conectar()
    try:
        dados = coletar(conn)
    finally:
        conn.close()

    tabs = {"raw_primeira_fila_diario": (HEADER_DIARIO, dados["diario"])}
    sync.pii_guard(tabs)
    painel, resumo, bases = montar_painel(dados, now)

    if args.dry_run:
        print("  resumo:", resumo)
        print(f"  raw_primeira_fila_diario: {len(dados['diario'])} dias")
        return

    gc = sync.get_sheets_client()
    sh = gc.open_by_key(sync.SPREADSHEET_ID)
    sync.write_overwrite(sh, "raw_primeira_fila_diario", HEADER_DIARIO, dados["diario"])
    svc = sync.build_sheets_service()
    for base, linhas in bases:
        base.publicar(sh, svc, sync.SPREADSHEET_ID, linhas)
    painel.publicar(sh, svc, sync.SPREADSHEET_ID)
    dp.gravar_resumo(sh, svc, sync.SPREADSHEET_ID, resumo, now)
    print("Primeira Fila: aba e raw atualizados.")


if __name__ == "__main__":
    main()
