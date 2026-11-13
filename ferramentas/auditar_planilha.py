"""Auditoria so leitura da planilha do dashboard: abas, graficos, erros de formula e possivel PII.
Uso: python ferramentas/auditar_planilha.py  (ID de negocio do HubSpot tem 11 digitos e da falso
positivo de telefone em raw_hubspot_incentivador)."""
import sys, os, re
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import sync

svc = sync.build_sheets_service()
sid = sync.SPREADSHEET_ID
meta = svc.spreadsheets().get(spreadsheetId=sid, fields="sheets(properties(title,hidden,index,gridProperties),charts(chartId,spec(title)))").execute()
ERROS = re.compile(r"^#(REF!|ERROR!|N/A|DIV/0!|VALUE!|NAME\?|NUM!|NULL!)")
EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+")
CPF = re.compile(r"\b\d{3}\.\d{3}\.\d{3}-\d{2}\b")
FONE = re.compile(r"\(?\b\d{2}\)?\s?9\d{4}-?\d{4}\b")
titulos = []
for sh in meta["sheets"]:
    p = sh["properties"]; g = p["gridProperties"]
    charts = sh.get("charts", [])
    sem_titulo = sum(1 for c in charts if not c.get("spec", {}).get("title"))
    print(f"{p['index']:>2} {'[oculta]' if p.get('hidden') else '        '} {p['title']:<32} {g.get('rowCount')}x{g.get('columnCount')} graficos={len(charts)} sem_titulo={sem_titulo}")
    titulos.append(p["title"])

rngs = [f"'{t}'" for t in titulos]
res = svc.spreadsheets().values().batchGet(spreadsheetId=sid, ranges=rngs, valueRenderOption="FORMATTED_VALUE").execute()
print("\n== Erros e PII por aba")
total = 0
for vr in res["valueRanges"]:
    aba = vr["range"].split("!")[0].strip("'")
    erros, pii = [], []
    for i, linha in enumerate(vr.get("values", []), 1):
        for j, v in enumerate(linha):
            s = str(v)
            if ERROS.match(s): erros.append((i, j + 1, s))
            if EMAIL.search(s) or CPF.search(s) or FONE.search(s): pii.append((i, j + 1))
    total += len(erros)
    if erros or pii:
        print(f"  {aba}: erros={len(erros)} {erros[:6]} | possivel_pii={len(pii)} {pii[:4]}")
print(f"total de erros: {total}")
