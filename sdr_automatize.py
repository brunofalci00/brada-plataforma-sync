"""Banco do SDR Brada (Automatize, schema `dados`, so leitura): disparos por canal.

`modalidade` separa os tres canais: `incentivador`, `elaboracao` (leads de
elaboracao de projeto, rumo ao AprovAI) e `aprovado_selfservice` (proponentes com
projeto aprovado, rumo a Plataforma). Linhas `backfill-*` sao carga retroativa e
ficam fora.

PII (nome, e-mail, telefone) nunca e lida aqui: so lead_id, data, canal,
marcas de etapa e a `origem` do lead (de onde ele veio, para a fonte).
"""

import datetime
import hashlib
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
           bounce_em is not null, fonte_slug
    from dados.envios
    where modalidade = %s and coalesce(origem_evento, '') not like 'backfill%%'
"""
CAMPOS = ("lead_id", "dia", "canal", "abriu", "clicou", "respondeu", "agenda", "card", "cadastro", "bounce",
          "fonte")
MARCAS = CAMPOS[3:-1]


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
    """Um registro por lead: primeiro disparo (e a fonte dele), canais e cada marca se aconteceu em qualquer envio."""
    leads = {}
    for e in lista:
        l = leads.setdefault(str(e["lead_id"]), {"dia": e["dia"], "fonte": e.get("fonte"), "canais": set(),
                                                  **{m: False for m in MARCAS}})
        if e["dia"] and (l["dia"] is None or e["dia"] < l["dia"]):
            l["dia"], l["fonte"] = e["dia"], e.get("fonte")
        l["canais"].add(e["canal"] or "?")
        for m in MARCAS:
            l[m] |= bool(e[m])
    return leads


def canal_do_lead(canais):
    """Rotulo do canal de um lead para filtro: E-mail, WhatsApp ou os dois."""
    zap = "whatsapp" in canais
    email = any(c != "whatsapp" for c in canais)
    return "E-mail + WhatsApp" if zap and email else ("WhatsApp" if zap else "E-mail")


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


# Leads de teste que a Automatize disparou de verdade e que ficam fora do painel (por id, nunca por nome).
# 196aaa72...: lead "Teste Lead" (origem do cadastro), 1 e-mail da Elaboracao em 28/08 (auditoria de 26/11).
LEADS_TESTE = ("196aaa72-139e-4232-8a34-fe8419f643de",)

# Lead sem fonte_slug cuja `leads.origem` diz de onde veio. kiwify-aprovai: planilha de compradores da
# Kiwify importada em 18/11 (887 leads); ~240 envios da Elaboracao saiam como "sem fonte".
FONTE_POR_ORIGEM = {"kiwify-aprovai": "Kiwify (planilha importada)"}

# Le so `origem` de dados.leads (nunca nome, e-mail ou telefone).
CONSULTA_FRENTES = """
    select e.modalidade, e.dia, e.canal, e.fonte_slug,
           e.aberto_em is not null, e.clicou_em is not null, e.respondeu_em is not null,
           (e.visitou_agenda_em is not null or e.clicou_agendar_em is not null),
           coalesce(e.card_hubspot_em::date >= e.dia, false), coalesce(e.cadastro_em::date >= e.dia, false),
           e.bounce_em is not null, l.origem, e.lead_id
    from dados.envios e
    left join dados.leads l on l.id = e.lead_id
    where e.modalidade in (%s, %s, %s) and e.dia is not null
      and coalesce(e.origem_evento, '') not like 'backfill%%'
      and e.lead_id::text <> all(%s)
"""
CAMPOS_FRENTES = ("modalidade", "dia", "canal", "fonte") + MARCAS

# Plano do alocador no ultimo dia planejado: o que saiu e o que ficou esperando, por frente e canal.
# O plano tem uma VAGA por lead x canal (o mesmo lead pode estar no e-mail e no WhatsApp): a 4a coluna
# conta vagas, a 5a conta cada lead uma vez so (na 1a vaga dele entre as enviadas ou entre as que esperam).
# Em 27/11 a Elaboracao tinha 1.405 vagas esperando para 838 pessoas.
CONSULTA_FILA = """
    with p as (
        select modalidade, status, canal,
               row_number() over (partition by modalidade, lead_id, coalesce(status, '') like 'enviado%%'
                                  order by canal) = 1 as primeira
        from dados.plano_disparo
        where dia = (select max(dia) from dados.plano_disparo)
    )
    select modalidade, status, canal, count(*), count(*) filter (where primeira)
    from p
    group by 1, 2, 3
"""


def envios_frentes(conn):
    """Envios das tres frentes, sem lead_id: modalidade, dia, canal, fonte, marcas de etapa e `lead_hash`
    (o mesmo pseudonimo de sync.hash_id, para cruzar com a reuniao do Incentivador). O id cru nao sai daqui."""
    cur = conn.cursor()
    cur.execute(CONSULTA_FRENTES, (INCENTIVADOR, ELABORACAO, APTOS, list(LEADS_TESTE)))
    return [envio_da_linha(r) for r in cur.fetchall()]


def envio_da_linha(r):
    """Linha da CONSULTA_FRENTES -> envio. Sem fonte_slug, a fonte vem da origem do lead quando ela e conhecida;
    a origem em si nao sai daqui."""
    *campos, origem, lead_id = r
    e = dict(zip(CAMPOS_FRENTES, campos), lead_hash=hash_lead(lead_id))
    if not e["fonte"]:
        e["fonte"] = FONTE_POR_ORIGEM.get(origem)
    return e


def hash_lead(lead_id):
    """Igual a sync.hash_id (sha256 truncado em 12); repetido aqui para o modulo nao depender do sync."""
    return hashlib.sha256(str(lead_id).encode("utf-8")).hexdigest()[:12] if lead_id else ""


def fila_do_dia(conn):
    """{modalidade: {"enviados": mensagens, "na_fila": PESSOAS esperando, "canal_fila": canal com mais vagas
    esperando}}. `enviado_n8n` tambem saiu (antes caia na fila)."""
    cur = conn.cursor()
    cur.execute(CONSULTA_FILA)
    out = {}
    for modalidade, status, canal, vagas, pessoas in cur.fetchall():
        f = out.setdefault(modalidade, {"enviados": 0, "na_fila": 0, "_canais": {}})
        if (status or "").startswith("enviado"):
            f["enviados"] += vagas
        else:
            f["na_fila"] += pessoas
            f["_canais"][canal or "?"] = f["_canais"].get(canal or "?", 0) + vagas
    for f in out.values():
        canais = f.pop("_canais")
        f["canal_fila"] = max(canais, key=canais.get) if canais else ""
    return out


def ultimo_disparo(lista):
    dias = [e["dia"] for e in lista if e["dia"]]
    return max(dias) if dias else None


def dias_uteis_sem_envio(ultimo, hoje):
    """Dias uteis (seg-sex) depois do ultimo envio ate hoje, inclusive."""
    if not ultimo:
        return None
    return sum(1 for i in range(1, (hoje - ultimo).days + 1)
               if (ultimo + datetime.timedelta(days=i)).weekday() < 5)


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
