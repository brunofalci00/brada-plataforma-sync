"""Conferencia visual sem navegador: exporta cada aba visivel do dashboard em PDF (com graficos)
pela conta de servico. Uso: python ferramentas/exportar_abas.py [aprovai plataforma ...]
Os PDFs vao para a pasta temporaria do sistema (dashboard_abas_pdf)."""
import sys, os, time
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import sync, requests
from google.oauth2.service_account import Credentials
from google.auth.transport.requests import Request

OUT = os.path.join(__import__("tempfile").gettempdir(), "dashboard_abas_pdf")
os.makedirs(OUT, exist_ok=True)
creds = Credentials.from_service_account_file(sync.SHEETS_SA_FILE, scopes=["https://www.googleapis.com/auth/drive.readonly"])
creds.refresh(Request())
ABAS = {"visao_geral": 310463746, "brenda": 1691747693, "plataforma": 943913291, "aprovai": 1842609998,
        "incentivador": 774501113, "comunidade": 1826355523, "primeira_fila": 1376125764}
alvo = sys.argv[1:] or list(ABAS)
for nome in alvo:
    url = (f"https://docs.google.com/spreadsheets/d/{sync.SPREADSHEET_ID}/export?format=pdf&gid={ABAS[nome]}"
           "&portrait=false&fitw=true&size=A3&gridlines=false&printtitle=false&sheetnames=false&pagenum=UNDEFINED"
           "&top_margin=0.2&bottom_margin=0.2&left_margin=0.2&right_margin=0.2")
    for tentativa in range(4):
        r = requests.get(url, headers={"Authorization": f"Bearer {creds.token}"}, timeout=120)
        if r.status_code == 200: break
        time.sleep(8 * (tentativa + 1))
    caminho = os.path.join(OUT, f"{nome}.pdf")
    with open(caminho, "wb") as f: f.write(r.content)
    print(nome, r.status_code, len(r.content), caminho)
    time.sleep(3)
