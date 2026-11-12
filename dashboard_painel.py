"""Painel visual em Google Sheets: cartao nativo, funil, colunas, linha, rosca e tabela.

Uma aba de painel e uma grade de 12 colunas x 100px. Cada `linha` recebe
componentes que dividem a largura por igual. Os numeros que alimentam os
graficos vao para uma aba escondida `dados_<aba>`; o painel so tem os graficos,
as tabelas e um cabecalho de uma linha. Tudo e apagado e redesenhado a cada
rodada (graficos, faixas, formatacao condicional, mesclagens).

Pegadinhas do Sheets cobertas aqui (todas silenciosas):
- serie com texto e numero misturados some do grafico: valores vao como numero;
- grafico sobe e cobre conteudo se faltam linhas abaixo: a aba e redimensionada;
- texto corta sem wrapStrategy: so ha texto curto, e com WRAP quando pode crescer;
- formula em planilha pt-BR usa ';' e '\\' (lista entre chaves).
"""

import math
import unicodedata

LARGURA_COL = 100
N_COLS = 12
COLUNAS_ABA = 26  # a aba mantem A..Z; o painel ocupa so as N_COLS primeiras
ALTURA_LINHA = 21
CONTEUDO = N_COLS * LARGURA_COL
GAP = 20
Y_CONTEUDO = 3 * ALTURA_LINHA + 12

LARANJA = (0.773, 0.353, 0.067)
LARANJA_CLARO = (0.957, 0.722, 0.553)
PESSEGO = (0.992, 0.937, 0.894)
CINZA = (0.42, 0.42, 0.42)
CINZA_TEXTO = (0.30, 0.30, 0.30)
BORDA = (0.86, 0.86, 0.86)
BRANCO = (1, 1, 1)
VERDE = (0.13, 0.55, 0.30)
VERMELHO = (0.80, 0.20, 0.15)
PALETA = [LARANJA, LARANJA_CLARO, CINZA, (0.55, 0.24, 0.05)]
FONTE = "Roboto"

FMT_INT = {"type": "NUMBER", "pattern": "#,##0"}
FMT_PCT = {"type": "PERCENT", "pattern": "0.0%"}
FMT_BRL = {"type": "CURRENCY", "pattern": '"R$" #,##0'}


def cor(rgb):
    return {"rgbColor": {"red": rgb[0], "green": rgb[1], "blue": rgb[2]}}


def num_br(n):
    n = n or 0
    if isinstance(n, float) and not n.is_integer():
        s = f"{n:,.1f}"
    else:
        s = f"{int(round(n)):,}"
    return s.replace(",", "X").replace(".", ",").replace("X", ".")


def pct_br(p):
    v = (p or 0) * 100
    if v == 0 or v >= 10:
        s = f"{v:.0f}"
    elif v >= 1:
        s = f"{v:.1f}"
    else:
        s = f"{v:.2f}"
    return s.replace(".", ",") + "%"


def taxa(parte, todo):
    return parte / todo if todo else 0


def _numero(v):
    if isinstance(v, bool) or v is None or v == "":
        return 0
    if isinstance(v, (int, float)):
        return v
    try:
        return float(str(v).replace(",", "."))
    except ValueError:
        return 0


def slug(texto):
    s = unicodedata.normalize("NFKD", texto).encode("ascii", "ignore").decode().lower()
    return "".join(c if c.isalnum() else "_" for c in s).strip("_")


def _titulo_fmt():
    return {"bold": True, "fontSize": 12, "foregroundColorStyle": cor(CINZA_TEXTO)}


def _spec_base(titulo, subtitulo=None):
    spec = {"title": titulo, "titleTextFormat": _titulo_fmt(), "fontName": FONTE,
            "backgroundColorStyle": cor(BRANCO)}
    if subtitulo:
        spec["subtitle"] = subtitulo
        spec["subtitleTextFormat"] = {"fontSize": 10, "foregroundColorStyle": cor(CINZA)}
    return spec


class _Ctx:
    """O que um componente precisa saber para gerar seus requests."""

    def __init__(self, painel_id, dados_id):
        self.painel_id = painel_id
        self.dados_id = dados_id

    def src(self, r0, r1, c0, c1):
        return {"sourceRange": {"sources": [{"sheetId": self.dados_id, "startRowIndex": r0, "endRowIndex": r1,
                                             "startColumnIndex": c0, "endColumnIndex": c1}]}}

    def grafico(self, pos, spec):
        x, y, w, h = pos
        return {"addChart": {"chart": {"spec": spec, "border": {"colorStyle": cor(BORDA)}, "position": {
            "overlayPosition": {"anchorCell": {"sheetId": self.painel_id, "rowIndex": y // ALTURA_LINHA,
                                               "columnIndex": x // LARGURA_COL},
                                "offsetXPixels": x % LARGURA_COL, "offsetYPixels": y % ALTURA_LINHA,
                                "widthPixels": w, "heightPixels": h}}}}}


class _Componente:
    altura = 300
    em_celulas = False

    def tem_conteudo(self):
        return True

    def bloco(self):
        """Linhas que vao para a aba de dados (lista de listas), ou []."""
        return []

    def formatos_dados(self, r0):
        """[(linha, coluna, numberFormat)] na aba de dados."""
        return []

    def requests(self, ctx, pos, r0):
        return []


class Cartao(_Componente):
    """Numero grande nativo (scorecardChart). `base` opcional = valor de comparacao."""

    altura = 110

    def __init__(self, titulo, valor, formato=FMT_INT, base=None, descricao_base=""):
        self.titulo, self.valor, self.formato = titulo, _numero(valor), formato
        self.base, self.descricao_base = (None if base is None else _numero(base)), descricao_base

    # A base fica 2 colunas ao lado do valor: em celula vizinha o Sheets funde os dois
    # intervalos no keyValueData e o cartao mostra valor + base somados, sem erro.
    COL_BASE = 2

    def bloco(self):
        linha = [self.valor] if self.base is None else [self.valor, "", self.base]
        return [[self.titulo], linha]

    def formatos_dados(self, r0):
        out = [(r0 + 1, 0, self.formato)]
        if self.base is not None:
            out.append((r0 + 1, self.COL_BASE, self.formato))
        return out

    def requests(self, ctx, pos, r0):
        sc = {"keyValueData": ctx.src(r0 + 1, r0 + 2, 0, 1),
              "numberFormatSource": "FROM_DATA",
              "keyValueFormat": {"textFormat": {"fontSize": 30, "bold": True, "fontFamily": FONTE,
                                                "foregroundColorStyle": cor(LARANJA)}}}
        if self.base is not None:
            sc["baselineValueData"] = ctx.src(r0 + 1, r0 + 2, self.COL_BASE, self.COL_BASE + 1)
            sc["baselineValueFormat"] = {
                "comparisonType": "PERCENTAGE_DIFFERENCE", "description": self.descricao_base,
                "positiveColorStyle": cor(VERDE), "negativeColorStyle": cor(VERMELHO),
                "textFormat": {"fontSize": 10, "fontFamily": FONTE}}
        spec = _spec_base(self.titulo)
        spec["scorecardChart"] = sc
        return [ctx.grafico(pos, spec)]


class Funil(_Componente):
    """Barra horizontal em ordem de etapa, rotulo 'valor · % do topo'."""

    altura = 320

    def __init__(self, titulo, etapas, subtitulo="% sobre a primeira etapa", altura=None):
        self.titulo, self.subtitulo = titulo, subtitulo
        self.etapas = [(nome, _numero(v)) for nome, v in etapas]
        if altura:
            self.altura = altura

    def bloco(self):
        topo = self.etapas[0][1] if self.etapas else 0
        linhas = [["Etapa", "Quantidade", "Rotulo"]]
        for i, (nome, v) in enumerate(self.etapas):
            rotulo = num_br(v) if i == 0 else f"{num_br(v)} · {pct_br(taxa(v, topo))}"
            linhas.append([nome, v, rotulo])
        return linhas

    def requests(self, ctx, pos, r0):
        n = len(self.etapas) + 1
        topo = max((v for _, v in self.etapas), default=0) or 1
        spec = _spec_base(self.titulo, self.subtitulo)
        spec["basicChart"] = {
            "chartType": "BAR", "legendPosition": "NO_LEGEND", "headerCount": 1,
            "domains": [{"domain": ctx.src(r0, r0 + n, 0, 1)}],
            "series": [{"series": ctx.src(r0, r0 + n, 1, 2), "targetAxis": "BOTTOM_AXIS",
                        "colorStyle": cor(LARANJA),
                        "dataLabel": {"type": "CUSTOM", "placement": "OUTSIDE_END",
                                      "customLabelData": ctx.src(r0, r0 + n, 2, 3),
                                      "textFormat": {"fontSize": 10, "bold": True, "fontFamily": FONTE,
                                                     "foregroundColorStyle": cor(CINZA_TEXTO)}}}],
            "axis": [{"position": "BOTTOM_AXIS", "title": "",
                      "viewWindowOptions": {"viewWindowMin": 0, "viewWindowMax": topo * 1.3,
                                            "viewWindowMode": "EXPLICIT"}},
                     {"position": "LEFT_AXIS", "title": "",
                      "format": {"fontSize": 11, "foregroundColorStyle": cor(CINZA_TEXTO)}}],
        }
        return [ctx.grafico(pos, spec)]


class Serie(_Componente):
    """Grafico de categoria x N series: COLUMN, BAR, LINE ou AREA.

    `cabecalho` = [nome do eixo, serie1, serie2...]; `linhas` = [[categoria, v1, v2...]]."""

    def __init__(self, titulo, cabecalho, linhas, tipo="COLUMN", empilhado=False, rotulos=True,
                 subtitulo=None, cores=None, altura=300, legenda=None, formato=None):
        self.titulo, self.subtitulo, self.tipo = titulo, subtitulo, tipo
        self.cabecalho = cabecalho
        self.linhas = [[str(l[0])] + [_numero(v) for v in l[1:]] for l in linhas]
        self.empilhado, self.rotulos, self.altura = empilhado, rotulos, altura
        self.cores = cores or PALETA
        self.legenda = legenda
        self.formato = formato

    def bloco(self):
        return [list(self.cabecalho)] + self.linhas if self.linhas else []

    def formatos_dados(self, r0):
        if not self.formato:
            return []
        return [(r0 + 1 + i, c, self.formato) for i in range(len(self.linhas)) for c in range(1, len(self.cabecalho))]

    def requests(self, ctx, pos, r0):
        if not self.linhas:
            return []
        n = len(self.linhas) + 1
        n_series = len(self.cabecalho) - 1
        eixo_valor = "BOTTOM_AXIS" if self.tipo == "BAR" else "LEFT_AXIS"
        series = []
        for i in range(n_series):
            s = {"series": ctx.src(r0, r0 + n, i + 1, i + 2), "targetAxis": eixo_valor,
                 "colorStyle": cor(self.cores[i % len(self.cores)])}
            if self.tipo == "LINE":
                s["lineStyle"] = {"width": 3}
            if self.rotulos and not self.empilhado and n_series == 1 and self.tipo in ("COLUMN", "BAR"):
                s["dataLabel"] = {"type": "DATA", "placement": "OUTSIDE_END",
                                  "textFormat": {"fontSize": 10, "fontFamily": FONTE,
                                                 "foregroundColorStyle": cor(CINZA_TEXTO)}}
            series.append(s)
        legenda = self.legenda or ("NO_LEGEND" if n_series == 1 else "TOP_LEGEND")
        eixos = {"BOTTOM_AXIS": {"position": "BOTTOM_AXIS", "title": ""},
                 "LEFT_AXIS": {"position": "LEFT_AXIS", "title": ""}}
        if any("dataLabel" in s for s in series):
            topo = max((v for l in self.linhas for v in l[1:]), default=0)
            if topo > 0:  # folga para o rotulo da maior barra nao cortar na borda
                eixos[eixo_valor]["viewWindowOptions"] = {"viewWindowMin": 0, "viewWindowMax": topo * 1.2,
                                                          "viewWindowMode": "EXPLICIT"}
        bc = {"chartType": self.tipo, "legendPosition": legenda, "headerCount": 1,
              "domains": [{"domain": ctx.src(r0, r0 + n, 0, 1)}], "series": series,
              "axis": list(eixos.values())}
        if self.empilhado:
            bc["stackedType"] = "STACKED"
            if self.rotulos:
                bc["totalDataLabel"] = {"type": "DATA"}
        spec = _spec_base(self.titulo, self.subtitulo)
        spec["basicChart"] = bc
        return [ctx.grafico(pos, spec)]


class Rosca(_Componente):
    """Pizza com furo; cores vem do tema da planilha (o pieChart nao aceita cor por fatia)."""

    def __init__(self, titulo, fatias, subtitulo=None, altura=300):
        self.titulo, self.subtitulo, self.altura = titulo, subtitulo, altura
        self.fatias = [(str(n), _numero(v)) for n, v in fatias if _numero(v) > 0]

    def tem_conteudo(self):
        return len(self.fatias) >= 2

    def bloco(self):
        return [["Categoria", "Valor"]] + [list(f) for f in self.fatias] if self.fatias else []

    def requests(self, ctx, pos, r0):
        if not self.fatias:
            return []
        n = len(self.fatias) + 1
        spec = _spec_base(self.titulo, self.subtitulo)
        spec["pieChart"] = {"legendPosition": "LABELED_LEGEND", "pieHole": 0.55,
                            "domain": ctx.src(r0, r0 + n, 0, 1), "series": ctx.src(r0, r0 + n, 1, 2)}
        return [ctx.grafico(pos, spec)]


def _cel(valor=None, fmt=None, bold=False, fg=CINZA_TEXTO, bg=None, size=10, alinhar=None, wrap=False):
    # Texto vai sempre como stringValue (nunca avaliado): rotulos vem de GA/HubSpot/bancos externos.
    ued = {}
    if isinstance(valor, bool):
        ued["userEnteredValue"] = {"stringValue": "sim" if valor else "nao"}
    elif isinstance(valor, (int, float)):
        ued["userEnteredValue"] = {"numberValue": valor}
    elif valor is not None:
        ued["userEnteredValue"] = {"stringValue": str(valor)}
    f = {"textFormat": {"bold": bold, "fontSize": size, "fontFamily": FONTE, "foregroundColorStyle": cor(fg)},
         "verticalAlignment": "MIDDLE"}
    if bg:
        f["backgroundColorStyle"] = cor(bg)
    if fmt:
        f["numberFormat"] = fmt
    if alinhar:
        f["horizontalAlignment"] = alinhar
    if wrap:
        f["wrapStrategy"] = "WRAP"
    ued["userEnteredFormat"] = f
    return ued


class Tabela(_Componente):
    """Tabela em celulas: cabecalho laranja, faixas alternadas, borda fina, gradiente opcional."""

    em_celulas = True

    def __init__(self, titulo, cabecalho, linhas, formatos=None, gradiente=()):
        self.titulo, self.cabecalho, self.linhas = titulo, cabecalho, linhas
        self.formatos = formatos or [None] * len(cabecalho)
        self.gradiente = gradiente
        self.altura = (2 + max(len(linhas), 1)) * ALTURA_LINHA

    def _spans(self):
        k = len(self.cabecalho)
        resto = 2 if k <= 6 else 1
        primeiro = N_COLS - resto * (k - 1)
        return [primeiro] + [resto] * (k - 1)

    def requests(self, ctx, pos, r0):
        _, y, _, _ = pos
        lin0 = y // ALTURA_LINHA
        spans = self._spans()
        inicios = [sum(spans[:i]) for i in range(len(spans))]
        pid = ctx.painel_id
        reqs = []

        def merge(r, c0, c1):
            if c1 - c0 > 1:
                reqs.append({"mergeCells": {"range": {"sheetId": pid, "startRowIndex": r, "endRowIndex": r + 1,
                                                      "startColumnIndex": c0, "endColumnIndex": c1},
                                            "mergeType": "MERGE_ALL"}})

        def linha_celulas(valores, **kw):
            cels = [{} for _ in range(N_COLS)]
            for i, v in enumerate(valores):
                fmt = kw.get("fmts", [None] * len(valores))[i]
                alinhar = "LEFT" if self.formatos[i] is None else "RIGHT"
                cels[inicios[i]] = _cel(v, fmt=fmt, bold=kw.get("bold", False), fg=kw.get("fg", CINZA_TEXTO),
                                        bg=kw.get("bg"), alinhar=alinhar)
            return {"values": cels}

        corpo = self.linhas or [["sem dados no periodo"] + [""] * (len(self.cabecalho) - 1)]
        rows = [{"values": [_cel(self.titulo, bold=True, size=12)] + [{}] * (N_COLS - 1)},
                linha_celulas(self.cabecalho, bold=True, fg=BRANCO, bg=LARANJA)]
        rows += [linha_celulas(l, fmts=self.formatos) for l in corpo]
        reqs.append({"updateCells": {"rows": rows, "fields": "userEnteredValue,userEnteredFormat",
                                     "start": {"sheetId": pid, "rowIndex": lin0, "columnIndex": 0}}})
        merge(lin0, 0, N_COLS)
        for r in range(lin0 + 1, lin0 + 2 + len(corpo)):
            for c0, sp in zip(inicios, spans):
                merge(r, c0, c0 + sp)
        faixa = {"sheetId": pid, "startRowIndex": lin0 + 1, "endRowIndex": lin0 + 2 + len(corpo),
                 "startColumnIndex": 0, "endColumnIndex": N_COLS}
        reqs.append({"addBanding": {"bandedRange": {"range": faixa, "rowProperties": {
            "headerColorStyle": cor(LARANJA), "firstBandColorStyle": cor(BRANCO),
            "secondBandColorStyle": cor(PESSEGO)}}}})
        reqs.append({"updateBorders": {"range": faixa,
                                       "top": {"style": "SOLID", "colorStyle": cor(LARANJA)},
                                       "bottom": {"style": "SOLID", "colorStyle": cor(LARANJA)},
                                       "innerHorizontal": {"style": "SOLID", "colorStyle": cor(BORDA)}}})
        if self.linhas:
            for col in self.gradiente:
                valores = {_numero(l[col]) for l in self.linhas}
                if len(valores) < 2:  # coluna constante: o gradiente pintaria tudo da cor maxima
                    continue
                reqs.append({"addConditionalFormatRule": {"index": 0, "rule": {
                    "ranges": [{"sheetId": pid, "startRowIndex": lin0 + 2, "endRowIndex": lin0 + 2 + len(corpo),
                                "startColumnIndex": inicios[col], "endColumnIndex": inicios[col] + spans[col]}],
                    "gradientRule": {"minpoint": {"type": "NUMBER", "value": "0", "colorStyle": cor(BRANCO)},
                                     "maxpoint": {"type": "MAX", "colorStyle": cor(LARANJA_CLARO)}}}}})
        return reqs


ABA_RESUMO = "dados_resumo"


def _linhas_resumo(svc, spreadsheet_id):
    try:
        resp = svc.spreadsheets().values().get(spreadsheetId=spreadsheet_id, range=f"'{ABA_RESUMO}'!A2:C",
                                               valueRenderOption="UNFORMATTED_VALUE").execute()
    except Exception as e:  # aba ainda nao existe na primeira rodada
        print(f"[resumo] sem leitura de {ABA_RESUMO}: {str(e)[:120]}")
        return []
    return [(r + ["", ""])[:3] for r in resp.get("values", []) if r and r[0]]


def ler_resumo(svc, spreadsheet_id):
    """{chave: valor numerico} gravado pelos syncs de cada canal (aba escondida)."""
    return {k: v for k, v, _ in _linhas_resumo(svc, spreadsheet_id)}


def gravar_resumo(sh, svc, spreadsheet_id, valores, agora):
    """Mescla `valores` no resumo (chaves de outros canais ficam como estao)."""
    import gspread
    try:
        ws = sh.worksheet(ABA_RESUMO)
    except gspread.exceptions.WorksheetNotFound:
        ws = sh.add_worksheet(title=ABA_RESUMO, rows=100, cols=3)
    carimbo = agora.strftime("%Y-%m-%d %H:%M")
    linhas = {k: [k, v, quando] for k, v, quando in _linhas_resumo(svc, spreadsheet_id)}
    for k, v in valores.items():
        linhas[k] = [k, _numero(v), carimbo]
    corpo = [["chave", "valor", "atualizado"]] + [linhas[k] for k in sorted(linhas)]
    svc.spreadsheets().values().clear(spreadsheetId=spreadsheet_id, range=f"'{ABA_RESUMO}'").execute()
    svc.spreadsheets().values().update(spreadsheetId=spreadsheet_id, range=f"'{ABA_RESUMO}'!A1",
                                       valueInputOption="RAW", body={"values": corpo}).execute()
    if not ws.isSheetHidden:
        svc.spreadsheets().batchUpdate(spreadsheetId=spreadsheet_id, body={"requests": [{"updateSheetProperties": {
            "properties": {"sheetId": ws.id, "hidden": True}, "fields": "hidden"}}]}).execute()


class Painel:
    def __init__(self, aba, titulo, rodape):
        self.aba, self.titulo, self.rodape = aba, titulo, rodape
        self.linhas = []

    def linha(self, *componentes):
        """Componente None ou sem conteudo (ex.: rosca de uma fatia so) sai da linha; os outros alargam."""
        self.linhas.append([c for c in componentes if c is not None and c.tem_conteudo()])
        return self

    def layout(self):
        """[(componente, (x, y, w, h))] e a altura total em px."""
        y = Y_CONTEUDO
        out = []
        for comps in self.linhas:
            if not comps:
                continue
            if any(c.em_celulas for c in comps):
                y = math.ceil(y / ALTURA_LINHA) * ALTURA_LINHA
            n = len(comps)
            w = (CONTEUDO - GAP * (n - 1)) // n
            h = max(c.altura for c in comps)
            for i, c in enumerate(comps):
                out.append((c, (i * (w + GAP), y, w, h)))
            y += h + GAP
        return out, y

    def blocos(self, itens):
        """Empilha os blocos de dados na aba escondida. Retorna ([(comp, r0)], valores, formatos)."""
        r, inicio, valores, formatos = 0, [], [], []
        for c, _ in itens:
            b = c.bloco()
            inicio.append(r)
            if b:
                valores.extend(b)
                formatos.extend(c.formatos_dados(r))
                valores.append([])
                r += len(b) + 1
        return inicio, valores, formatos

    def montar_requests(self, painel_id, dados_id, n_linhas, n_linhas_dados, existentes, itens, inicio, formatos):
        ctx = _Ctx(painel_id, dados_id)
        reqs = [{"deleteEmbeddedObject": {"objectId": cid}} for cid in existentes["charts"]]
        reqs += [{"deleteBanding": {"bandedRangeId": bid}} for bid in existentes["bandas"]]
        reqs += [{"deleteConditionalFormatRule": {"sheetId": painel_id, "index": 0}}
                 for _ in range(existentes["condicionais"])]
        reqs += [{"unmergeCells": {"range": {"sheetId": painel_id}}},
                 {"updateCells": {"range": {"sheetId": painel_id}, "fields": "userEnteredValue,userEnteredFormat"}},
                 {"updateSheetProperties": {"properties": {"sheetId": painel_id, "gridProperties": {
                     "rowCount": n_linhas, "columnCount": COLUNAS_ABA, "hideGridlines": True,
                     "frozenRowCount": 0}},
                     "fields": "gridProperties(rowCount,columnCount,hideGridlines,frozenRowCount)"}},
                 {"updateDimensionProperties": {"range": {"sheetId": painel_id, "dimension": "COLUMNS",
                                                          "startIndex": 0, "endIndex": N_COLS},
                                                "properties": {"pixelSize": LARGURA_COL}, "fields": "pixelSize"}},
                 {"updateDimensionProperties": {"range": {"sheetId": painel_id, "dimension": "ROWS",
                                                          "startIndex": 0, "endIndex": n_linhas},
                                                "properties": {"pixelSize": ALTURA_LINHA}, "fields": "pixelSize"}},
                 {"updateCells": {"start": {"sheetId": painel_id, "rowIndex": 0, "columnIndex": 0},
                                  "fields": "userEnteredValue,userEnteredFormat", "rows": [
                                      {"values": [_cel(self.titulo, bold=True, fg=BRANCO, bg=LARANJA, size=16)]
                                       + [_cel(bg=LARANJA)] * (N_COLS - 1)},
                                      {"values": [_cel(bg=LARANJA)] * N_COLS},
                                      {"values": [_cel(self.rodape, fg=CINZA, size=9)] + [{}] * (N_COLS - 1)}]}},
                 {"mergeCells": {"range": {"sheetId": painel_id, "startRowIndex": 0, "endRowIndex": 2,
                                           "startColumnIndex": 0, "endColumnIndex": N_COLS}, "mergeType": "MERGE_ALL"}},
                 {"mergeCells": {"range": {"sheetId": painel_id, "startRowIndex": 2, "endRowIndex": 3,
                                           "startColumnIndex": 0, "endColumnIndex": N_COLS}, "mergeType": "MERGE_ALL"}},
                 {"updateSheetProperties": {"properties": {"sheetId": dados_id, "hidden": True,
                                                           "gridProperties": {"rowCount": n_linhas_dados,
                                                                              "columnCount": 8}},
                                            "fields": "hidden,gridProperties(rowCount,columnCount)"}}]
        for r, c, fmt in formatos:
            reqs.append({"repeatCell": {"range": {"sheetId": dados_id, "startRowIndex": r, "endRowIndex": r + 1,
                                                  "startColumnIndex": c, "endColumnIndex": c + 1},
                                        "cell": {"userEnteredFormat": {"numberFormat": fmt}},
                                        "fields": "userEnteredFormat.numberFormat"}})
        for (c, pos), r0 in zip(itens, inicio):
            reqs.extend(c.requests(ctx, pos, r0))
        return reqs

    def publicar(self, sh, svc, spreadsheet_id, indice=None):
        import gspread

        def aba(titulo, **kw):
            try:
                return sh.worksheet(titulo)
            except gspread.exceptions.WorksheetNotFound:
                return sh.add_worksheet(title=titulo, rows=100, cols=N_COLS, **kw)

        ws = aba(self.aba, index=indice) if indice is not None else aba(self.aba)
        dws = aba("dados_" + slug(self.aba))
        itens, altura = self.layout()
        inicio, valores, formatos = self.blocos(itens)
        n_linhas = math.ceil(altura / ALTURA_LINHA) + 4
        n_linhas_dados = max(len(valores) + 5, 50)

        meta = svc.spreadsheets().get(
            spreadsheetId=spreadsheet_id,
            fields="sheets(properties(sheetId),charts(chartId),bandedRanges(bandedRangeId),conditionalFormats)",
        ).execute()
        existentes = {"charts": [], "bandas": [], "condicionais": 0}
        for s in meta.get("sheets", []):
            if s["properties"]["sheetId"] == ws.id:
                existentes = {"charts": [c["chartId"] for c in s.get("charts", [])],
                              "bandas": [b["bandedRangeId"] for b in s.get("bandedRanges", [])],
                              "condicionais": len(s.get("conditionalFormats", []))}

        if dws.row_count < n_linhas_dados:
            dws.resize(rows=n_linhas_dados)
        svc.spreadsheets().values().clear(spreadsheetId=spreadsheet_id, range=f"'{dws.title}'").execute()
        if valores:
            svc.spreadsheets().values().update(
                spreadsheetId=spreadsheet_id, range=f"'{dws.title}'!A1", valueInputOption="RAW",
                body={"values": valores}).execute()
        reqs = self.montar_requests(ws.id, dws.id, n_linhas, n_linhas_dados, existentes, itens, inicio, formatos)
        svc.spreadsheets().batchUpdate(spreadsheetId=spreadsheet_id, body={"requests": reqs}).execute()
        return ws
