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
import dashboard_kpi_blocks as kpi
from sync_aprovai import build_sheets_service

SECRETS_FILE = os.path.expanduser("~/.brada-secrets/automatize-primeira-fila-dados.env")

BRL = {"type": "CURRENCY", "pattern": "R$#,##0.00"}
PCT = {"type": "PERCENT", "pattern": "0.0%"}

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

URL_EQUIPE = "https://www.primeira-fila.com/equipe"

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
    return dados


def _taxa(parte, todo):
    return parte / todo if todo else 0


def montar_secoes(dados):
    e, a, p, r, h = (dados[k] for k in ("espacos", "anfitrioes", "procura", "reservas", "hunter"))
    return [
        kpi.Secao("Espacos anunciados", [
            (e["cadastrados"], "Espacos cadastrados", None),
            (e["no_ar"], "No ar hoje (publicados e ativos)", None),
            (e["em_revisao"], "Em revisao ou ajuste", None),
        ], nota=f"Anuncios em revisao: conferir na area da equipe, {URL_EQUIPE}"),
        kpi.Secao("Anfitrioes", [
            (a["perfis"], "Perfis cadastrados", None),
            (a["plano_rede"], "No plano Rede", None),
            (a["plano_basico"], "No plano Basico", None),
            (a["contas_recebimento"], "Com conta de recebimento ativa", None),
        ], nota=f"Sem os {len(PERFIS_TESTE)} perfis de teste e da equipe, nem os espacos, reservas, cliques e conversas ligados a eles."),
        kpi.Secao("Procura dos organizadores", [
            (p["cliques_total"], "Cliques em anuncios (desde o lancamento)", None),
            (p["cliques_30d"], "Cliques nos ultimos 30 dias", None),
            (p["conversas"], "Conversas abertas no chat", None),
            (p["pedidos_reserva"], "Pedidos de reserva", None),
        ]),
        kpi.Secao("Reservas e dinheiro", [
            (r["pagas"], "Reservas pagas e validas", None),
            (r["valor_pago"], "Valor pago pelos organizadores", BRL),
            (r["comissao"], "Comissao da Primeira Fila", BRL),
            (r["canceladas_expiradas"], "Canceladas ou expiradas", None),
        ], nota="Pago = pagamento confirmado e reserva nao cancelada. Reserva confirmada sem pagamento nao conta.",
           nota_aviso=True),
        kpi.Secao("Hunter (prospeccao de espacos pela Automatize)", [
            (h["captados"], "Espacos captados pelo robo", None),
            (h["abordados"], "Abordados no WhatsApp", None),
            (h["responderam"], "Responderam", None),
            (_taxa(h["cadastraram"], h["abordados"]), "Abordados que se cadastraram", PCT),
        ]),
    ]


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
    secoes = montar_secoes(dados)
    subtitulo = (f"Foto: {now.strftime('%d/%m/%Y %H:%M')} (Brasilia) - banco da Primeira Fila "
                 "(acesso de leitura liberado pela Automatize). Acumulado desde o lancamento, salvo indicacao")

    if args.dry_run:
        for secao in secoes:
            print(f"  [{secao.titulo}]", [(v, r) for v, r, _ in secao.metricas])
        print(f"  raw_primeira_fila_diario: {len(dados['diario'])} dias")
        return

    gc = sync.get_sheets_client()
    sh = gc.open_by_key(sync.SPREADSHEET_ID)
    sync.write_overwrite(sh, "raw_primeira_fila_diario", HEADER_DIARIO, dados["diario"])
    kpi.publish(sh, build_sheets_service(), sync.SPREADSHEET_ID, "Primeira Fila",
                "Primeira Fila - Espacos, procura e reservas", subtitulo, secoes)
    print("Primeira Fila: aba e raw atualizadas.")


if __name__ == "__main__":
    main()
