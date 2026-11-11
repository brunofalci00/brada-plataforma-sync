"""
Sync Disparo Incentivador -> Google Sheets (aba `Disparo Incentivador` +
raw_disparo_incentivador na mesma Sheet `Brada_Dashboard_Produtos`).

Fonte: banco Postgres da Automatize (schema `dados`, acesso so leitura),
a MESMA plataforma interna que roda a Brenda (IA de disparo por e-mail e
WhatsApp pros leads do SDR Incentivador). Host recebido do Jean em 11/11.

Complementa sync_sdr_incentivador.py (que le so o HubSpot): aqui entra o
funil ANTES do card existir no CRM — envio, abertura, clique, resposta,
visita a agenda, clique em agendar — e depois cruza os cards criados com
o estagio ATUAL no HubSpot (a tabela dados.hubspot_cards guarda so o
estagio de quando o aviso foi criado, pode estar desatualizada).

PII: dados.leads/dados.envios/dados.conversas tem nome, e-mail, telefone,
empresa, mensagem — NUNCA saem daqui. So lead_id (hash) e contadores por
canal/fonte_slug/dia (nao-PII: slug de campanha, nao de pessoa).
dados.conversas fica de fora: mistura disparo de outros produtos
(CriaBrasil, Elaboracao) junto com o Incentivador, e teria que ser
filtrada por lead_id pra nao contaminar a metrica - nao vale o risco por
um dado que dados.envios ja cobre (abertura/clique/resposta).

Rodar local: python sync_disparo_incentivador.py --dry-run
"""

import argparse
import datetime
import os

import sync
import dashboard_kpi_blocks as kpi
import sync_sdr_incentivador as sdr
from sync_aprovai import build_sheets_service

SECRETS_FILE = os.path.expanduser("~/.brada-secrets/automatize-sdr-brada.env")

PCT = {"type": "PERCENT", "pattern": "0.0%"}

HEADER_RAW = [
    "lead_id_hash", "dia", "canal", "fonte_slug", "aberto", "clicou", "respondeu",
    "visitou_agenda", "clicou_agendar", "card_hubspot", "bounce",
]

CONSULTA_LEADS = "select status, count(*) from dados.leads group by status"

CONSULTA_ENVIOS = """
    select id, lead_id, dia, canal, fonte_slug,
           aberto_em is not null, clicou_em is not null, respondeu_em is not null,
           visitou_agenda_em is not null, clicou_agendar_em is not null,
           card_hubspot_em is not null, bounce_em is not null
    from dados.envios
"""

CONSULTA_CARDS = "select deal_id from dados.hubspot_cards where deal_id is not null"


def _credenciais():
    env = {
        "host": os.environ.get("AUTOMATIZE_SDR_DB_HOST"),
        "port": os.environ.get("AUTOMATIZE_SDR_DB_PORT"),
        "name": os.environ.get("AUTOMATIZE_SDR_DB_NAME"),
        "user": os.environ.get("AUTOMATIZE_SDR_DB_USER"),
        "password": os.environ.get("AUTOMATIZE_SDR_DB_PASSWORD"),
    }
    if all(env.values()):
        return env
    if not os.path.exists(SECRETS_FILE):
        raise SystemExit("Credenciais do banco do SDR Brada nao encontradas (AUTOMATIZE_SDR_DB_* ou arquivo local).")
    arq = {}
    with open(SECRETS_FILE, encoding="utf-8") as f:
        for linha in f:
            if "=" in linha and not linha.lstrip().startswith("#"):
                k, v = linha.split("=", 1)
                arq[k.strip()] = v.strip()
    return {
        "host": arq.get("AUTOMATIZE_SDR_DB_HOST"),
        "port": arq.get("AUTOMATIZE_SDR_DB_PORT"),
        "name": arq.get("AUTOMATIZE_SDR_DB_NAME"),
        "user": arq.get("AUTOMATIZE_SDR_DB_USER"),
        "password": arq.get("AUTOMATIZE_SDR_DB_PASSWORD"),
    }


def conectar():
    import psycopg2
    cred = _credenciais()
    conn = psycopg2.connect(host=cred["host"], port=cred["port"], dbname=cred["name"],
                             user=cred["user"], password=cred["password"],
                             sslmode="require", connect_timeout=20)
    conn.set_session(readonly=True, autocommit=True)
    return conn


def coletar(conn):
    cur = conn.cursor()

    cur.execute(CONSULTA_LEADS)
    leads_por_status = dict(cur.fetchall())

    cur.execute(CONSULTA_ENVIOS)
    envios = cur.fetchall()

    cur.execute(CONSULTA_CARDS)
    deal_ids = [r[0] for r in cur.fetchall()]

    return {"leads_por_status": leads_por_status, "envios": envios, "deal_ids": deal_ids}


def montar_raw(envios):
    linhas = []
    for (envio_id, lead_id, dia, canal, fonte_slug, aberto, clicou, respondeu,
         visitou_agenda, clicou_agendar, card_hubspot, bounce) in envios:
        linhas.append([
            sync.hash_id(str(lead_id)), dia.isoformat() if dia else "", canal or "", fonte_slug or "",
            "sim" if aberto else "nao", "sim" if clicou else "nao", "sim" if respondeu else "nao",
            "sim" if visitou_agenda else "nao", "sim" if clicou_agendar else "nao",
            "sim" if card_hubspot else "nao", "sim" if bounce else "nao",
        ])
    return linhas


def resumo_envios(envios):
    total = len(envios)

    def conta(pos):
        return sum(1 for e in envios if e[pos])

    return {
        "total": total,
        "abriram": conta(5), "clicaram": conta(6), "responderam": conta(7),
        "visitaram_agenda": conta(8), "clicaram_agendar": conta(9),
        "card_hubspot": conta(10), "bounce": conta(11),
    }


def estagios_ao_vivo(deal_ids):
    """Estagio ATUAL no HubSpot pra cada deal_id (a tabela do banco so guarda o
    estagio de quando o card foi criado, pode estar desatualizada). Batch de
    ate 100 por chamada (limite da API de batch/read do HubSpot)."""
    if not sdr.HUBSPOT_TOKEN or not deal_ids:
        return {}
    estagios = {}
    for i in range(0, len(deal_ids), 100):
        lote = deal_ids[i:i + 100]
        resp = sdr.hs_post("https://api.hubapi.com/crm/v3/objects/deals/batch/read", {
            "inputs": [{"id": d} for d in lote], "properties": ["dealstage"],
        })
        for r in resp.get("results", []):
            estagios[r["id"]] = r["properties"].get("dealstage")
    return estagios


def resumo_cards(deal_ids, estagios):
    total = len(deal_ids)
    com_estagio = [estagios.get(d) for d in deal_ids if d in estagios]
    beyond = sum(1 for e in com_estagio if e in sdr.BEYOND_REUNIAO)
    return {
        "total": total,
        "encontrados_no_hubspot": len(com_estagio),
        "em_reuniao_ou_alem": beyond,
    }


def _taxa(parte, todo):
    return parte / todo if todo else 0


def montar_secoes(leads_por_status, resumo_env, resumo_crd):
    total_leads = sum(leads_por_status.values())
    return [
        kpi.Secao("Leads no funil (SDR Brada)", [
            (leads_por_status.get("novo", 0), "Novos, ainda sem disparo", None),
            (leads_por_status.get("pendente", 0), "Pendentes de contato", None),
            (leads_por_status.get("contatado", 0), "Contatados", None),
            (total_leads, "Total no funil", None),
        ], nota="Leads do SDR Brada (sourcing de incentivadores por lei/programa: SALIC, ICMS, ProAC e outros)."),
        kpi.Secao("Disparo (Brenda) - desde o lancamento", [
            (resumo_env["total"], "Enviados", None),
            (_taxa(resumo_env["abriram"], resumo_env["total"]), "Abriram", PCT),
            (_taxa(resumo_env["clicaram"], resumo_env["total"]), "Clicaram", PCT),
            (_taxa(resumo_env["responderam"], resumo_env["total"]), "Responderam", PCT),
        ], nota="Por e-mail e WhatsApp, Brenda (IA de disparo da Automatize)."),
        kpi.Secao("Do clique ate o CRM", [
            (resumo_env["visitaram_agenda"], "Visitaram a pagina da agenda", None),
            (resumo_env["clicaram_agendar"], "Clicaram em agendar", None),
            (resumo_env["card_hubspot"], "Card criado no HubSpot (automatico)", None),
            (_taxa(resumo_env["card_hubspot"], resumo_env["total"]), "Taxa disparo -> card", PCT),
        ], nota="Card automatico = a integracao cria sozinha quando o lead clica em agendar. "
                "Sem isso, e o executivo que puxa o lead a mao (nao entra nesta contagem)."),
        kpi.Secao("Desses cards, onde estao hoje (HubSpot ao vivo)", [
            (resumo_crd["total"], "Cards criados pela automacao (total)", None),
            (resumo_crd["em_reuniao_ou_alem"], "Em reuniao agendada ou alem, agora", None),
            (_taxa(resumo_crd["em_reuniao_ou_alem"], resumo_crd["total"]), "Taxa card -> reuniao", PCT),
            (resumo_env["bounce"], "E-mails/WhatsApp que nao entregaram (bounce)", None),
        ], nota=(f"{resumo_crd['encontrados_no_hubspot']} de {resumo_crd['total']} cards ainda existem no "
                 "HubSpot (o estagio e consultado ao vivo, nao fica preso ao que o banco da Automatize "
                 "guardou na hora da criacao)."), nota_aviso=True),
    ]


def main():
    ap = argparse.ArgumentParser(description="Sync Disparo Incentivador -> Sheets")
    ap.add_argument("--dry-run", action="store_true", help="le tudo, imprime, nao escreve")
    args = ap.parse_args()

    now = datetime.datetime.now(sync.BRT)
    print("Puxando banco do SDR Brada (schema dados, so leitura)...")
    conn = conectar()
    try:
        dados = coletar(conn)
    finally:
        conn.close()

    raw = montar_raw(dados["envios"])
    resumo_env = resumo_envios(dados["envios"])
    print(f"  {resumo_env['total']} envios | {resumo_env['card_hubspot']} cards criados")

    print("Cruzando cards com o estagio ao vivo no HubSpot...")
    estagios = estagios_ao_vivo(dados["deal_ids"])
    resumo_crd = resumo_cards(dados["deal_ids"], estagios)
    print(f"  {resumo_crd['em_reuniao_ou_alem']}/{resumo_crd['total']} em reuniao ou alem")

    tabs = {"raw_disparo_incentivador": (HEADER_RAW, raw)}
    sync.pii_guard(tabs)
    print("pii_guard: OK (zero hits)")

    secoes = montar_secoes(dados["leads_por_status"], resumo_env, resumo_crd)
    subtitulo = (f"Foto: {now.strftime('%d/%m/%Y %H:%M')} (Brasilia) - banco do SDR Brada "
                 "(acesso de leitura liberado pela Automatize) + HubSpot ao vivo. Acumulado desde o lancamento")

    if args.dry_run:
        print("\n--dry-run: nada escrito na Sheet.")
        print(f"  raw_disparo_incentivador: {len(raw)} linhas")
        for secao in secoes:
            print(f"  [{secao.titulo}]", [(v, r) for v, r, _ in secao.metricas])
        return

    gc = sync.get_sheets_client()
    sh = gc.open_by_key(sync.SPREADSHEET_ID)
    sync.write_overwrite(sh, "raw_disparo_incentivador", HEADER_RAW, raw)
    kpi.publish(sh, build_sheets_service(), sync.SPREADSHEET_ID, "Disparo Incentivador",
                "Disparo Incentivador - Brenda ate o CRM", subtitulo, secoes)
    print("Disparo Incentivador: aba e raw atualizadas.")


if __name__ == "__main__":
    main()
