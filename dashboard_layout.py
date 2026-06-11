"""
Layout e escrita da aba `Dashboard` (consumo humano direto pela Tamyris).

Principios:
- O sync e o dono da aba: escreve VALORES calculados (nunca formulas, exceto
  1 SPARKLINE documentada) via UM values_batch_update com ranges fixos —
  SEM ws.clear(), pra aba nunca aparecer em branco pra quem estiver olhando.
- Formatacao versionada por sentinela: a celula Z1 (fonte branca) guarda a
  LAYOUT_VERSION aplicada. Diferente -> reaplica o batch completo (idempotente:
  unmerge antes de merge, deleta conditional formats e protected ranges antigos
  antes de recriar). Igual -> so valores. Self-healing: apagar Z1 reaplica tudo.
- Staleness visivel: carimbo serial datetime + formatacao condicional
  (carimbo > 24h -> fundo vermelho). Timezone/locale da planilha sao setados
  explicitamente (America/Sao_Paulo + pt_BR) pra NOW() casar com o carimbo BRT.
- Ordem no main: raw_* -> snap -> meta -> Dashboard POR ULTIMO (layout ->
  valores -> sentinela).

v2 (11/06): bloco "MIGRACAO — ANTIGA -> NOVA" (gap 199 ativos na antiga vs 4
migrados visiveis; diagnostico no vault: diagnostico_gap_reativacao_pos_golive_11jun).
"""

import datetime

LAYOUT_VERSION = "v2"
DASH_TITLE = "Dashboard"
SENTINEL_CELL = "Z1"

# Cores Brada
LARANJA = {"red": 0.773, "green": 0.353, "blue": 0.067}      # C55A11
LARANJA_CLARO = {"red": 0.957, "green": 0.898, "blue": 0.851}  # F4E5D9
CINZA = {"red": 0.6, "green": 0.6, "blue": 0.6}               # 999999
CINZA_ESCURO = {"red": 0.27, "green": 0.27, "blue": 0.27}
BRANCO = {"red": 1, "green": 1, "blue": 1}
VERMELHO_CLARO = {"red": 0.957, "green": 0.8, "blue": 0.8}

# Canais canonicos exibidos (sempre todos, com zero) — exclui "migrado"
CANAIS_EXIBIDOS = [
    ("organico", "Orgânico"),
    ("leadlovers", "LeadLovers"),
    ("automatize", "Automatize (IA)"),
    ("meta_ads", "Meta Ads"),
    ("outro", "Outro"),
]

EPOCH_SHEETS = datetime.datetime(1899, 12, 30)


def datetime_to_serial(dt_naive):
    """datetime naive (ja em BRT) -> numero de serie do Sheets."""
    return (dt_naive - EPOCH_SHEETS).total_seconds() / 86400.0


# ---------------------------------------------------------------
# Helpers de range
# ---------------------------------------------------------------

def _col(letter):
    return ord(letter.upper()) - ord("A")


def grid(sheet_id, a1):
    """'A5:B6' (1-based) -> GridRange. Tambem aceita celula unica 'D2'."""
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


# Mapa v2 (linhas): 1 titulo · 2 carimbo · 4-13 PROJETOS+expiracao ·
# 15-19 MIGRACAO · 21-24 PROPOSTAS · 26-39 CADASTROS · 41-45 FUNIL · 47 rodape
MERGES = [
    "A1:H1", "A2:C2", "D2:F2",
    "A4:H4",
    "A5:B6", "C5:D6", "E5:F6", "G5:H6",
    "A7:B7", "C7:D7", "E7:F7", "G7:H7",
    "A8:H8",
    "A10:H10",
    "A11:B12", "C11:D12", "E11:F12", "G11:H12",
    "A13:B13", "C13:D13", "E13:F13",
    "A15:H15",
    "A16:B17", "C16:D17", "E16:F17", "G16:H17",
    "A18:B18", "C18:D18", "E18:F18", "G18:H18",
    "A19:H19",
    "A21:H21",
    "A22:B23", "C22:E23", "F22:H23",
    "A24:B24", "C24:E24",
    "A26:H26",
    "A27:B28", "C27:D28", "E27:F28", "G27:H28",
    "A29:B29", "C29:D29", "E29:F29", "G29:H29",
    "A30:H30",
    "A31:C31", "D31:H31",
    "F32:H39",
    "A41:H41",
    "A42:B43", "C42:D43", "E42:F43", "G42:H43",
    "A44:B44", "C44:D44", "E44:F44",
    "A45:H45",
    "A47:H47",
]

SCORECARD_RANGES = ["A5:H6", "A11:F12", "A16:H17", "A22:E23", "A27:H28", "A42:F43"]
LABEL_RANGES = ["A7:H7", "A13:F13", "A18:H18", "A24:E24", "A29:H29", "A44:F44"]
HEADER_RANGES = ["A4:H4", "A15:H15", "A21:H21", "A26:H26", "A41:H41"]
NOTE_RANGES = ["A8:H8", "G11:H12", "A19:H19", "F22:H23", "A30:H30",
               "G42:H43", "A45:H45", "A47:H47"]
PERCENT_RANGES = ["E16:F17", "G16:H17", "E27:F28"]


def _fmt(range_, cell_format, fields):
    return {"repeatCell": {"range": range_, "cell": {"userEnteredFormat": cell_format},
                           "fields": "userEnteredFormat(" + fields + ")"}}


def layout_requests(sheet_id, meta):
    """Batch completo de formatacao (idempotente). `meta` = fetch_sheet_metadata
    pra achar conditional formats / protected ranges antigos e abas a ocultar."""
    req = []

    # Propriedades da planilha: timezone/locale (carimbo e NOW() em BRT)
    req.append({"updateSpreadsheetProperties": {
        "properties": {"timeZone": "America/Sao_Paulo", "locale": "pt_BR"},
        "fields": "timeZone,locale"}})

    # Aba Dashboard: primeira, cor laranja, sem gridlines
    req.append({"updateSheetProperties": {
        "properties": {"sheetId": sheet_id, "index": 0,
                       "tabColor": LARANJA,
                       "gridProperties": {"hideGridlines": True}},
        "fields": "index,tabColor,gridProperties.hideGridlines"}})

    # Oculta todas as outras abas (raw_*, snap, meta). Editores conseguem
    # re-exibir; viewer nao. UX, nao seguranca (raw so tem hash).
    for s in meta.get("sheets", []):
        sid = s["properties"]["sheetId"]
        if sid != sheet_id and not s["properties"].get("hidden"):
            req.append({"updateSheetProperties": {
                "properties": {"sheetId": sid, "hidden": True},
                "fields": "hidden"}})

    # Limpa conditional formats antigos da aba (evita acumulo no reapply)
    n_cf = 0
    for s in meta.get("sheets", []):
        if s["properties"]["sheetId"] == sheet_id:
            n_cf = len(s.get("conditionalFormats", []))
    for i in range(n_cf - 1, -1, -1):
        req.append({"deleteConditionalFormatRule": {"sheetId": sheet_id, "index": i}})

    # Limpa protected ranges antigos da aba
    for s in meta.get("sheets", []):
        if s["properties"]["sheetId"] == sheet_id:
            for pr in s.get("protectedRanges", []):
                req.append({"deleteProtectedRange": {"protectedRangeId": pr["protectedRangeId"]}})

    # Larguras das colunas A-H + altura do titulo
    req.append({"updateDimensionProperties": {
        "range": {"sheetId": sheet_id, "dimension": "COLUMNS",
                  "startIndex": 0, "endIndex": 8},
        "properties": {"pixelSize": 108}, "fields": "pixelSize"}})
    req.append({"updateDimensionProperties": {
        "range": {"sheetId": sheet_id, "dimension": "ROWS",
                  "startIndex": 0, "endIndex": 1},
        "properties": {"pixelSize": 36}, "fields": "pixelSize"}})

    # Merges: desfaz tudo na regiao e refaz (idempotente)
    req.append({"unmergeCells": {"range": grid(sheet_id, "A1:H50")}})
    for m in MERGES:
        req.append({"mergeCells": {"range": grid(sheet_id, m), "mergeType": "MERGE_ALL"}})

    # Reset visual da regiao + fonte base
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
    # Linha do carimbo
    req.append(_fmt(grid(sheet_id, "A2:C2"),
                    {"textFormat": {"fontSize": 9, "foregroundColor": CINZA}},
                    "textFormat"))
    req.append(_fmt(grid(sheet_id, "D2:F2"),
                    {"textFormat": {"fontSize": 9, "bold": True, "foregroundColor": CINZA_ESCURO},
                     "numberFormat": {"type": "DATE_TIME", "pattern": "dd/mm/yyyy hh:mm"}},
                    "textFormat,numberFormat"))

    # Headers de secao
    for h in HEADER_RANGES:
        req.append(_fmt(grid(sheet_id, h),
                        {"backgroundColor": LARANJA_CLARO,
                         "textFormat": {"bold": True, "fontSize": 11, "foregroundColor": CINZA_ESCURO}},
                        "backgroundColor,textFormat"))
    # Subheaders
    for h in ["A10:H10", "A31:C31", "D31:H31"]:
        req.append(_fmt(grid(sheet_id, h),
                        {"textFormat": {"bold": True, "fontSize": 10, "foregroundColor": CINZA_ESCURO}},
                        "textFormat"))

    # Scorecards (numeros grandes laranja, centralizados)
    for sc in SCORECARD_RANGES:
        req.append(_fmt(grid(sheet_id, sc),
                        {"textFormat": {"bold": True, "fontSize": 22, "foregroundColor": LARANJA},
                         "horizontalAlignment": "CENTER",
                         "numberFormat": {"type": "NUMBER", "pattern": "#,##0"}},
                        "textFormat,horizontalAlignment,numberFormat"))
    # Excecoes de cor/formato
    req.append(_fmt(grid(sheet_id, "G5:H6"),  # Rascunhos em cinza escuro
                    {"textFormat": {"bold": True, "fontSize": 22, "foregroundColor": CINZA_ESCURO}},
                    "textFormat"))
    req.append(_fmt(grid(sheet_id, "E11:F12"),  # Sem data em cinza (gap escancarado)
                    {"textFormat": {"bold": True, "fontSize": 22, "foregroundColor": CINZA}},
                    "textFormat"))
    req.append(_fmt(grid(sheet_id, "C22:E23"),  # Valor aprovado em R$
                    {"numberFormat": {"type": "NUMBER", "pattern": "\"R$ \"#,##0"}},
                    "numberFormat"))
    for pr in PERCENT_RANGES:  # retencao, base logou, ativacao
        req.append(_fmt(grid(sheet_id, pr),
                        {"numberFormat": {"type": "NUMBER", "pattern": "0.0%"}},
                        "numberFormat"))

    # Labels dos scorecards
    for lb in LABEL_RANGES:
        req.append(_fmt(grid(sheet_id, lb),
                        {"textFormat": {"fontSize": 9, "foregroundColor": CINZA},
                         "horizontalAlignment": "CENTER"},
                        "textFormat,horizontalAlignment"))

    # Notas/caveats
    for nt in NOTE_RANGES:
        req.append(_fmt(grid(sheet_id, nt),
                        {"textFormat": {"fontSize": 8, "italic": True, "foregroundColor": CINZA},
                         "wrapStrategy": "WRAP"},
                        "textFormat,wrapStrategy"))

    # Tabelas (canal e semanas)
    req.append(_fmt(grid(sheet_id, "A32:B37"),
                    {"textFormat": {"fontSize": 9}}, "textFormat"))
    req.append(_fmt(grid(sheet_id, "D32:E39"),
                    {"textFormat": {"fontSize": 9}}, "textFormat"))
    req.append(_fmt(grid(sheet_id, "A37:B37"),  # linha Total em bold
                    {"textFormat": {"fontSize": 9, "bold": True}}, "textFormat"))

    # Sentinela Z1 invisivel (fonte branca)
    req.append(_fmt(grid(sheet_id, "Z1"),
                    {"textFormat": {"foregroundColor": BRANCO, "fontSize": 6}},
                    "textFormat"))

    # Staleness: carimbo com mais de 24h -> fundo vermelho
    req.append({"addConditionalFormatRule": {"index": 0, "rule": {
        "ranges": [grid(sheet_id, "D2:F2")],
        "booleanRule": {
            "condition": {"type": "CUSTOM_FORMULA",
                          "values": [{"userEnteredValue": "=$D$2<NOW()-1"}]},
            "format": {"backgroundColor": VERMELHO_CLARO,
                       "textFormat": {"bold": True}}}}}})

    # Protecao branda contra edicao acidental (warning only)
    req.append({"addProtectedRange": {"protectedRange": {
        "range": grid(sheet_id, "A1:H50"),
        "description": "Aba gerada pelo brada-plataforma-sync — editar via pipeline",
        "warningOnly": True}}})

    return req


# ---------------------------------------------------------------
# Valores
# ---------------------------------------------------------------

def value_data(m, now_brt_naive):
    """Lista de {range, values} pro values_batch_update (RAW). Inclui os textos
    estaticos (idempotente e self-healing)."""
    pct_sem_data = f"{m['exp_sem_data']}/{m['proj_total']}"
    d = [
        ("A1", [["Plataforma Brada — Visão Geral"]]),
        ("A2", [["Última atualização:"]]),
        ("D2", [[datetime_to_serial(now_brt_naive)]]),
        ("A4", [["PROJETOS"]]),
        ("A5", [[m["proj_ativos"]]]), ("C5", [[m["st_disponivel"]]]),
        ("E5", [[m["st_em_execucao"]]]), ("G5", [[m["st_rascunho"]]]),
        ("A7", [["Ativos (régua proposta*)"]]), ("C7", [["Disponíveis"]]),
        ("E7", [["Em Execução"]]), ("G7", [["Rascunhos"]]),
        ("A8", [[f"*Régua proposta, a validar com a gerência: status Disponível ou Em Execução E captação não expirada. Total: {m['proj_total']} projetos ({m['st_concluido']} concluído(s))."]]),
        ("A10", [["Expiração da captação (CAC)"]]),
        ("A11", [[m["exp_vigente"]]]), ("C11", [[m["exp_expirado"]]]), ("E11", [[m["exp_sem_data"]]]),
        ("G11", [[f"{pct_sem_data} projetos sem data de expiração — gap de dado; pauta de governança com Thiago/Tamyris."]]),
        ("A13", [["Vigentes"]]), ("C13", [["Expirados"]]), ("E13", [["Sem data"]]),
        ("A15", [["MIGRAÇÃO — PLATAFORMA ANTIGA → NOVA"]]),
        ("A16", [[m["antiga_baseline"]]]), ("C16", [[m["mig_visiveis"]]]),
        ("E16", [[m["retencao_frac"]]]), ("G16", [[m["base_logou_frac"]]]),
        ("A18", [["Ativos na antiga (corte 08/06)"]]), ("C18", [["Migrados visíveis na nova"]]),
        ("E18", [["Retenção de projetos"]]), ("G18", [["Base migrada que logou"]]),
        ("A19", [[f"Retenção = projetos ({m['mig_visiveis']}/{m['antiga_baseline']}); reativação = pessoas ({m['base_logou']}/{m['base_total']} logaram). {m['st_rascunho']} projetos migrados estão em Rascunho — invisíveis no matchmaking até completarem o cadastro. Diagnóstico e opções de destravamento em pauta com a gerência (11/06)."]]),
        ("A21", [["PROPOSTAS APROVADAS"]]),
        ("A22", [[m["prop_aprovadas"]]]), ("C22", [[m["prop_valor"]]]),
        ("F22", [["Inclui editais de exemplo da migração — os números podem zerar quando forem excluídos."]]),
        ("A24", [["Aprovadas"]]), ("C24", [["Valor total aprovado"]]),
        ("A26", [[f"CADASTROS NA PLATAFORMA (exclui {m['n_migrados']} migrados)"]]),
        ("A27", [[m["novos_mes"]]]), ("C27", [[m["novos_mes_ant"]]]),
        ("E27", [[m["ativacao_frac"]]]), ("G27", [[m["ativos_30d"]]]),
        ("A29", [["Novos no mês (parcial)"]]), ("C29", [["Novos no mês anterior"]]),
        ("E29", [["Ativação (% já logou)*"]]), ("G29", [["Usuários ativos 30d"]]),
        ("A30", [["*Proxy via Firebase Auth até o campo lastLogin existir na plataforma (cadastro faz auto-login: tende a 100%)."]]),
        ("A31", [["Por canal de origem (acumulado)"]]),
        ("D31", [["Novos cadastros por semana (últimas 8)"]]),
        ("A32", [[label, m["canais"].get(key, 0)] for key, label in CANAIS_EXIBIDOS]
                + [["Total", sum(m["canais"].values())]]),
        ("D32", [[lbl, n] for lbl, n in m["semanas"]]),
        ("A41", [["FUNIL AUTOMATIZE — PÚBLICOS COM DEAL (incentivador e elaboração/prestação)"]]),
        ("A42", [["—"]]), ("C42", [["—"]]), ("E42", [["—"]]),
        ("G42", [["Integração HubSpot entra na próxima sprint."]]),
        ("A44", [["Leads trabalhados (deals)"]]), ("C44", [["Viraram cadastro"]]),
        ("E44", [["Criaram projeto"]]),
        ("A45", [[f"Cadastros atribuídos à IA via UTM (camada 1): {m['utm_automatize']} — o público com projeto já aprovado não passa pelo HubSpot; a atribuição dele é exclusivamente via UTM no link enviado pela IA."]]),
        ("A47", [["Gerado automaticamente pelo pipeline brada-plataforma-sync — não editar manualmente. Dúvidas: Bruno."]]),
    ]
    return [{"range": f"{DASH_TITLE}!{rng}", "values": vals} for rng, vals in d]


# ---------------------------------------------------------------
# Orquestracao
# ---------------------------------------------------------------

def ensure_dashboard(sh, metrics, now_brt_naive):
    """Garante aba, layout (se versao mudou) e valores. Retorna resumo str."""
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
        # So no BUMP de versao: limpa valores orfaos do layout anterior
        # (linhas que o layout novo nao sobrescreve). Runs diarios seguem
        # sem clear — a aba nunca fica em branco no dia a dia.
        ws.batch_clear(["A1:H50"])
        applied = True

    # Valores: um unico batch RAW (sem clear nos runs diarios)
    sh.values_batch_update({
        "valueInputOption": "RAW",
        "data": value_data(metrics, now_brt_naive),
    })
    # Unica formula da aba (documentada): sparkline da serie semanal,
    # referenciando celulas da PROPRIA aba escritas pelo sync acima.
    sh.values_batch_update({
        "valueInputOption": "USER_ENTERED",
        "data": [{"range": f"{DASH_TITLE}!F32", "values": [["=SPARKLINE(E32:E39)"]]}],
    })
    if applied:
        # Sentinela por ultimo: se o run morrer antes, o proximo reaplica tudo
        sh.values_batch_update({
            "valueInputOption": "RAW",
            "data": [{"range": f"{DASH_TITLE}!{SENTINEL_CELL}", "values": [[LAYOUT_VERSION]]}],
        })
    return ("layout aplicado (" + LAYOUT_VERSION + ")" if applied
            else "layout ja aplicado (" + LAYOUT_VERSION + ")")
