"""Motor de filtros das abas do dashboard (Sheets pt-BR).

Cada aba com filtro tem:
- uma aba escondida `base_<algo>` com linhas agregadas por dia (coluna A, serial de
  data do Sheets), dimensoes (fonte, canal, origem...) e etapas do funil (numeros);
- uma linha de `dp.Controles` no topo do painel (periodo A, periodo B, dimensoes,
  agrupamento), que as formulas leem;
- componentes "vivos": o valor de cada celula de dados e uma formula SUMIFS sobre a
  base, entao mudar um filtro redesenha os graficos na hora, sem rodar nada.

Os filtros voltam ao padrao (ultimos 30 dias contra os 30 anteriores) a cada rodada e
valem para todo mundo que abre a planilha. Texto vindo de fonte externa so entra em
formula por `literal()` (lista branca de caracteres + aspas escapadas).
"""

import datetime
import re

import dashboard_painel as dp

EPOCA = datetime.date(1899, 12, 30)
TODAS, TODOS = "Todas", "Todos"
DIA, SEMANA = "Dia", "Semana"
MAX_PONTOS = 366
JANELA_PADRAO = 30
SEM_VALOR = "sem fonte"

SEGURO = re.compile(r"[\w .\-/()+]{1,60}")


def serial(dia):
    return (dia - EPOCA).days


def seguro(texto):
    return bool(SEGURO.fullmatch(str(texto)))


def literal(texto):
    """Texto de fora (ex.: fonte do banco) dentro de formula: so caracteres simples, aspas escapadas."""
    if not seguro(texto):
        raise ValueError(f"valor fora do padrao para formula: {texto!r}")
    return '"' + str(texto).replace('"', '""') + '"'


def F(texto):
    return dp.Formula("=" + texto)


def rotulo(valor, topo=None):
    if topo is None:
        return F(f'TEXT({valor};"#,##0")')
    return F(f'TEXT({valor};"#,##0")&" · "&TEXT(IFERROR({valor}/{topo};0);"0.0%")')


# ---------- base escondida ----------

class Base:
    """Aba escondida: coluna A = dia (serial), depois as dimensoes, depois as etapas.

    dimensoes: [(chave, cabecalho, rotulo_de_todos)]; etapas: [(chave, cabecalho)].
    """

    def __init__(self, aba, dimensoes, etapas):
        self.aba, self.dimensoes, self.etapas = aba, dimensoes, etapas
        chaves = [k for k, _, _ in dimensoes] + [k for k, _ in etapas]
        self.letra = {k: dp._letra(1 + i) for i, k in enumerate(chaves)}

    @property
    def cabecalho(self):
        return ["Dia"] + [c for _, c, _ in self.dimensoes] + [c for _, c in self.etapas]

    def coluna(self, chave):
        letra = "A" if chave == "dia" else self.letra[chave]
        return f"{self.aba}!${letra}:${letra}"

    def agregar(self, registros):
        """registros: (dia, {dimensao: valor}, {etapa: numero}). Soma por dia x dimensoes."""
        soma = {}
        for dia, dims, etapas in registros:
            if not dia:
                continue
            chave = (dia,) + tuple(str(dims.get(k) or SEM_VALOR) for k, _, _ in self.dimensoes)
            acc = soma.setdefault(chave, dict.fromkeys((k for k, _ in self.etapas), 0))
            for k, _ in self.etapas:
                acc[k] += int(etapas.get(k, 0))
        return [[serial(c[0])] + list(c[1:]) + [acc[k] for k, _ in self.etapas]
                for c, acc in sorted(soma.items())]

    def opcoes(self, linhas, chave, desde=None):
        """Valores da dimensao por volume da 1a etapa; nome fora do padrao fica de fora (iria para formula).
        `desde` (data): so valores com volume a partir dela (tabela sem linha zerada)."""
        i = 1 + [k for k, _, _ in self.dimensoes].index(chave)
        topo = 1 + len(self.dimensoes)
        corte = serial(desde) if desde else None
        total = {}
        for l in linhas:
            if corte is not None and (l[0] < corte or not l[topo]):
                continue
            total[l[i]] = total.get(l[i], 0) + l[topo]
        fora = sorted(v for v in total if not seguro(v))
        if fora:
            print(f"[AVISO] {self.aba}: {len(fora)} valor(es) de '{chave}' fora do padrao ficaram fora do filtro")
        return sorted((v for v in total if seguro(v)), key=lambda v: (-total[v], v))

    def publicar(self, sh, svc, spreadsheet_id, linhas):
        """Texto vai RAW (nunca vira formula); a coluna A ganha formato de data; aba escondida."""
        import gspread
        try:
            ws = sh.worksheet(self.aba)
        except gspread.exceptions.WorksheetNotFound:
            ws = sh.add_worksheet(title=self.aba, rows=len(linhas) + 10, cols=len(self.cabecalho))
        if ws.row_count < len(linhas) + 10 or ws.col_count < len(self.cabecalho):
            ws.resize(rows=max(ws.row_count, len(linhas) + 10), cols=max(ws.col_count, len(self.cabecalho)))
        svc.spreadsheets().values().clear(spreadsheetId=spreadsheet_id, range=f"'{self.aba}'").execute()
        svc.spreadsheets().values().update(spreadsheetId=spreadsheet_id, range=f"'{self.aba}'!A1",
                                           valueInputOption="RAW",
                                           body={"values": [self.cabecalho] + linhas}).execute()
        svc.spreadsheets().batchUpdate(spreadsheetId=spreadsheet_id, body={"requests": [
            {"repeatCell": {"range": {"sheetId": ws.id, "startRowIndex": 1, "startColumnIndex": 0,
                                      "endColumnIndex": 1},
                            "cell": {"userEnteredFormat": {"numberFormat": dp.FMT_DATA}},
                            "fields": "userEnteredFormat.numberFormat"}},
            {"updateSheetProperties": {"properties": {"sheetId": ws.id, "hidden": True}, "fields": "hidden"}}]}
        ).execute()


# ---------- controles ----------

def controles(aba, hoje, extras=(), agrupar=True):
    """Periodo A (ultimos 30 dias), periodo B (os 30 anteriores), campos extras e agrupamento."""
    a_ate, a_de = hoje, hoje - datetime.timedelta(days=JANELA_PADRAO - 1)
    b_ate, b_de = a_de - datetime.timedelta(days=1), a_de - datetime.timedelta(days=JANELA_PADRAO)
    campos = [dp.Campo("de_A", "Período A: de", serial(a_de), data=True),
              dp.Campo("ate_A", "até", serial(a_ate), data=True),
              dp.Campo("de_B", "Período B: de", serial(b_de), data=True),
              dp.Campo("ate_B", "até", serial(b_ate), data=True)] + list(extras)
    if agrupar:
        campos.append(dp.Campo("agrupar", "Agrupar por", DIA, opcoes=[DIA, SEMANA]))
    return dp.Controles(aba, campos)


def campo_lista(chave, rotulo_campo, opcoes, todos=TODAS, span=1):
    return dp.Campo(chave, rotulo_campo, todos, span=span, opcoes=[todos] + list(opcoes))


# ---------- formulas (pt-BR: ';' entre argumentos) ----------

class Filtros:
    """SUMIFS sobre uma Base lendo as celulas de filtro. Dimensao sem campo nos controles e ignorada."""

    def __init__(self, ctl, base):
        self.ctl, self.base = ctl, base
        self.no_ctl = {c.chave for c in ctl.campos}

    def r(self, chave):
        return self.ctl.ref(chave)

    def periodo(self, p):
        return self.r(f"de_{p}"), self.r(f"ate_{p}")

    def _criterios(self, fixos):
        """fixos[chave]: ausente/None = segue o filtro; texto = valor fixo; False = ignora a dimensao."""
        partes = []
        for chave, _, todos in self.base.dimensoes:
            valor = fixos.get(chave)
            if valor is False:
                continue
            if valor is None:
                if chave not in self.no_ctl:
                    continue
                crit = f'IF({self.r(chave)}="{todos}";"*";{self.r(chave)})'
            else:
                crit = literal(valor)
            partes += [self.base.coluna(chave), crit]
        return partes

    def soma(self, etapa, de, ate, **fixos):
        col_dia = self.base.coluna("dia")
        partes = [self.base.coluna(etapa), col_dia, f'">="&{de}', col_dia, f'"<="&{ate}']
        return "SUMIFS(" + ";".join(partes + self._criterios(fixos)) + ")"

    def soma_p(self, etapa, p="A", **fixos):
        de, ate = self.periodo(p)
        return self.soma(etapa, de, ate, **fixos)

    def taxa(self, etapa, p="A", sobre=None, **fixos):
        base = sobre or self.base.etapas[0][0]
        return f"IFERROR({self.soma_p(etapa, p, **fixos)}/{self.soma_p(base, p, **fixos)};0)"

    def dias(self, p="A"):
        de, ate = self.periodo(p)
        return f"MAX({ate}-{de}+1;1)"

    def sequencia(self):
        """Dias (ou segundas-feiras) do periodo A, ate MAX_PONTOS pontos."""
        de, ate = self.periodo("A")
        if "agrupar" not in self.no_ctl:
            return f"SEQUENCE(MIN({ate}-{de}+1;{MAX_PONTOS});1;{de})"
        seg = f"{de}-WEEKDAY({de};3)"
        dia = f"SEQUENCE(MIN({ate}-{de}+1;{MAX_PONTOS});1;{de})"
        semana = f"SEQUENCE(MIN(INT(({ate}-{seg})/7)+1;{MAX_PONTOS});1;{seg};7)"
        return f'IF({self.r("agrupar")}="{SEMANA}";{semana};{dia})'

    def por_ponto(self, etapa, **fixos):
        """Valor da etapa em cada ponto da sequencia. Dimensao fixada zera a serie se o filtro
        da aba aponta para outro valor (ex.: serie 'WhatsApp' com o filtro em 'E-mail')."""
        de, ate = self.periodo("A")
        fim = f'IF({self.r("agrupar")}="{SEMANA}";d+6;d)' if "agrupar" in self.no_ctl else "d"
        portao = ""
        for chave, _, todos in self.base.dimensoes:
            valor = fixos.get(chave)
            if valor and chave in self.no_ctl:
                ref = self.r(chave)
                portao += f'OR({ref}="{todos}";{ref}={literal(valor)})*'
        soma = self.soma(etapa, f"MAX(d;{de})", f"MIN({fim};{ate})", **fixos)
        return f'IFERROR(MAP({self.sequencia()};LAMBDA(d;{portao}{soma}));"")'


# ---------- componentes vivos ----------

class FunilVivo(dp.Funil):
    """Barra = % do topo (eixo fixo ate 130%, nada corta); rotulo = 'quantidade · %'."""

    def __init__(self, titulo, flt, etapas, subtitulo="% sobre a primeira etapa", altura=None, **fixos):
        super().__init__(titulo, [(n, 1) for n, _ in etapas], subtitulo=subtitulo, altura=altura)
        self.flt, self.etapas_vivas, self.fixos = flt, etapas, fixos

    def bloco(self):
        topo = self.flt.soma_p(self.etapas_vivas[0][1], **self.fixos)
        linhas = [["Etapa", "% do topo", "Rotulo"]]
        for i, (nome, etapa) in enumerate(self.etapas_vivas):
            v = self.flt.soma_p(etapa, **self.fixos)
            pct = F(f"IF({topo}>0;1;0)") if i == 0 else F(f"IFERROR({v}/{topo};0)")
            linhas.append([nome, pct, rotulo(v) if i == 0 else rotulo(v, topo)])
        return linhas

    def formatos_dados(self, r0):
        return [(r0 + 1 + i, 1, dp.FMT_PCT) for i in range(len(self.etapas_vivas))]


class CartaoVivo(dp.Cartao):
    def __init__(self, titulo, formula_a, formula_b, formato=dp.FMT_INT):
        super().__init__(titulo, 0, formato, base=0, descricao_base="vs período B")
        self.fa, self.fb = formula_a, formula_b

    def bloco(self):
        # Base zero vira vazio: o cartao do Sheets mostraria "####%" (divisao por zero).
        return [[self.titulo], [F(self.fa), "", F(f'IF(({self.fb})=0;"";{self.fb})')]]


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
    def __init__(self, titulo, fatias_formula, subtitulo=None, altura=300):
        super().__init__(titulo, [(n, 1) for n, _ in fatias_formula], subtitulo=subtitulo, altura=altura)
        self.fatias_formula = fatias_formula

    def bloco(self):
        return [["Categoria", "Valor"]] + [[n, f] for n, f in self.fatias_formula]


# ---------- blocos padrao de toda aba de produto ----------

def evolucao(titulo, flt, etapa, dimensao, valores, subtitulo=None):
    """Serie do periodo A por dia ou semana, empilhada pelos valores de uma dimensao."""
    linha = [F(flt.sequencia())] + [F(flt.por_ponto(etapa, **{dimensao: v})) for v in valores]
    return SerieViva(titulo, ["Dia"] + list(valores), [linha], n_linhas=MAX_PONTOS,
                     formato_dominio=dp.FMT_DIA, empilhado=True, rotulos=False, altura=320,
                     subtitulo=subtitulo or "Por dia ou semana (filtro 'Agrupar por')")


def comparacao_ab(titulo, flt, etapas):
    """Tabela etapa x (A, % do topo A, B, % do topo B, A sobre B)."""
    topo_a, topo_b = flt.soma_p(etapas[0][1], "A"), flt.soma_p(etapas[0][1], "B")
    linhas = []
    for nome, etapa in etapas:
        a, b = flt.soma_p(etapa, "A"), flt.soma_p(etapa, "B")
        linhas.append([nome, F(a), F(f"IFERROR({a}/{topo_a};0)"), F(b), F(f"IFERROR({b}/{topo_b};0)"),
                       F(f'IFERROR({a}/{b}-1;"")')])
    return dp.Tabela(titulo, ["Etapa", "A", "% do topo A", "B", "% do topo B", "A sobre B"], linhas,
                     formatos=[None, dp.FMT_INT, dp.FMT_PCT, dp.FMT_INT, dp.FMT_PCT, dp.FMT_PCT])


def por_dimensao(titulo, flt, dimensao, valores, colunas, gradiente=(), cabecalho_dim=None):
    """Tabela uma linha por valor da dimensao. colunas: [(cabecalho, 'soma'|'taxa', etapa)]."""
    linhas = []
    for v in valores:
        linha = [v]
        for _, tipo, etapa in colunas:
            f = flt.soma_p(etapa, **{dimensao: v}) if tipo == "soma" else flt.taxa(etapa, **{dimensao: v})
            linha.append(F(f))
        linhas.append(linha)
    formatos = [None] + [dp.FMT_INT if t == "soma" else dp.FMT_PCT for _, t, _ in colunas]
    return dp.Tabela(titulo, [cabecalho_dim or dimensao.capitalize()] + [c for c, _, _ in colunas], linhas,
                     formatos=formatos, gradiente=gradiente)
