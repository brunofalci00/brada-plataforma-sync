"""
Layout e escrita da aba `Dashboard` (consumo humano direto pela Tamyris).

Principios:
- O sync e o dono da aba: escreve VALORES calculados (nunca formulas, exceto
  1 SPARKLINE documentada) via UM values_batch_update com ranges fixos —
  SEM ws.clear() nos runs diarios (a aba nunca aparece em branco). O clear
  acontece SO no bump de LAYOUT_VERSION (limpa valores orfaos do layout velho).
- Formatacao versionada por sentinela: a celula Z1 (fonte branca) guarda a
  LAYOUT_VERSION aplicada. Diferente -> reaplica o batch completo (idempotente).
  Self-healing: apagar Z1 reaplica tudo.
- Staleness visivel: carimbo serial + formatacao condicional (>24h -> vermelho).
  Timezone/locale setados (America/Sao_Paulo + pt_BR).

v3 (11/06, pedido do Bruno): visual limpo — zero notas explicativas na aba
(definicoes e caveats vivem no dicionario de KPIs do vault), cards com fundo
suave, headers com borda laranja, bloco FUNIL removido ate ter dados (S3),
card "Ativacao" (proxy ~100% por auto-login) trocado por "Total de novos".
"""

import datetime

LAYOUT_VERSION = "v3"
DASH_TITLE = "Dashboard"
SENTINEL_CELL = "Z1"

# Cores Brada
LARANJA = {"red": 0.773, "green": 0.353, "blue": 0.067}        # C55A11
CARD_BG = {"red": 0.976, "green": 0.961, "blue": 0.949}        # F9F5F2 (suave)
CINZA = {"red": 0.6, "green": 0.6, "blue": 0.6}                 # 999999
CINZA_ESCURO = {"red": 0.27, "green": 0.27, "blue": 0.27}
BRANCO = {"red": 1, "green": 1, "blue": 1}
VERMELHO_CLARO = {"red": 0.957, "green": 0.8, "blue": 0.8}

CANAIS_EXIBIDOS = [
    ("organico", "Orgânico"),
    ("leadlovers", "LeadLovers"),
    ("automatize", "Automatize (IA)"),
    ("meta_ads", "Meta Ads"),
    ("outro", "Outro"),
]

EPOCH_SHEETS = datetime.datetime(1899, 12, 30)


def datetime_to_serial(dt_naive):
    return (dt_naive - EPOCH_SHEETS).total_seconds() / 86400.0


def _col(letter):
    return ord(letter.upper()) - ord("A")


def grid(sheet_id, a1):
    if ":" in a1:
        start, end = a1.split(":")
    else:
        start = end = a1
    def parse(ref):
        col = "".join(c for c in ref if c.isalpha())
        row = int("".join(c for c in ref if c.isdigit()))
        return row - 1, _col(col)
    r1, c1 = parse(start)
    r2, c2 = parse(end)
    return {"sheetId": sheet_id, "startRowIndex": r1, "endRowIndex": r2 + 1,
            "startColumnIndex": c1, "endColumnIndex": c2 + 1}


# Mapa v3: 1 titulo · 2 carimbo · 4-10 PROJETOS · 12-15 MIGRACAO ·
# 17-20 PROPOSTAS · 22-35 CADASTROS (cards + tabelas)
MERGES = [
    "A1:H1", "A2:C2", "D2:F2",
    "A4:H4",
    "A5:B6", "C5:D6", "E5:F6", "G5:H6",
    "A7:B7", "C7:D7", "E7:F7", "G7:H7",
    "A8:B9", "C8:D9", "E8:F9",
    "A10:B10", "C10:D10", "E10:F10",
    "A12:H12",
    "A13:B14", "C13:D14", "E13:F14", "G13:H14",
    "A15:B15", "C15:D15", "E15:F15", "G15:H15",
    "A17:H17",
    "A18:B19", "C18:E19",
    "A20:B20", "C20:E20",
    "A22:H22",
    "A23:B24", "C23:D24", "E23:F24", "G23:H24",
    "A25:B25", "C25:D25", "E25:F25", "G25:H25",
    "A27:C27", "D27:H27",
    "F28:H35",
]

# Faixas de "card" (numero + label compartilham o fundo suave)
CARD_BANDS = ["A5:H7", "A8:F10", "A13:H15", "A18:E20", "A23:H25"]
NUMBER_RANGES = ["A5:H6", "A8:F9", "A13:H14", "A18:E19", "A23:H24"]
LABEL_RANGES = ["A7:H7", "A10:F10", "A15:H15", "A20:E20", "A25:H25"]
HEADER_RANGES = ["A4:H4", "A12:H12", "A17:H17", "A22:H22"]
PERCENT_RANGES = ["E13:F14", "G13:H14"]


def _fmt(range_, cell_format, fields):
    return {"repeatCell": {"range": range_, "cell": {"userEnteredFormat": cell_format},
                           "fields": "userEnteredFormat(" + fields + ")"}}


def layout_requests(sheet_id, meta):
    req = []

    req.append({"updateSpreadsheetProperties": {
        "properties": {"timeZone": "America/Sao_Paulo", "locale": "pt_BR"},
        "fields": "timeZone,locale"}})

    req.append({"updateSheetProperties": {
        "properties": {"sheetId": sheet_id, "index": 0,
                       "tabColor": LARANJA,
                       "gridProperties": {"hideGridlines": True}},
        "fields": "index,tabColor,gridProperties.hideGridlines"}})

    # Oculta as outras abas (UX, nao seguranca — raw so tem hash)
    for s in meta.get("sheets", []):
        sid = s["properties"]["sheetId"]
        if sid != sheet_id and not s["properties"].get("hidden"):
            req.append({"updateSheetProperties": {
                "properties": {"sheetId": sid, "hidden": True},
                "fields": "hidden"}})

    # Limpa conditional formats e protected ranges antigos (anti-acumulo)
    n_cf = 0
    for s in meta.get("sheets", []):
        if s["properties"]["sheetId"] == sheet_id:
            n_cf = len(s.get("conditionalFormats", []))
            for pr in s.get("protectedRanges", []):
                req.append({"deleteProtectedRange": {"protectedRangeId": pr["protectedRangeId"]}})
    for i in range(n_cf - 1, -1, -1):
        req.append({"deleteConditionalFormatRule": {"sheetId": sheet_id, "index": i}})

    # Dimensoes
    req.append({"updateDimensionProperties": {
        "range": {"sheetId": sheet_id, "dimension": "COLUMNS",
                  "startIndex": 0, "endIndex": 8},
        "properties": {"pixelSize": 110}, "fields": "pixelSize"}})
    req.append({"updateDimensionProperties": {
        "range": {"sheetId": sheet_id, "dimension": "ROWS",
                  "startIndex": 0, "endIndex": 1},
        "properties": {"pixelSize": 42}, "fields": "pixelSize"}})

    # Merges (idempotente)
    req.append({"unmergeCells": {"range": grid(sheet_id, "A1:H50")}})
    for m in MERGES:
        req.append({"mergeCells": {"range": grid(sheet_id, m), "mergeType": "MERGE_ALL"}})

    # Reset visual
    req.append(_fmt(grid(sheet_id, "A1:H50"),
                    {"backgroundColor": BRANCO,
                     "textFormat": {"fontSize": 10, "foregroundColor": CINZA_ESCURO},
                     "verticalAlignment": "MIDDLE"},
                    "backgroundColor,textFormat,verticalAlignment"))

    # Titulo
    req.append(_fmt(grid(sheet_id, "A1:H1"),
                    {"backgroundColor": LARANJA,
                     "textFormat": {"bold": True, "fontSize": 14, "foregroundColor": BRANCO},
                     "horizontalAlignment": "LEFT"},
                    "backgroundColor,textFormat,horizontalAlignment"))
    # Carimbo
    req.append(_fmt(grid(sheet_id, "A2:C2"),
                    {"textFormat": {"fontSize": 8, "foregroundColor": CINZA}},
                    "textFormat"))
    req.append(_fmt(grid(sheet_id, "D2:F2"),
                    {"textFormat": {"fontSize": 8, "bold": True, "foregroundColor": CINZA},
                     "numberFormat": {"type": "DATE_TIME", "pattern": "dd/mm/yyyy hh:mm"}},
                    "textFormat,numberFormat"))

    # Headers de secao: texto laranja bold, borda inferior laranja
    for h in HEADER_RANGES:
        req.append(_fmt(grid(sheet_id, h),
                        {"textFormat": {"bold": True, "fontSize": 11, "foregroundColor": LARANJA}},
                        "textFormat"))
        req.append({"updateBorders": {
            "range": grid(sheet_id, h),
            "bottom": {"style": "SOLID_MEDIUM", "color": LARANJA}}})

    # Faixas de card (fundo suave cobrindo numero + label)
    for band in CARD_BANDS:
        req.append(_fmt(grid(sheet_id, band),
                        {"backgroundColor": CARD_BG}, "backgroundColor"))

    # Numeros
    for nr in NUMBER_RANGES:
        req.append(_fmt(grid(sheet_id, nr),
                        {"textFormat": {"bold": True, "fontSize": 20, "foregroundColor": LARANJA},
                         "horizontalAlignment": "CENTER",
                         "numberFormat": {"type": "NUMBER", "pattern": "#,##0"}},
                        "textFormat,horizontalAlignment,numberFormat"))
    # Excecoes
    req.append(_fmt(grid(sheet_id, "G5:H6"),   # Rascunhos
                    {"textFormat": {"bold": True, "fontSize": 20, "foregroundColor": CINZA_ESCURO}},
                    "textFormat"))
    req.append(_fmt(grid(sheet_id, "E8:F9"),   # Sem data (gap escancarado)
                    {"textFormat": {"bold": True, "fontSize": 20, "foregroundColor": CINZA}},
                    "textFormat"))
    req.append(_fmt(grid(sheet_id, "C18:E19"),  # Valor aprovado
                    {"numberFormat": {"type": "NUMBER", "pattern": "\"R$ \"#,##0"}},
                    "numberFormat"))
    for pr in PERCENT_RANGES:
        req.append(_fmt(grid(sheet_id, pr),
                        {"numberFormat": {"type": "NUMBER", "pattern": "0.0%"}},
                        "numberFormat"))

    # Labels
    for lb in LABEL_RANGES:
        req.append(_fmt(grid(sheet_id, lb),
                        {"textFormat": {"fontSize": 9, "foregroundColor": CINZA},
                         "horizontalAlignment": "CENTER"},
                        "textFormat,horizontalAlignment"))

    # Subheaders das tabelas
    for h in ["A27:C27", "D27:H27"]:
        req.append(_fmt(grid(sheet_id, h),
                        {"textFormat": {"bold": True, "fontSize": 10, "foregroundColor": CINZA_ESCURO}},
                        "textFormat"))
    # Tabelas
    req.append(_fmt(grid(sheet_id, "A28:B33"),
                    {"textFormat": {"fontSize": 9}}, "textFormat"))
    req.append(_fmt(grid(sheet_id, "D28:E35"),
                    {"textFormat": {"fontSize": 9}}, "textFormat"))
    req.append(_fmt(grid(sheet_id, "A33:B33"),  # Total bold
                    {"textFormat": {"fontSize": 9, "bold": True}}, "textFormat"))

    # Sentinela invisivel
    req.append(_fmt(grid(sheet_id, "Z1"),
                    {"textFormat": {"foregroundColor": BRANCO, "fontSize": 6}},
                    "textFormat"))

    # Staleness: carimbo >24h -> vermelho
    req.append({"addConditionalFormatRule": {"index": 0, "rule": {
        "ranges": [grid(sheet_id, "D2:F2")],
        "booleanRule": {
            "condition": {"type": "CUSTOM_FORMULA",
                          "values": [{"userEnteredValue": "=$D$2<NOW()-1"}]},
            "format": {"backgroundColor": VERMELHO_CLARO,
                       "textFormat": {"bold": True}}}}}})

    # Protecao branda
    req.append({"addProtectedRange": {"protectedRange": {
        "range": grid(sheet_id, "A1:H50"),
        "description": "Aba gerada pelo brada-plataforma-sync — editar via pipeline",
        "warningOnly": True}}})

    return req


# ---------------------------------------------------------------
# Valores (sem notas explicativas — definicoes vivem no dicionario do vault)
# ---------------------------------------------------------------

def value_data(m, now_brt_naive):
    d = [
        ("A1", [["Plataforma Brada — Visão Geral"]]),
        ("A2", [["Atualizado em"]]),
        ("D2", [[datetime_to_serial(now_brt_naive)]]),
        ("A4", [["PROJETOS"]]),
        ("A5", [[m["proj_ativos"]]]), ("C5", [[m["st_disponivel"]]]),
        ("E5", [[m["st_em_execucao"]]]), ("G5", [[m["st_rascunho"]]]),
        ("A7", [["Ativos"]]), ("C7", [["Disponíveis"]]),
        ("E7", [["Em Execução"]]), ("G7", [["Rascunhos"]]),
        ("A8", [[m["exp_vigente"]]]), ("C8", [[m["exp_expirado"]]]), ("E8", [[m["exp_sem_data"]]]),
        ("A10", [["CAC vigente"]]), ("C10", [["CAC expirado"]]), ("E10", [["CAC sem data"]]),
        ("A12", [["MIGRAÇÃO — ANTIGA → NOVA"]]),
        ("A13", [[m["antiga_baseline"]]]), ("C13", [[m["mig_visiveis"]]]),
        ("E13", [[m["retencao_frac"]]]), ("G13", [[m["base_logou_frac"]]]),
        ("A15", [["Ativos na antiga"]]), ("C15", [["Migrados ativos na nova"]]),
        ("E15", [["Retenção"]]), ("G15", [["Migrados que logaram"]]),
        ("A17", [["PROPOSTAS APROVADAS"]]),
        ("A18", [[m["prop_aprovadas"]]]), ("C18", [[m["prop_valor"]]]),
        ("A20", [["Aprovadas"]]), ("C20", [["Valor aprovado"]]),
        ("A22", [["CADASTROS NOVOS"]]),
        ("A23", [[m["novos_mes"]]]), ("C23", [[m["novos_mes_ant"]]]),
        ("E23", [[m["novos_total"]]]), ("G23", [[m["ativos_30d"]]]),
        ("A25", [["No mês"]]), ("C25", [["Mês anterior"]]),
        ("E25", [["Total de novos"]]), ("G25", [["Usuários ativos 30d"]]),
        ("A27", [["Por canal de origem"]]),
        ("D27", [["Novos por semana (últimas 8)"]]),
        ("A28", [[label, m["canais"].get(key, 0)] for key, label in CANAIS_EXIBIDOS]
                + [["Total", sum(m["canais"].values())]]),
        ("D28", [[lbl, n] for lbl, n in m["semanas"]]),
    ]
    return [{"range": f"{DASH_TITLE}!{rng}", "values": vals} for rng, vals in d]


# ---------------------------------------------------------------
# Orquestracao
# ---------------------------------------------------------------

def ensure_dashboard(sh, metrics, now_brt_naive):
    import gspread

    try:
        ws = sh.worksheet(DASH_TITLE)
        created = False
    except gspread.exceptions.WorksheetNotFound:
        ws = sh.add_worksheet(title=DASH_TITLE, rows=60, cols=26)
        created = True

    sentinel = ws.acell(SENTINEL_CELL).value
    applied = False
    if created or sentinel != LAYOUT_VERSION:
        meta = sh.fetch_sheet_metadata()
        sh.batch_update({"requests": layout_requests(ws.id, meta)})
        # So no BUMP de versao: limpa valores orfaos do layout anterior.
        # Runs diarios seguem sem clear (aba nunca fica em branco).
        ws.batch_clear(["A1:H50"])
        applied = True

    sh.values_batch_update({
        "valueInputOption": "RAW",
        "data": value_data(metrics, now_brt_naive),
    })
    # Unica formula da aba (documentada): sparkline da serie semanal
    sh.values_batch_update({
        "valueInputOption": "USER_ENTERED",
        "data": [{"range": f"{DASH_TITLE}!F28", "values": [["=SPARKLINE(E28:E35)"]]}],
    })
    if applied:
        sh.values_batch_update({
            "valueInputOption": "RAW",
            "data": [{"range": f"{DASH_TITLE}!{SENTINEL_CELL}", "values": [[LAYOUT_VERSION]]}],
        })
    return ("layout aplicado (" + LAYOUT_VERSION + ")" if applied
            else "layout ja aplicado (" + LAYOUT_VERSION + ")")
