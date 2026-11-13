"""Aba "Explorar disparos": o funil da Brenda (SDR da Automatize) com filtros.

A mesma IA dispara para as tres frentes (Incentivador, AprovAI/elaboracao e
Plataforma/aptos). A aba escondida `base_disparos` guarda os envios agregados por
dia x frente x canal x fonte (sem lead, sem PII). No topo da aba ficam as celulas
de filtro (periodo A, periodo B para comparar, frente, canal, fonte, agrupamento);
as formulas da aba `dados_explorar_disparos` somam a base conforme esses filtros e
os graficos se redesenham na hora, sem rodar nada.

Os filtros voltam ao padrao (ultimos 30 dias contra os 30 anteriores) a cada
atualizacao diaria e valem para todo mundo que abrir a planilha.
"""

import datetime
import re

import dashboard_painel as dp
import sdr_automatize as sdr

ABA = "Explorar disparos"
ABA_BASE = "base_disparos"
FRENTES = [(sdr.INCENTIVADOR, "Incentivador"), (sdr.ELABORACAO, "AprovAI"), (sdr.APTOS, "Plataforma")]
NOME_FRENTE = dict(FRENTES)
CANAIS = [("email", "E-mail"), ("whatsapp", "WhatsApp")]
SEM_FONTE = "sem fonte"
TODAS, TODOS = "Todas", "Todos"
DIA, SEMANA = "Dia", "Semana"
MAX_PONTOS = 366
JANELA_PADRAO = 30

CABECALHO_BASE = ["Dia", "Frente", "Canal", "Fonte", "Envios", "Entregues", "Abriram",
                  "Clicaram ou responderam", "Responderam", "Resultado"]
COLUNA = {"envios": "E", "entregues": "F", "abriram": "G", "engajaram": "H", "responderam": "I",
          "resultado": "J"}
FUNIL = [("Envios", "envios"), ("Entregues", "entregues"), ("Abriram", "abriram"),
         ("Clicaram ou responderam", "engajaram"), ("Resultado", "resultado")]
TAXAS = [("Entrega", "entregues"), ("Abertura", "abriram"), ("Clique ou resposta", "engajaram"),
         ("Resposta", "responderam"), ("Resultado", "resultado")]
EPOCA = datetime.date(1899, 12, 30)


# ---------- base (Python) ----------

def etapas_do_envio(e):
    """Cada etapa conta o envio que chegou nela ou alem: o funil nunca sobe."""
    resultado = e["card"] if e["modalidade"] == sdr.INCENTIVADOR else e["cadastro"]
    engajou = e["clicou"] or e["respondeu"] or e["agenda"] or e["card"] or e["cadastro"]
    abriu = e["abriu"] or engajou
    return {"envios": 1, "entregues": int(abriu or not e["bounce"]), "abriram": int(abriu),
            "engajaram": int(engajou), "responderam": int(e["respondeu"]), "resultado": int(resultado)}


def serial(dia):
    return (dia - EPOCA).days


def nome_canal(c):
    return "WhatsApp" if c == "whatsapp" else "E-mail"


def base(envios):
    """Linhas agregadas [serial do dia, frente, canal, fonte, envios..resultado], em ordem de dia."""
    soma = {}
    for e in envios:
        if not e["dia"] or e["modalidade"] not in NOME_FRENTE:
            continue
        chave = (e["dia"], NOME_FRENTE[e["modalidade"]], nome_canal(e["canal"]), e["fonte"] or SEM_FONTE)
        acc = soma.setdefault(chave, dict.fromkeys(COLUNA, 0))
        for k, v in etapas_do_envio(e).items():
            acc[k] += v
    return [[serial(d), f, c, fo] + [acc[k] for k in COLUNA] for (d, f, c, fo), acc in sorted(soma.items())]


SEGURO = re.compile(r"[\w .\-/()]{1,60}")


def seguro(texto):
    return bool(SEGURO.fullmatch(str(texto)))


def literal(texto):
    """Texto de fora (ex.: fonte do banco) dentro de formula: so caracteres simples, aspas escapadas."""
    if not seguro(texto):
        raise ValueError(f"valor fora do padrao para formula: {texto!r}")
    return '"' + str(texto).replace('"', '""') + '"'


def fontes(linhas):
    """Fontes por volume; nome fora do padrao nao vira filtro nem linha (entraria no texto da formula)."""
    total = {}
    for l in linhas:
        total[l[3]] = total.get(l[3], 0) + l[4]
    fora = sorted(f for f in total if not seguro(f))
    if fora:
        print(f"[AVISO] fontes com nome fora do padrao ficaram fora do filtro: {len(fora)}")
    return sorted((f for f in total if seguro(f)), key=lambda f: (-total[f], f))


# ---------- formulas (planilha pt-BR: ';' entre argumentos) ----------

def _col(letra):
    return f"{ABA_BASE}!${letra}:${letra}"


class Filtros:
    """Monta SUMIFS sobre a base lendo as celulas de filtro da aba."""

    def __init__(self, ctl):
        self.ctl = ctl

    def r(self, chave):
        return self.ctl.ref(chave)

    def periodo(self, p):
        return self.r(f"de_{p}"), self.r(f"ate_{p}")

    def _criterios(self, frente, canal, fonte):
        """None = segue o filtro da aba; texto = valor fixo; False = ignora a coluna."""
        partes = []
        for letra, valor, chave, todos in (("B", frente, "frente", TODAS), ("C", canal, "canal", TODOS),
                                           ("D", fonte, "fonte", TODAS)):
            if valor is False:
                continue
            if valor is None:
                crit = f'IF({self.r(chave)}="{todos}";"*";{self.r(chave)})'
            else:
                crit = literal(valor)
            partes += [_col(letra), crit]
        return partes

    def soma(self, etapa, de, ate, frente=None, canal=None, fonte=None):
        partes = [_col(COLUNA[etapa]), _col("A"), f'">="&{de}', _col("A"), f'"<="&{ate}']
        return "SUMIFS(" + ";".join(partes + self._criterios(frente, canal, fonte)) + ")"

    def soma_p(self, etapa, p="A", **kw):
        de, ate = self.periodo(p)
        return self.soma(etapa, de, ate, **kw)

    def taxa(self, etapa, p="A", **kw):
        return f"IFERROR({self.soma_p(etapa, p, **kw)}/{self.soma_p('envios', p, **kw)};0)"

    def dias(self, p="A"):
        de, ate = self.periodo(p)
        return f"MAX({ate}-{de}+1;1)"

    def sequencia(self):
        """Dias (ou segundas-feiras) do periodo A, ate MAX_PONTOS pontos."""
        de, ate = self.periodo("A")
        seg = f"{de}-WEEKDAY({de};3)"
        dia = f"SEQUENCE(MIN({ate}-{de}+1;{MAX_PONTOS});1;{de})"
        semana = f"SEQUENCE(MIN(INT(({ate}-{seg})/7)+1;{MAX_PONTOS});1;{seg};7)"
        return f'IF({self.r("agrupar")}="{SEMANA}";{semana};{dia})'

    def por_ponto(self, frente):
        de, ate = self.periodo("A")
        fim = f'IF({self.r("agrupar")}="{SEMANA}";d+6;d)'
        filtro = self.r("frente")
        soma = self.soma("envios", f"MAX(d;{de})", f"MIN({fim};{ate})", frente=frente)
        return f'IFERROR(MAP({self.sequencia()};LAMBDA(d;OR({filtro}="{TODAS}";{filtro}="{frente}")*{soma}));"")'


def F(texto):
    return dp.Formula("=" + texto)


def rotulo(valor, topo=None):
    if topo is None:
        return F(f'TEXT({valor};"#,##0")')
    return F(f'TEXT({valor};"#,##0")&" · "&TEXT(IFERROR({valor}/{topo};0);"0.0%")')


# ---------- componentes vivos (numeros saem das formulas, nao do Python) ----------

class FunilVivo(dp.Funil):
    """Barra = % do topo (eixo fixo ate 130%, nada corta); rotulo = 'quantidade · %'."""

    def __init__(self, titulo, flt, subtitulo):
        super().__init__(titulo, [(n, 1) for n, _ in FUNIL], subtitulo=subtitulo)
        self.flt = flt

    def bloco(self):
        topo = self.flt.soma_p("envios")
        linhas = [["Etapa", "% do topo", "Rotulo"]]
        for i, (nome, etapa) in enumerate(FUNIL):
            v = self.flt.soma_p(etapa)
            pct = F(f"IF({topo}>0;1;0)") if i == 0 else F(f"IFERROR({v}/{topo};0)")
            linhas.append([nome, pct, rotulo(v) if i == 0 else rotulo(v, topo)])
        return linhas

    def formatos_dados(self, r0):
        return [(r0 + 1 + i, 1, dp.FMT_PCT) for i in range(len(FUNIL))]


class CartaoVivo(dp.Cartao):
    def __init__(self, titulo, formula_a, formula_b, formato=dp.FMT_INT):
        super().__init__(titulo, 0, formato, base=0, descricao_base="vs período B")
        self.fa, self.fb = formula_a, formula_b

    def bloco(self):
        return [[self.titulo], [F(self.fa), "", F(self.fb)]]


class SerieViva(dp.Serie):
    """Serie cujas celulas sao formulas; `n_linhas` reserva espaco (a sequencia de dias se espalha)."""

    def __init__(self, titulo, cabecalho, linhas_formula, n_linhas=None, formato_dominio=None, **kw):
        n = n_linhas or len(linhas_formula)
        super().__init__(titulo, cabecalho, [[""] + [0] * (len(cabecalho) - 1)] * n, **kw)
        self.linhas_formula, self.formato_dominio = linhas_formula, formato_dominio

    def bloco(self):
        vazias = [[] for _ in range(len(self.linhas) - len(self.linhas_formula))]
        return [list(self.cabecalho)] + self.linhas_formula + vazias

    def formatos_dados(self, r0):
        out = []
        if self.formato:
            out += [(r0 + 1 + i, c, self.formato) for i in range(len(self.linhas_formula))
                    for c in range(1, len(self.cabecalho))]
        if self.formato_dominio:
            out += [(r0 + 1 + i, 0, self.formato_dominio) for i in range(len(self.linhas))]
        return out


class RoscaViva(dp.Rosca):
    def __init__(self, titulo, fatias_formula, subtitulo=None):
        super().__init__(titulo, [(n, 1) for n, _ in fatias_formula], subtitulo=subtitulo)
        self.fatias_formula = fatias_formula

    def bloco(self):
        return [["Categoria", "Valor"]] + [[n, f] for n, f in self.fatias_formula]


# ---------- aba ----------

def controles(hoje, lista_fontes):
    a_ate, a_de = hoje, hoje - datetime.timedelta(days=JANELA_PADRAO - 1)
    b_ate, b_de = a_de - datetime.timedelta(days=1), a_de - datetime.timedelta(days=JANELA_PADRAO)
    return dp.Controles(ABA, [
        dp.Campo("de_A", "Período A: de", serial(a_de), data=True),
        dp.Campo("ate_A", "até", serial(a_ate), data=True),
        dp.Campo("de_B", "Período B: de", serial(b_de), data=True),
        dp.Campo("ate_B", "até", serial(b_ate), data=True),
        dp.Campo("frente", "Frente", TODAS, span=2, opcoes=[TODAS] + [n for _, n in FRENTES]),
        dp.Campo("canal", "Canal", TODOS, opcoes=[TODOS] + [n for _, n in CANAIS]),
        dp.Campo("fonte", "Fonte do lead", TODAS, span=2, opcoes=[TODAS] + lista_fontes),
        dp.Campo("agrupar", "Agrupar por", DIA, opcoes=[DIA, SEMANA]),
    ])


def montar_painel(linhas_base, agora):
    lista_fontes = fontes(linhas_base)
    ctl = controles(agora.date(), lista_fontes)
    flt = Filtros(ctl)
    p = dp.Painel(ABA, "Disparos da Brenda · explorar com filtros",
                  f"Atualizado {agora.strftime('%d/%m/%Y %H:%M')} · mude as células claras abaixo; "
                  f"voltam para os últimos {JANELA_PADRAO} dias a cada atualização")
    p.linha(ctl)

    def ab(etapa_ou_expr):
        return [flt.soma_p(etapa_ou_expr, "A"), flt.soma_p(etapa_ou_expr, "B")]

    env_a, env_b = ab("envios")
    p.linha(CartaoVivo("Envios", env_a, env_b),
            CartaoVivo("Envios por dia", f"{env_a}/{flt.dias('A')}", f"{env_b}/{flt.dias('B')}", dp.FMT_INT),
            CartaoVivo("Taxa de abertura", flt.taxa("abriram", "A"), flt.taxa("abriram", "B"), dp.FMT_PCT),
            CartaoVivo("Taxa de resultado", flt.taxa("resultado", "A"), flt.taxa("resultado", "B"), dp.FMT_PCT))
    p.linha(FunilVivo("Funil do período A", flt,
                      "Resultado: card no HubSpot (Incentivador) ou cadastro (AprovAI e Plataforma)"),
            RoscaViva("Envios por frente no período A",
                      [(n, F(flt.soma_p("envios", frente=n))) for _, n in FRENTES],
                      subtitulo="a mesma IA dispara para as três"))
    comparacao = []
    for nome, etapa in FUNIL:
        a, b = ab(etapa)
        comparacao.append([nome, F(a), F(f"IFERROR({a}/{env_a};0)"), F(b), F(f"IFERROR({b}/{env_b};0)"),
                           F(f'IFERROR({a}/{b}-1;"")')])
    p.linha(dp.Tabela("Período A contra período B", ["Etapa", "A", "% do topo A", "B", "% do topo B", "A sobre B"],
                      comparacao, formatos=[None, dp.FMT_INT, dp.FMT_PCT, dp.FMT_INT, dp.FMT_PCT, dp.FMT_PCT]))
    p.linha(SerieViva("Envios no período A", ["Dia"] + [n for _, n in FRENTES],
                      [[F(flt.sequencia())] + [F(flt.por_ponto(n)) for _, n in FRENTES]],
                      n_linhas=MAX_PONTOS, formato_dominio=dp.FMT_DIA, empilhado=True, rotulos=False,
                      subtitulo="Por dia ou semana (filtro 'Agrupar por'), empilhado por frente", altura=320))
    p.linha(SerieViva("Taxas por canal no período A", ["Taxa"] + [n for _, n in CANAIS],
                      [[nome] + [F(flt.taxa(etapa, canal=c)) for _, c in CANAIS] for nome, etapa in TAXAS],
                      formato=dp.FMT_PCT, subtitulo="% dos envios de cada canal"),
            SerieViva("Taxas por frente no período A", ["Taxa"] + [n for _, n in FRENTES],
                      [[nome] + [F(flt.taxa(etapa, frente=n)) for _, n in FRENTES] for nome, etapa in TAXAS],
                      formato=dp.FMT_PCT, subtitulo="% dos envios de cada frente"))
    por_fonte = []
    for fo in lista_fontes:
        env = flt.soma_p("envios", fonte=fo)
        por_fonte.append([fo, F(env), F(flt.taxa("abriram", fonte=fo)), F(flt.taxa("engajaram", fonte=fo)),
                          F(flt.soma_p("resultado", fonte=fo)), F(flt.taxa("resultado", fonte=fo))])
    p.linha(dp.Tabela("Por fonte do lead no período A",
                      ["Fonte", "Envios", "Abertura", "Clique ou resposta", "Resultado", "Taxa de resultado"],
                      por_fonte, formatos=[None, dp.FMT_INT, dp.FMT_PCT, dp.FMT_PCT, dp.FMT_INT, dp.FMT_PCT],
                      gradiente=[1, 5]))
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
        (dp.Serie("Disparos da Brenda por semana", ["Semana"] + [n for _, n in FRENTES],
                  divisao_por_semana(envios, hoje), empilhado=True,
                  subtitulo=f"A mesma IA dispara para as três frentes · {nota}"),
         dp.Rosca("Divisão dos últimos 28 dias", fatias)),
        (dp.Tabela("Fila do alocador da Brenda hoje",
                   ["Frente", "Envios por dia (28d)", "Enviados hoje", "Esperando na fila", "Canal da fila"],
                   linhas_fila(envios, fila, hoje),
                   formatos=[None, {"type": "NUMBER", "pattern": "0.0"}, dp.FMT_INT, dp.FMT_INT, None],
                   gradiente=[3]),),
    ]


def publicar_base(sh, svc, spreadsheet_id, linhas):
    """Aba escondida com a base agregada. Texto vai RAW (nunca vira formula)."""
    import gspread
    try:
        ws = sh.worksheet(ABA_BASE)
    except gspread.exceptions.WorksheetNotFound:
        ws = sh.add_worksheet(title=ABA_BASE, rows=len(linhas) + 10, cols=len(CABECALHO_BASE))
    if ws.row_count < len(linhas) + 10:
        ws.resize(rows=len(linhas) + 10)
    svc.spreadsheets().values().clear(spreadsheetId=spreadsheet_id, range=f"'{ABA_BASE}'").execute()
    svc.spreadsheets().values().update(spreadsheetId=spreadsheet_id, range=f"'{ABA_BASE}'!A1", valueInputOption="RAW",
                                       body={"values": [CABECALHO_BASE] + linhas}).execute()
    svc.spreadsheets().batchUpdate(spreadsheetId=spreadsheet_id, body={"requests": [
        {"repeatCell": {"range": {"sheetId": ws.id, "startRowIndex": 1, "startColumnIndex": 0, "endColumnIndex": 1},
                        "cell": {"userEnteredFormat": {"numberFormat": dp.FMT_DATA}},
                        "fields": "userEnteredFormat.numberFormat"}},
        {"updateSheetProperties": {"properties": {"sheetId": ws.id, "hidden": True}, "fields": "hidden"}}]}).execute()


def main():
    import sync
    agora = datetime.datetime.now(sync.BRT)
    conn = sdr.conectar()
    try:
        linhas = base(sdr.envios_frentes(conn))
    finally:
        conn.close()
    sh = sync.get_sheets_client().open_by_key(sync.SPREADSHEET_ID)
    svc = sync.build_sheets_service()
    publicar_base(sh, svc, sync.SPREADSHEET_ID, linhas)
    montar_painel(linhas, agora).publicar(sh, svc, sync.SPREADSHEET_ID, indice=1)
    print(f"Explorar disparos: aba publicada ({len(linhas)} linhas na base).")


if __name__ == "__main__":
    main()
