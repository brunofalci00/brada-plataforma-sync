"""Resumo executivo v7. Layout e valores juntos; somente A:H e Z1 são gerenciados."""
import datetime

# Bump obrigatorio quando muda a FORMA da aba: `publish` so reaplica layout se o
# Z1 divergir, e sem isso a caixa mesclada do 4o cartao da linha 26 (que saiu)
# ficaria formatada e vazia na planilha.
VERSION = "v7.2-analytics"
ROWS = 145
ORANGE = {"red": .773, "green": .353, "blue": .067}
INK = {"red": .27, "green": .27, "blue": .27}
WHITE = {"red": 1, "green": 1, "blue": 1}
SOFT = {"red": .976, "green": .961, "blue": .949}


def rectangle(sid, r1, r2, c1=0, c2=8):
    return {"sheetId": sid, "startRowIndex": r1 - 1, "endRowIndex": r2,
            "startColumnIndex": c1, "endColumnIndex": c2}


def model(m, now):
    cells, blocks = {}, []

    def text(row, value, kind="note"):
        cells[row, 0] = value
        blocks.append((row, row, 0, 8, kind))

    def cards(row, items):
        for i, (label, value, kind) in enumerate(items):
            cells[row, i * 2] = "—" if value is None or value == "" else value
            cells[row + 2, i * 2] = label
            blocks.extend([(row, row + 1, i * 2, i * 2 + 2, kind),
                           (row + 2, row + 2, i * 2, i * 2 + 2, "label")])

    def table(row, values, col=0):
        for offset, (label, value) in enumerate(values):
            cells[row + offset, col] = label
            cells[row + offset, col + 3] = "—" if value is None or value == "" else value
            blocks.append((row + offset, row + offset, col, col + 3, "table"))

    def metric_cards(row, items):
        cards(row, [(label, m[key], kind) for label, key, kind in items])

    a, f = m["automatize"], m["fontes"]
    text(1, "Plataforma Brada · Aquisição e ativação", "title")
    text(2, (now.replace(tzinfo=None) - datetime.datetime(1899, 12, 30)).total_seconds() / 86400, "timestamp")
    text(4, "Automatize · Resultado na plataforma", "header")
    cards(5, [(label, a[key], "number") for label, key in [
        ("Cadastros de proponentes", "cadastros"), ("Criaram projeto", "criaram"),
        ("Com projeto publicado", "publicaram"), ("Aptos a captar hoje", "aptos")]])
    text(8, "Pessoas distintas. Ativação = Disponível/Em Execução com prazo vigente. Publicação e aptidão são o estado da foto.")
    cards(10, [("Projetos desse grupo", a["projetos"], "number"),
               ("Projetos aptos hoje", a["projetos_aptos"], "number"),
               ("Projetos do maior dono", a["maior_proprietario"], "number"),
               ("Cadastros sem lead vinculado", m["automatize_sem_lead"], "number")])
    text(14, "Automatize · Cobertura dos leads", "header")
    metric_cards(15, [("Leads armazenados", "leads_total", "number"),
                      ("Leads com cadastro vinculado", "leads_cadastrados", "number"),
                      ("Leads com projeto vinculado", "leads_com_projeto", "number")])
    text(18, "Fonte " + ("indisponível" if f["automatize_estado"] == "indisponivel" else "parcial")
         + ". Total real não confirmado; esta base não é denominador de conversão total.", "warning")
    text(19, "Última ingestão de leads: " + (f["automatize_ultima_ingestao"] or "não registrada")
         + ". Vínculos com cadastros recalculados em cada foto.")
    text(21, "Plataforma · Estoque e prazo de captação", "header")
    metric_cards(22, [("Projetos cadastrados", "proj_total", "number"), ("Podem captar hoje", "proj_ativos", "number"),
                      ("Prazo vencido, a reativar", "proj_a_reativar", "number"), ("Em execução", "st_em_execucao", "number")])
    # Nota no meio, e nao no fim: ela explica as duas faixas, e a linha 29 tem de
    # ficar livre para separar a secao do proximo cabecalho, como no resto da aba.
    text(25, "Rascunhos, podem captar hoje, prazo vencido, concluídos e sem prazo definido somam o estoque; "
         "\"Em execução\" é um recorte dentro de quem pode captar. Projeto com prazo vencido não aparece para o "
         "incentivador na plataforma: volta a captar com renovação de prazo, e é a fila da régua de reengajamento.")
    metric_cards(26, [("Rascunhos", "st_rascunho", "number"), ("Concluídos", "st_concluido", "number"),
                      ("Sem prazo definido", "proj_sem_prazo", "number")])
    text(30, "Novos proponentes · Funil de pessoas", "header")
    metric_cards(31, [("Cadastros novos", "funil_cadastraram", "number"), ("Criaram projeto", "funil_com_projeto", "number"),
                      ("Com projeto publicado", "funil_publicaram", "number"), ("Aptos a captar hoje", "funil_aptos", "number")])
    text(34, "Somente proponentes não migrados. Cada pessoa conta uma vez; publicado vencido não é apto hoje.")
    text(36, "Cadastros e uso · Públicos e períodos indicados", "header")
    metric_cards(37, [(m["mes_atual_rotulo"], "novos_mes", "number"), (m["mes_anterior_rotulo"], "novos_mes_ant", "number"),
                      ("Acumulado de novos", "novos_total", "number"), ("Acessaram em 30d (base total)", "ativos_30d", "number")])
    text(40, "Cadastros: novos de todos os perfis, sem migrados. Acessos: toda a base. Mês corrente parcial; anterior completo.")
    channels = [("sem_atribuicao", "Sem origem identificada"), ("automatize", "Automatize"),
                ("leadlovers", "LeadLovers"), ("meta_ads", "Meta Ads"), ("instagram", "Instagram"),
                ("comercial", "Comercial"), ("site", "Site"), ("outro", "Outro")]
    table(42, [(label, m["canais"].get(key, 0)) for key, label in channels])
    text(51, "Séries semanais · Cadastros / acesso em 30d", "header")
    table(52, [("Semana · cadastros novos", "Pessoas")] + m["semanas"])
    table(52, [("Semana · ativos em 30d", "Pessoas")] + m["semanas_ativos"], 4)
    text(61, "Semana começa na segunda. Ativos = última foto da semana; — é foto ausente, não zero.")
    text(63, "Publicação · Qualidade e bloqueios", "header")
    metric_cards(64, [("Disponíveis completos", "st_disponivel_completo", "number"), ("Em atualização", "st_disponivel_selo", "number"),
                      ("Rascunhos a até 3 campos", "rasc_perto", "number")])
    metric_cards(68, [("Sem Diário Oficial", "rasc_sem_diario", "number"), ("Sem descrição", "rasc_sem_descricao", "number"),
                      ("Sem orçamento", "rasc_sem_orcamento", "number")])
    text(72, "Prazo · Vencimentos acumulados de projetos aptos", "header")
    metric_cards(73, [("Até 30 dias", "vence_30d", "number"), ("Até 60 dias", "vence_60d", "number"),
                      ("Até 90 dias", "vence_90d", "number"), ("Até 180 dias", "vence_180d", "number")])
    text(77, "Migração · Retenção do conjunto original", "header")
    metric_cards(78, [("Projetos no baseline", "antiga_baseline", "number"), ("Baseline ainda visível", "baseline_visiveis", "number"),
                      ("Retenção de visibilidade", "retencao_frac", "percent"), ("Migrados que acessaram", "base_logou_frac", "percent")])
    text(81, "Retenção mede projetos do mesmo baseline, não aptidão para captar. Acesso da base migrada mede pessoas.")
    text(83, "Propostas reais · Testes excluídos", "header")
    metric_cards(84, [("Propostas aprovadas", "prop_aprovadas", "number"), ("Valor aprovado", "prop_valor", "money")])
    text(88, "Crescimento · Projetos criados nos últimos 30 dias", "header")
    metric_cards(89, [("Automatize", "orig_automatize", "number"), ("LeadLovers", "orig_leadlovers", "number"),
                      ("Base migrada", "orig_migrado", "number"), ("Sem origem registrada", "orig_sem_atribuicao", "number")])
    metric_cards(93, [("Projetos novos", "orig_projetos_novos", "number"), ("Donos distintos", "orig_donos", "number"),
                      ("Participação do maior dono", "orig_concentracao_frac", "percent"), ("Projetos de quem recebeu e-mail", "orig_de_tocados", "number")])
    text(97, "Comunicação · Processamento e acesso observado", "header")
    metric_cards(98, [("Mensagens na fila", "mail_total", "number"), ("Mensagens nos últimos 7d", "mail_7d", "number"),
                      ("Processadas com sucesso", "mail_entrega_frac", "percent"), ("Acesso observado em até 7d", "mail_voltaram_frac", "percent")])
    text(101, "Sucesso não comprova entrega na caixa de entrada. Acesso usa último login de coortes com 7 dias completos: limite inferior observado, não efeito causal do e-mail.")
    text(103, "Detalhamento no back office: backlogbrada.web.app/m/dashboards/r/funil-plataforma")
    text(106, "Novos proponentes · Frentes para destravar", "header")
    funnel = m["funil"]
    cards(107, [("Pessoas sem projeto", funnel["cadastros"] - funnel["criaram"], "number"),
                ("Com projeto, sem publicação atual", funnel["criaram"] - funnel["publicaram"], "number"),
                ("Publicaram, sem aptidão vigente", funnel["publicaram"] - funnel["aptos"], "number")])
    text(110, "Grupos exclusivos de proponentes novos, fora os aptos. Próximos passos: criar, concluir a publicação ou revisar status/prazo.")
    return cells, blocks


def requests(sid, metrics, now, metadata, apply_layout):
    cells, blocks = model(metrics, now)
    out = []
    if apply_layout:
        sheet = next(s for s in metadata["sheets"] if s["properties"]["sheetId"] == sid)
        out += [{"updateSpreadsheetProperties": {"properties": {"timeZone": "America/Sao_Paulo", "locale": "pt_BR"}, "fields": "timeZone,locale"}},
                {"updateSheetProperties": {"properties": {"sheetId": sid, "gridProperties": {"rowCount": max(ROWS, sheet["properties"]["gridProperties"]["rowCount"]), "hideGridlines": True}}, "fields": "gridProperties.rowCount,gridProperties.hideGridlines"}},
                {"unmergeCells": {"range": rectangle(sid, 1, ROWS)}},
                {"updateDimensionProperties": {"range": {"sheetId": sid, "dimension": "ROWS", "startIndex": 0, "endIndex": ROWS}, "properties": {"hiddenByUser": False, "pixelSize": 25}, "fields": "hiddenByUser,pixelSize"}},
                {"updateDimensionProperties": {"range": {"sheetId": sid, "dimension": "COLUMNS", "startIndex": 0, "endIndex": 8}, "properties": {"pixelSize": 110}, "fields": "pixelSize"}},
                {"repeatCell": {"range": rectangle(sid, 1, ROWS), "cell": {"userEnteredFormat": {"backgroundColor": WHITE, "textFormat": {"fontSize": 11, "foregroundColor": INK}, "wrapStrategy": "WRAP", "verticalAlignment": "MIDDLE", "numberFormat": {"type": "NUMBER", "pattern": "#,##0"}}}, "fields": "userEnteredFormat"}}]
        for index in range(len(sheet.get("conditionalFormats", [])) - 1, -1, -1):
            out.append({"deleteConditionalFormatRule": {"sheetId": sid, "index": index}})
        out.append({"addConditionalFormatRule": {"index": 0, "rule": {
            "ranges": [rectangle(sid, 2, 2)], "booleanRule": {
                "condition": {"type": "CUSTOM_FORMULA", "values": [{"userEnteredValue": "=$A$2<AGORA()-26/24"}]},
                "format": {"backgroundColor": {"red": 1, "green": .8, "blue": .8}, "textFormat": {"bold": True}}}}}})
        for r1, r2, c1, c2, kind in blocks:
            region = rectangle(sid, r1, r2, c1, c2)
            out.append({"mergeCells": {"range": region, "mergeType": "MERGE_ALL"}})
            fmt = {"textFormat": {"fontSize": 11, "foregroundColor": INK}, "wrapStrategy": "WRAP", "verticalAlignment": "MIDDLE"}
            if kind in ("number", "percent", "money"):
                fmt.update(backgroundColor=SOFT, horizontalAlignment="CENTER")
                fmt["textFormat"].update(fontSize=20, bold=True, foregroundColor=ORANGE)
                fmt["numberFormat"] = {"type": "NUMBER", "pattern": {"number": "#,##0", "percent": "0.0%", "money": '"R$ "#,##0.00'}[kind]}
            elif kind == "label":
                fmt.update(backgroundColor=SOFT, horizontalAlignment="CENTER")
            elif kind == "timestamp":
                fmt["numberFormat"] = {"type": "DATE_TIME", "pattern": '"Foto: "dd/mm/yyyy hh:mm" (Brasília) · Acumulado, salvo indicação"'}
            elif kind == "warning":
                fmt.update(backgroundColor={"red": 1, "green": .95, "blue": .87})
                fmt["textFormat"].update(bold=True)
            elif kind in ("header", "title"):
                fmt["textFormat"].update(fontSize=12 if kind == "header" else 16, bold=True, foregroundColor=ORANGE if kind == "header" else WHITE)
                if kind == "title":
                    fmt["backgroundColor"] = ORANGE
            out.append({"repeatCell": {"range": region, "cell": {"userEnteredFormat": fmt}, "fields": "userEnteredFormat"}})
            if kind in ("label", "note", "title", "timestamp", "warning"):
                out.append({"updateDimensionProperties": {"range": {"sheetId": sid, "dimension": "ROWS", "startIndex": r1 - 1, "endIndex": r2}, "properties": {"pixelSize": 44}, "fields": "pixelSize"}})
    rows = []
    for r in range(1, ROWS + 1):
        values = []
        for c in range(8):
            v = cells.get((r, c), "")
            key = "numberValue" if isinstance(v, (int, float)) else "stringValue"
            values.append({"userEnteredValue": {key: v}} if v != "" else {})
        rows.append({"values": values})
    out.append({"updateCells": {"range": rectangle(sid, 1, ROWS), "rows": rows, "fields": "userEnteredValue"}})
    out.append({"updateCells": {"range": rectangle(sid, 1, 1, 25, 26), "rows": [{"values": [{"userEnteredValue": {"stringValue": VERSION}}]}], "fields": "userEnteredValue"}})
    return out


def publish(sh, metrics, now):
    import gspread
    try:
        ws = sh.worksheet("Dashboard")
    except gspread.exceptions.WorksheetNotFound:
        ws = sh.add_worksheet(title="Dashboard", rows=ROWS, cols=26)
    apply_layout = ws.acell("Z1").value != VERSION
    sh.batch_update({"requests": requests(ws.id, metrics, now, sh.fetch_sheet_metadata() if apply_layout else {}, apply_layout)})
    return VERSION
