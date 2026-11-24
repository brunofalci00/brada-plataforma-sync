"""Aba "Brenda" (antes "Explorar disparos"): o SDR da Automatize nas tres frentes, com filtros.

A mesma IA dispara para as tres frentes (Incentivador, Elaboracao e
Plataforma/aptos). A aba escondida `base_disparos` guarda os envios agregados por
dia x frente x canal x fonte (sem lead, sem PII). No topo da aba ficam as celulas
de filtro (periodo A, periodo B para comparar, frente, canal, fonte, agrupamento);
as formulas da aba `dados_explorar_disparos` somam a base conforme esses filtros e
os graficos se redesenham na hora, sem rodar nada.

Os filtros voltam ao padrao (ultimos 30 dias contra os 30 anteriores) a cada
atualizacao diaria e valem para todo mundo que abrir a planilha.

No fim da aba fica a foto de hoje, sem filtro: como a Brenda divide a capacidade
entre as frentes por semana e o que esta esperando na fila do alocador (saiu da
Visao Geral na fase 3).
"""

import datetime

import dashboard_painel as dp
import painel_filtros as fl
import sdr_automatize as sdr
from painel_filtros import F, MAX_PONTOS, literal, seguro  # noqa: F401  (API usada pelos testes)

ABA = "Brenda"
ABA_ANTIGA = "Explorar disparos"
ABA_BASE = "base_disparos"
FRENTES = [(sdr.INCENTIVADOR, "Incentivador"), (sdr.ELABORACAO, "Elaboracao"), (sdr.APTOS, "Plataforma")]
NOME_FRENTE = dict(FRENTES)
CANAIS = [("email", "E-mail"), ("whatsapp", "WhatsApp")]
SEM_FONTE = fl.SEM_VALOR
TODAS, TODOS = fl.TODAS, fl.TODOS
DIA, SEMANA = fl.DIA, fl.SEMANA
JANELA_PADRAO = fl.JANELA_PADRAO

BASE = fl.Base(ABA_BASE,
               [("frente", "Frente", TODAS), ("canal", "Canal", TODOS), ("fonte", "Fonte", TODAS)],
               [("envios", "Envios"), ("entregues", "Entregues"), ("abriram", "Abriram"),
                ("engajaram", "Clicaram ou responderam"), ("responderam", "Responderam"),
                ("resultado", "Resultado")])
CABECALHO_BASE = BASE.cabecalho
FUNIL = [("Envios", "envios"), ("Entregues", "entregues"), ("Abriram", "abriram"),
         ("Clicaram ou responderam", "engajaram"), ("Conversao final", "resultado")]
TAXAS = [("Entrega", "entregues"), ("Abertura", "abriram"), ("Clique ou resposta", "engajaram"),
         ("Resposta", "responderam"), ("Conversao final", "resultado")]


# ---------- base (Python) ----------

def etapas_do_envio(e):
    """Cada etapa conta o envio que chegou nela ou alem: o funil nunca sobe.

    Resultado de cada frente (decisoes de 23/11): Incentivador = reuniao (o card do HubSpot nasce sozinho ao
    abrir a agenda); Elaboracao = assinou o AprovAI; Plataforma = cadastro."""
    resultado = {sdr.INCENTIVADOR: e.get("reuniao", False),
                 sdr.ELABORACAO: e.get("assinou", False)}.get(e["modalidade"], e["cadastro"])
    engajou = e["clicou"] or e["respondeu"] or e["agenda"] or e["card"] or e["cadastro"]
    abriu = e["abriu"] or engajou
    return {"envios": 1, "entregues": int(abriu or not e["bounce"]), "abriram": int(abriu),
            "engajaram": int(engajou), "responderam": int(e["respondeu"]), "resultado": int(resultado)}


serial = fl.serial


def nome_canal(c):
    return "WhatsApp" if c == "whatsapp" else "E-mail"


def base(envios):
    """Linhas agregadas [serial do dia, frente, canal, fonte, envios..resultado], em ordem de dia.
    O resultado conta 1 por lead (no primeiro envio dele): 3 e-mails para quem se cadastrou nao viram 3 cadastros."""
    def registros():
        com_resultado = set()
        for e in sorted((e for e in envios if e["dia"] and e["modalidade"] in NOME_FRENTE), key=lambda e: e["dia"]):
            etapas = etapas_do_envio(e)
            chave = (e["modalidade"], e.get("lead_hash"))
            if etapas["resultado"] and e.get("lead_hash"):
                if chave in com_resultado:
                    etapas = dict(etapas, resultado=0)
                com_resultado.add(chave)
            yield (e["dia"], {"frente": NOME_FRENTE[e["modalidade"]], "canal": nome_canal(e["canal"]),
                              "fonte": e["fonte"]}, etapas)
    return BASE.agregar(registros())


def fontes(linhas):
    """Fontes por volume; nome fora do padrao nao vira filtro nem linha (entraria no texto da formula)."""
    return BASE.opcoes(linhas, "fonte")


class Filtros(fl.Filtros):
    def __init__(self, ctl):
        super().__init__(ctl, BASE)


# ---------- aba ----------

def controles(hoje, lista_fontes):
    return fl.controles(ABA, hoje, [
        fl.campo_lista("frente", "Frente", [n for _, n in FRENTES], span=2),
        fl.campo_lista("canal", "Canal", [n for _, n in CANAIS], todos=TODOS),
        fl.campo_lista("fonte", "Fonte do lead", lista_fontes, span=2)])


def bounce(flt, **fixos):
    """Envios que voltaram (nao entregues) sobre os envios."""
    return f'IFERROR(1-{flt.soma_p("entregues", **fixos)}/{flt.soma_p("envios", **fixos)};0)'


MIN_ENVIOS_TAXA = 20  # abaixo disso a taxa engana (2 envios entregues = "100% de entrega")
# O WhatsApp nao mede abertura nem devolve "e-mail invalido": a taxa sairia 100% e enganaria.
NAO_MEDE = {("WhatsApp", "abriram"), ("WhatsApp", "voltou")}


def taxas_vivas(flt, dimensao, valores):
    """Taxas por valor da dimensao; valor com menos de MIN_ENVIOS_TAXA envios no periodo A fica vazio."""
    def cel(formula, v, etapa):
        if (v, etapa) in NAO_MEDE:
            return ""
        return F(f'IF({flt.soma_p("envios", **{dimensao: v})}<{MIN_ENVIOS_TAXA};"";{formula})')
    linhas = [[nome] + [cel(flt.taxa(etapa, **{dimensao: v}), v, etapa) for v in valores] for nome, etapa in TAXAS]
    return linhas + [["Voltou (e-mail invalido)"] + [cel(bounce(flt, **{dimensao: v}), v, "voltou")
                                                     for v in valores]]


def montar_painel(linhas_base, agora, foto=(), saude=None):
    lista_fontes = fontes(linhas_base)
    recentes = agora.date() - datetime.timedelta(days=2 * JANELA_PADRAO - 1)
    com_volume = BASE.opcoes(linhas_base, "fonte", desde=recentes) or lista_fontes[:1] or [SEM_FONTE]
    ctl = controles(agora.date(), lista_fontes)
    flt = Filtros(ctl)
    p = dp.Painel(ABA, "Brenda · a prospeccao automatica nas tres frentes",
                  f"Atualizado {agora.strftime('%d/%m/%Y %H:%M')} · mude as células claras abaixo; "
                  f"voltam para os últimos {JANELA_PADRAO} dias a cada atualização")
    p.linha(ctl)
    if saude is not None:
        p.linha(saude)
    env_a, env_b = flt.soma_p("envios", "A"), flt.soma_p("envios", "B")
    p.linha(fl.CartaoVivo("Mensagens enviadas", env_a, env_b),
            fl.CartaoVivo("Envios por dia", f"{env_a}/{flt.dias('A')}", f"{env_b}/{flt.dias('B')}", dp.FMT_INT),
            fl.CartaoVivo("Abertura dos e-mails", flt.taxa("abriram", "A", canal="E-mail"),
                          flt.taxa("abriram", "B", canal="E-mail"), dp.FMT_PCT),
            fl.CartaoVivo("Conversao final", flt.taxa("resultado", "A"), flt.taxa("resultado", "B"), dp.FMT_PCT))
    p.linha(fl.FunilVivo("Funil do período A", flt, FUNIL,
                         "Conversao final: reuniao (Incentivador), assinatura do AprovAI (Elaboracao) ou cadastro "
                         "(Plataforma), 1 por contato · WhatsApp entregue conta como aberto · escolha uma frente no filtro"),
            fl.SerieViva("Taxas por frente no período A", ["Taxa"] + [n for _, n in FRENTES],
                         taxas_vivas(flt, "frente", [n for _, n in FRENTES]),
                         formato=dp.FMT_PCT, subtitulo="% das mensagens de cada frente · vazio = menos de 20 no periodo"))
    p.linha(fl.comparacao_ab("Período A contra período B", flt, FUNIL, maturacao=fl.MATURACAO_DIAS))
    p.linha(fl.SerieViva("Envios no período A", ["Dia"] + [n for _, n in FRENTES],
                         [[F(flt.sequencia())] + [F(flt.por_ponto("envios", frente=n)) for _, n in FRENTES]],
                         n_linhas=MAX_PONTOS, formato_dominio=dp.FMT_DIA, empilhado=True, rotulos=False,
                         subtitulo="Por dia ou semana (filtro 'Agrupar por'), empilhado por frente", altura=320))
    p.linha(fl.SerieViva("Taxas por canal no período A", ["Taxa"] + [n for _, n in CANAIS],
                         taxas_vivas(flt, "canal", [c for _, c in CANAIS]), formato=dp.FMT_PCT,
                         subtitulo="% das mensagens de cada canal · WhatsApp nao mede abertura nem e-mail invalido"))
    p.linha(fl.por_dimensao("Por fonte do lead no período A", flt, "fonte", com_volume,
                            [("Envios", "soma", "envios"), ("Abertura dos e-mails", "taxa", "abriram", {"canal": "E-mail"}),
                             ("Clique ou resposta", "taxa", "engajaram"), ("Conversao final", "soma", "resultado"),
                             ("Conversao final (%)", "taxa", "resultado")], gradiente=[1, 5], cabecalho_dim="Fonte do contato"))
    for componentes in foto:
        p.linha(*componentes)
    return p


def divisao_por_semana(envios, hoje, n=12):
    """[[segunda dd/mm, incentivador, aprovai, plataforma]] das ultimas n semanas."""
    cont = {s: dict.fromkeys(NOME_FRENTE, 0) for s in sdr.semanas(hoje, n)}
    for e in envios:
        if e["dia"] and e["modalidade"] in NOME_FRENTE and sdr.segunda(e["dia"]) in cont:
            cont[sdr.segunda(e["dia"])][e["modalidade"]] += 1
    return [[s.strftime("%d/%m")] + [c[m] for m, _ in FRENTES] for s, c in cont.items()]


def linhas_fila(envios, fila, hoje, janela=28):
    """Tabela da Visao Geral: [frente, envios por dia na janela, enviados hoje, na fila, canal da fila]."""
    inicio = hoje - datetime.timedelta(days=janela - 1)
    out = []
    for m, nome in FRENTES:
        recentes = sum(1 for e in envios if e["modalidade"] == m and e["dia"] and e["dia"] >= inicio)
        f = fila.get(m, {"enviados": 0, "na_fila": 0, "canal_fila": ""})
        canal = nome_canal(f["canal_fila"]) if f["canal_fila"] else "-"
        out.append([nome, recentes / janela, f["enviados"], f["na_fila"], canal])
    return out


LIMITE_PARADA = 2  # dias uteis sem envio para a frente contar como parada


def linhas_saude(envios, hoje, frentes=None, fila=None):
    """[frente, ultimo envio, dias uteis sem envio, envios 7d, na fila, situacao] por frente."""
    out = []
    for m, nome in (frentes or FRENTES):
        dias = [e["dia"] for e in envios if e.get("modalidade", m) == m and e["dia"]]
        ultimo = max(dias) if dias else None
        parado = sdr.dias_uteis_sem_envio(ultimo, hoje)
        sete = sum(1 for d in dias if d > hoje - datetime.timedelta(days=7))
        situacao = "SEM ENVIO" if ultimo is None else ("PARADA" if parado > LIMITE_PARADA else "Disparando")
        linha = [nome, ultimo.strftime("%d/%m") if ultimo else "-", parado if parado is not None else "-", sete]
        if fila is not None:
            linha.append(fila.get(m, {}).get("na_fila", 0))
        out.append(linha + [situacao])
    return out


def resumo_saude(envios, hoje):
    """Chaves brenda_<frente>_dias_sem_envio, _envios_7d e _envios_7d_antes (8 a 14 dias atras) para o alerta
    da Visao Geral: parada e queda de volume."""
    out = {}
    for (m, nome), linha in zip(FRENTES, linhas_saude(envios, hoje)):
        chave = dp.slug(nome)
        out[f"brenda_{chave}_dias_sem_envio"] = linha[2] if isinstance(linha[2], int) else 99
        out[f"brenda_{chave}_envios_7d"] = linha[3]
        out[f"brenda_{chave}_envios_7d_antes"] = sum(
            1 for e in envios if e.get("modalidade") == m and e["dia"]
            and hoje - datetime.timedelta(days=14) < e["dia"] <= hoje - datetime.timedelta(days=7))
    return out


def tabela_saude(envios, hoje, frentes=None, fila=None,
                 titulo=f"A Brenda está disparando? (hoje, sem filtro · PARADA = mais de {LIMITE_PARADA} dias uteis sem envio)"):
    cab = ["Frente", "Ultimo envio", "Dias uteis sem envio", "Envios em 7 dias"] + (
        ["Esperando na fila"] if fila is not None else []) + ["Situacao"]
    return dp.Tabela(titulo, cab, linhas_saude(envios, hoje, frentes, fila),
                     formatos=[None, None, dp.FMT_INT, dp.FMT_INT] + ([dp.FMT_INT] if fila is not None else []) + [None],
                     gradiente=[2])


def componentes_visao_geral(envios, fila, hoje):
    """Linhas de componentes para a Visao Geral: como a mesma IA divide os disparos entre as frentes."""
    zap = [e["dia"] for e in envios if e["canal"] == "whatsapp" and e["dia"]]
    nota = f"último WhatsApp em {max(zap).strftime('%d/%m')}" if zap else "sem WhatsApp"
    return [
        (dp.Serie("Capacidade da Brenda por semana (foto de hoje, sem filtro)", ["Semana"] + [n for _, n in FRENTES],
                  divisao_por_semana(envios, hoje), empilhado=True,
                  subtitulo=f"A mesma IA dispara para as três frentes · {nota}"),),
        (dp.Tabela("Fila de envio hoje (sem filtro)",
                   ["Frente", "Mensagens por dia (28d)", "Enviadas hoje", "Esperando na fila", "Canal da fila"],
                   linhas_fila(envios, fila, hoje),
                   formatos=[None, {"type": "NUMBER", "pattern": "0.0"}, dp.FMT_INT, dp.FMT_INT, None],
                   gradiente=[3]),),
    ]


def renomear_abas_antigas(sh, svc, spreadsheet_id):
    """Primeira rodada depois da fase 3: a aba antiga e a de dados viram "Brenda" (mesmo gid, links valem)."""
    titulos = {w.title: w.id for w in sh.worksheets()}
    pares = [(ABA_ANTIGA, ABA), ("dados_" + dp.slug(ABA_ANTIGA), "dados_" + dp.slug(ABA))]
    reqs = [{"updateSheetProperties": {"properties": {"sheetId": titulos[velho], "title": novo}, "fields": "title"}}
            for velho, novo in pares if velho in titulos and novo not in titulos]
    if reqs:
        svc.spreadsheets().batchUpdate(spreadsheetId=spreadsheet_id, body={"requests": reqs}).execute()
        print(f"  {len(reqs)} aba(s) renomeada(s) para '{ABA}'")


def leads_com_reuniao(sh, aba="raw_disparo_incentivador"):
    """Hashes dos leads do Incentivador cujo card ja passou por reuniao (HubSpot ao vivo, gravado pela aba
    Incentivador, que roda antes no CI). Sem a aba, ninguem conta como reuniao e o aviso sai no log."""
    import gspread
    try:
        linhas = sh.worksheet(aba).get_all_values()
    except gspread.exceptions.WorksheetNotFound:
        print(f"[AVISO] {aba} ausente: reunioes do Incentivador saem como 0 na aba Brenda")
        return frozenset()
    if not linhas:
        return frozenset()
    cab = linhas[0]
    if "lead_id_hash" not in cab or "reuniao_ou_alem" not in cab:
        print(f"[AVISO] {aba} com cabecalho inesperado: reunioes do Incentivador saem como 0 na aba Brenda")
        return frozenset()
    i, j = cab.index("lead_id_hash"), cab.index("reuniao_ou_alem")
    return frozenset(l[i] for l in linhas[1:] if len(l) > j and l[j] == "sim")


def hashes_da_aba(sh, aba):
    """Coluna lead_id_hash de uma aba escondida gravada por outro sync que roda antes no CI."""
    import gspread
    try:
        linhas = sh.worksheet(aba).get_all_values()
    except gspread.exceptions.WorksheetNotFound:
        print(f"[AVISO] {aba} ausente: o resultado dessa frente sai como 0 na aba Brenda")
        return frozenset()
    if not linhas or not linhas[0] or linhas[0][0] != "lead_id_hash":
        print(f"[AVISO] {aba} vazia ou com cabecalho inesperado: o resultado dessa frente sai como 0")
        return frozenset()
    return frozenset(l[0] for l in linhas[1:] if l and l[0])


def cadastros_pelo_link(sh, aba="raw_users"):
    """lead_hash das contas da plataforma que chegaram pelo link rastreado da Brenda (utm_term = lead_id).

    A Automatize so marca cadastro quando o e-mail casa; a aba Plataforma tambem casa pelo link.
    Usar os dois aqui faz a aba Brenda contar os mesmos cadastros que a Plataforma."""
    import gspread
    try:
        linhas = sh.worksheet(aba).get_all_values()
    except gspread.exceptions.WorksheetNotFound:
        print(f"[AVISO] {aba} ausente: cadastro da Brenda so pelo que a Automatize marcou")
        return frozenset()
    if not linhas or "utm_term" not in linhas[0]:
        print(f"[AVISO] {aba} sem a coluna utm_term: cadastro da Brenda so pelo que a Automatize marcou")
        return frozenset()
    i = linhas[0].index("utm_term")
    return frozenset(sdr.hash_lead(l[i].strip().lower()) for l in linhas[1:] if len(l) > i and l[i].strip())


def marcar_resultados(envios, reunioes, assinaram, cadastrados):
    """Junta os resultados que vem de outras abas: reuniao (HubSpot), assinatura (Asaas) e cadastro pelo link."""
    def marcar(e):
        h = e.get("lead_hash")
        cadastro = e["cadastro"] or (e["modalidade"] == sdr.APTOS and h in cadastrados)
        return dict(e, reuniao=h in reunioes, assinou=h in assinaram, cadastro=cadastro)
    return [marcar(e) for e in envios]


def publicar_base(sh, svc, spreadsheet_id, linhas):
    BASE.publicar(sh, svc, spreadsheet_id, linhas)


def main():
    import sync
    agora = datetime.datetime.now(sync.BRT)
    conn = sdr.conectar()
    try:
        envios = sdr.envios_frentes(conn)
        fila = sdr.fila_do_dia(conn)
    finally:
        conn.close()
    sh = sync.get_sheets_client().open_by_key(sync.SPREADSHEET_ID)
    reunioes = leads_com_reuniao(sh)
    assinaram = hashes_da_aba(sh, "raw_elaboracao_assinantes")
    envios = marcar_resultados(envios, reunioes, assinaram, cadastros_pelo_link(sh))
    linhas = base(envios)
    svc = sync.build_sheets_service()
    renomear_abas_antigas(sh, svc, sync.SPREADSHEET_ID)
    publicar_base(sh, svc, sync.SPREADSHEET_ID, linhas)
    foto = componentes_visao_geral(envios, fila, agora.date())
    saude = tabela_saude(envios, agora.date(), fila=fila)
    dp.gravar_resumo(sh, svc, sync.SPREADSHEET_ID, resumo_saude(envios, agora.date()), agora)
    montar_painel(linhas, agora, foto, saude).publicar(sh, svc, sync.SPREADSHEET_ID, indice=1)
    print(f"Brenda: aba publicada ({len(linhas)} linhas na base).")


if __name__ == "__main__":
    main()
