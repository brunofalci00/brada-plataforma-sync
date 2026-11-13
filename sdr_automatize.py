"""Banco do SDR Brada (Automatize, schema `dados`, so leitura): disparos por canal.

`modalidade` separa os tres canais: `incentivador`, `elaboracao` (leads de
elaboracao de projeto, rumo ao AprovAI) e `aprovado_selfservice` (proponentes com
projeto aprovado, rumo a Plataforma). Linhas `backfill-*` sao carga retroativa e
ficam fora.

PII (nome, e-mail, telefone) nunca e lida aqui: so lead_id, data, canal e
marcas de etapa.
"""

import datetime
import os

SECRETS_FILE = os.path.expanduser("~/.brada-secrets/automatize-sdr-brada.env")
CHAVES = ("HOST", "PORT", "NAME", "USER", "PASSWORD")

INCENTIVADOR = "incentivador"
ELABORACAO = "elaboracao"
APTOS = "aprovado_selfservice"

CONSULTA = """
    select lead_id, dia, canal,
           aberto_em is not null, clicou_em is not null, respondeu_em is not null,
           (visitou_agenda_em is not null or clicou_agendar_em is not null),
           coalesce(card_hubspot_em::date >= dia, false), coalesce(cadastro_em::date >= dia, false),
           bounce_em is not null
    from dados.envios
    where modalidade = %s and coalesce(origem_evento, '') not like 'backfill%%'
"""
CAMPOS = ("lead_id", "dia", "canal", "abriu", "clicou", "respondeu", "agenda", "card", "cadastro", "bounce")
MARCAS = CAMPOS[3:]


def _credenciais():
    """AUTOMATIZE_SDR_DB_* do ambiente (CI); local, o arquivo em ~/.brada-secrets."""
    env = {k: os.environ.get(f"AUTOMATIZE_SDR_DB_{k}") for k in CHAVES}
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
    return {k: arq.get(f"AUTOMATIZE_SDR_DB_{k}") for k in CHAVES}


def conectar():
    import psycopg2
    c = _credenciais()
    conn = psycopg2.connect(host=c["HOST"], port=c["PORT"], dbname=c["NAME"], user=c["USER"],
                            password=c["PASSWORD"], sslmode="require", connect_timeout=20)
    conn.set_session(readonly=True, autocommit=True)
    return conn


def envios(conn, modalidade):
    cur = conn.cursor()
    cur.execute(CONSULTA, (modalidade,))
    return [dict(zip(CAMPOS, r)) for r in cur.fetchall()]


def por_lead(lista):
    """Um registro por lead: primeiro disparo, canais e cada marca se aconteceu em qualquer envio."""
    leads = {}
    for e in lista:
        l = leads.setdefault(str(e["lead_id"]), {"dia": e["dia"], "canais": set(), **{m: False for m in MARCAS}})
        if e["dia"] and (l["dia"] is None or e["dia"] < l["dia"]):
            l["dia"] = e["dia"]
        l["canais"].add(e["canal"] or "?")
        for m in MARCAS:
            l[m] |= bool(e[m])
    return leads


def segunda(dia):
    return dia - datetime.timedelta(days=dia.weekday())


def semanas(hoje, n):
    ini = segunda(hoje)
    return [ini - datetime.timedelta(weeks=i) for i in range(n - 1, -1, -1)]


def _canal(c):
    return "whatsapp" if c == "whatsapp" else "email"


def por_semana(lista, hoje, n=10):
    cont = {s: {"email": 0, "whatsapp": 0} for s in semanas(hoje, n)}
    for e in lista:
        if e["dia"] and segunda(e["dia"]) in cont:
            cont[segunda(e["dia"])][_canal(e["canal"])] += 1
    return [[s.strftime("%d/%m"), c["email"], c["whatsapp"]] for s, c in cont.items()]


def por_dia(lista, hoje, n=30):
    dias = [hoje - datetime.timedelta(days=i) for i in range(n - 1, -1, -1)]
    cont = {d: {"email": 0, "whatsapp": 0} for d in dias}
    for e in lista:
        if e["dia"] in cont:
            cont[e["dia"]][_canal(e["canal"])] += 1
    return [[d.strftime("%d/%m"), c["email"], c["whatsapp"]] for d, c in cont.items()]


CONSULTA_FRENTES = """
    select modalidade, dia, canal, fonte_slug,
           aberto_em is not null, clicou_em is not null, respondeu_em is not null,
           (visitou_agenda_em is not null or clicou_agendar_em is not null),
           coalesce(card_hubspot_em::date >= dia, false), coalesce(cadastro_em::date >= dia, false),
           bounce_em is not null
    from dados.envios
    where modalidade in (%s, %s, %s) and dia is not null
      and coalesce(origem_evento, '') not like 'backfill%%'
"""
CAMPOS_FRENTES = ("modalidade", "dia", "canal", "fonte") + MARCAS

# Plano do alocador no ultimo dia planejado: o que saiu e o que ficou esperando, por frente e canal.
CONSULTA_FILA = """
    select modalidade, status, canal, count(*)
    from dados.plano_disparo
    where dia = (select max(dia) from dados.plano_disparo)
    group by 1, 2, 3
"""


def envios_frentes(conn):
    """Envios das tres frentes, sem lead_id: modalidade, dia, canal, fonte e marcas de etapa."""
    cur = conn.cursor()
    cur.execute(CONSULTA_FRENTES, (INCENTIVADOR, ELABORACAO, APTOS))
    return [dict(zip(CAMPOS_FRENTES, r)) for r in cur.fetchall()]


def fila_do_dia(conn):
    """{modalidade: {"enviados": n, "na_fila": n, "canal_fila": canal mais comum entre os que esperam}}."""
    cur = conn.cursor()
    cur.execute(CONSULTA_FILA)
    out = {}
    for modalidade, status, canal, n in cur.fetchall():
        f = out.setdefault(modalidade, {"enviados": 0, "na_fila": 0, "_canais": {}})
        if status == "enviado":
            f["enviados"] += n
        else:
            f["na_fila"] += n
            f["_canais"][canal or "?"] = f["_canais"].get(canal or "?", 0) + n
    for f in out.values():
        canais = f.pop("_canais")
        f["canal_fila"] = max(canais, key=canais.get) if canais else ""
    return out


def ultimo_disparo(lista):
    dias = [e["dia"] for e in lista if e["dia"]]
    return max(dias) if dias else None


def coorte(leads, hoje, etapas, n=10):
    """Uma linha por semana do primeiro disparo: [semana, n, etapa1, etapa2, ..., taxa da ultima]."""
    linhas = []
    for s in semanas(hoje, n):
        grupo = [l for l in leads.values() if l["dia"] and segunda(l["dia"]) == s]
        if not grupo:
            continue
        contagens = [sum(1 for l in grupo if l[chave]) for chave in etapas]
        linhas.append([s.strftime("%d/%m"), len(grupo)] + contagens
                      + [contagens[-1] / len(grupo) if grupo else 0])
    return linhas
