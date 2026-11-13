"""Aba "Brenda" (antes "Explorar disparos"): o SDR da Automatize nas tres frentes, com filtros.

A mesma IA dispara para as tres frentes (Incentivador, AprovAI/elaboracao e
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
FRENTES = [(sdr.INCENTIVADOR, "Incentivador"), (sdr.ELABORACAO, "AprovAI"), (sdr.APTOS, "Plataforma")]
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
         ("Clicaram ou responderam", "engajaram"), ("Resultado", "resultado")]
TAXAS = [("Entrega", "entregues"), ("Abertura", "abriram"), ("Clique ou resposta", "engajaram"),
         ("Resposta", "responderam"), ("Resultado", "resultado")]


# ---------- base (Python) ----------

def etapas_do_envio(e):
    """Cada etapa conta o envio que chegou nela ou alem: o funil nunca sobe."""
    resultado = e["card"] if e["modalidade"] == sdr.INCENTIVADOR else e["cadastro"]
    engajou = e["clicou"] or e["respondeu"] or e["agenda"] or e["card"] or e["cadastro"]
    abriu = e["abriu"] or engajou
    return {"envios": 1, "entregues": int(abriu or not e["bounce"]), "abriram": int(abriu),
            "engajaram": int(engajou), "responderam": int(e["respondeu"]), "resultado": int(resultado)}


serial = fl.serial


def nome_canal(c):
    return "WhatsApp" if c == "whatsapp" else "E-mail"


def base(envios):
    """Linhas agregadas [serial do dia, frente, canal, fonte, envios..resultado], em ordem de dia."""
    return BASE.agregar(
        (e["dia"], {"frente": NOME_FRENTE[e["modalidade"]], "canal": nome_canal(e["canal"]), "fonte": e["fonte"]},
         etapas_do_envio(e))
        for e in envios if e["dia"] and e["modalidade"] in NOME_FRENTE)


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


def taxas_vivas(flt, dimensao, valores):
    linhas = [[nome] + [F(flt.taxa(etapa, **{dimensao: v})) for v in valores] for nome, etapa in TAXAS]
    return linhas + [["Bounce"] + [F(bounce(flt, **{dimensao: v})) for v in valores]]


def montar_painel(linhas_base, agora, foto=()):
    lista_fontes = fontes(linhas_base)
    recentes = agora.date() - datetime.timedelta(days=2 * JANELA_PADRAO - 1)
    com_volume = BASE.opcoes(linhas_base, "fonte", desde=recentes) or lista_fontes[:1] or [SEM_FONTE]
    ctl = controles(agora.date(), lista_fontes)
    flt = Filtros(ctl)
    p = dp.Painel(ABA, "Brenda · o SDR da Automatize nas tres frentes",
                  f"Atualizado {agora.strftime('%d/%m/%Y %H:%M')} · mude as células claras abaixo; "
                  f"voltam para os últimos {JANELA_PADRAO} dias a cada atualização")
    p.linha(ctl)
    env_a, env_b = flt.soma_p("envios", "A"), flt.soma_p("envios", "B")
    p.linha(fl.CartaoVivo("Envios", env_a, env_b),
            fl.CartaoVivo("Envios por dia", f"{env_a}/{flt.dias('A')}", f"{env_b}/{flt.dias('B')}", dp.FMT_INT),
            fl.CartaoVivo("Taxa de abertura", flt.taxa("abriram", "A"), flt.taxa("abriram", "B"), dp.FMT_PCT),
            fl.CartaoVivo("Taxa de resultado", flt.taxa("resultado", "A"), flt.taxa("resultado", "B"), dp.FMT_PCT))
    p.linha(fl.FunilVivo("Funil do período A", flt, FUNIL,
                         "Resultado: card no HubSpot (Incentivador) ou cadastro (AprovAI e Plataforma)"),
            fl.RoscaViva("Envios por frente no período A",
                         [(n, F(flt.soma_p("envios", frente=n))) for _, n in FRENTES],
                         subtitulo="a mesma IA dispara para as três"))
    p.linha(fl.comparacao_ab("Período A contra período B", flt, FUNIL))
    p.linha(fl.SerieViva("Envios no período A", ["Dia"] + [n for _, n in FRENTES],
                         [[F(flt.sequencia())] + [F(flt.por_ponto("envios", frente=n)) for _, n in FRENTES]],
                         n_linhas=MAX_PONTOS, formato_dominio=dp.FMT_DIA, empilhado=True, rotulos=False,
                         subtitulo="Por dia ou semana (filtro 'Agrupar por'), empilhado por frente", altura=320))
    p.linha(fl.SerieViva("Taxas por canal no período A", ["Taxa"] + [n for _, n in CANAIS],
                         taxas_vivas(flt, "canal", [c for _, c in CANAIS]),
                         formato=dp.FMT_PCT, subtitulo="% dos envios de cada canal"),
            fl.SerieViva("Taxas por frente no período A", ["Taxa"] + [n for _, n in FRENTES],
                         taxas_vivas(flt, "frente", [n for _, n in FRENTES]),
                         formato=dp.FMT_PCT, subtitulo="% dos envios de cada frente"))
    p.linha(fl.por_dimensao("Por fonte do lead no período A", flt, "fonte", com_volume,
                            [("Envios", "soma", "envios"), ("Abertura", "taxa", "abriram"),
                             ("Clique ou resposta", "taxa", "engajaram"), ("Resultado", "soma", "resultado"),
                             ("Taxa de resultado", "taxa", "resultado")], gradiente=[1, 5]))
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


def componentes_visao_geral(envios, fila, hoje):
    """Linhas de componentes para a Visao Geral: como a mesma IA divide os disparos entre as frentes."""
    zap = [e["dia"] for e in envios if e["canal"] == "whatsapp" and e["dia"]]
    nota = f"último WhatsApp em {max(zap).strftime('%d/%m')}" if zap else "sem WhatsApp"
    inicio = hoje - datetime.timedelta(days=27)
    fatias = [(n, sum(1 for e in envios if e["modalidade"] == m and e["dia"] and e["dia"] >= inicio))
              for m, n in FRENTES]
    return [
        (dp.Serie("Capacidade da Brenda por semana (foto de hoje, sem filtro)", ["Semana"] + [n for _, n in FRENTES],
                  divisao_por_semana(envios, hoje), empilhado=True,
                  subtitulo=f"A mesma IA dispara para as três frentes · {nota}"),
         dp.Rosca("Divisão dos últimos 28 dias (sem filtro)", fatias)),
        (dp.Tabela("Fila do alocador hoje (sem filtro)",
                   ["Frente", "Envios por dia (28d)", "Enviados hoje", "Esperando na fila", "Canal da fila"],
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
    linhas = base(envios)
    sh = sync.get_sheets_client().open_by_key(sync.SPREADSHEET_ID)
    svc = sync.build_sheets_service()
    renomear_abas_antigas(sh, svc, sync.SPREADSHEET_ID)
    publicar_base(sh, svc, sync.SPREADSHEET_ID, linhas)
    foto = componentes_visao_geral(envios, fila, agora.date())
    montar_painel(linhas, agora, foto).publicar(sh, svc, sync.SPREADSHEET_ID, indice=1)
    print(f"Brenda: aba publicada ({len(linhas)} linhas na base).")


if __name__ == "__main__":
    main()
