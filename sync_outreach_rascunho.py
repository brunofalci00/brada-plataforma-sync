"""
Mutirao de reativacao: projetos em RASCUNHO -> planilha operacional do time.

Lista, POR PESSOA (dono), os leads com projeto incompleto na plataforma nova,
com contato real + link wa.me, deixa o time marcar o andamento do contato e
DETECTA no sync se o lead finalizou (saiu do rascunho). Planilha DEDICADA
(OUTREACH_SPREADSHEET_ID), separada do dashboard PII-safe.

PII (repo PUBLICO = logs publicos no GitHub Actions):
- A planilha de saida CONTEM PII (nome/telefone/e-mail) de proposito: e
  privada, compartilhada so com o time + a service account.
- O STDOUT/LOG deste script NUNCA imprime PII. Run normal e dry-run imprimem
  so contadores. A amostra de contatos reais (--sample N) e LOCAL-ONLY e
  imprime um aviso; NUNCA usar --sample no CI.

Merge race-safe (feedback do Sprint D financeiro):
- Linhas estaveis por owner_hash (col A). O sync escreve SO as colunas AUTO
  (A:M) e DESFECHO (T:W); NUNCA toca o bloco MANUAL (N:S) de linhas que ja
  existem -> zero corrida com o time editando. Pessoas novas sao anexadas no
  fim (com defaults manuais escritos uma unica vez).
- Retencao de finalizadores: quem ja apareceu e depois saiu do rascunho NAO e
  removido; vira Finalizou?=Sim com Detectado em carimbado.

Regra de rascunho portada de brada-plataforma-v3/src/pages/ong/Projects.tsx
(isProjectComplete, 11 campos). Helpers de auth/parse reusados de sync.py.
"""

import argparse
import datetime
import os
import re
import sys
from collections import defaultdict

import sync  # reusa init_firestore, get_sheets_client, load_collection, to_date, parse_budget_brl, hash_id, BRT

OUTREACH_SPREADSHEET_ID = os.environ.get("OUTREACH_SPREADSHEET_ID", "")
TAB = "mutirao_rascunho"
TAB_RESUMO = "Resumo"


def ohash(uid):
    """owner_hash com prefixo 'o': hex de 12 chars pode sair so com digitos
    (~0,5%) e o Sheets coage pra numero com USER_ENTERED, quebrando o round-trip
    da chave de merge. O prefixo garante texto sempre."""
    return "o" + sync.hash_id(uid)


# Contas internas/teste que NUNCA entram na lista (e-mail lowercased).
# Excluidas da contagem, da lista e removidas se ja estiverem na planilha.
EXCLUDE_EMAILS = {"brunofalci2000@gmail.com"}

# ===================================================
# REGRA DE RASCUNHO (porta exata do isProjectComplete do front)
# Ordem: os 3 campos quase-universais primeiro (descricao/orcamento/diario),
# pra coluna "Falta" virar copy direta pro WhatsApp.
# ===================================================
FIELD_LABELS = [
    ("description", "descrição"),
    ("budget", "orçamento"),
    ("__diario__", "diário oficial"),
    ("title", "título"),
    ("startDate", "data de início"),
    ("cacExpirationDate", "vencimento CAC"),
    ("location", "localização"),
    ("category", "categoria"),
    ("__ods__", "ODS"),
    ("targetAudience", "público-alvo"),
    ("fundingSource", "lei de incentivo"),
]
TOTAL_FIELDS = len(FIELD_LABELS)  # 11

HEADER = [
    "owner_hash",            # A  (oculto)
    "Proponente",            # B
    "WhatsApp",              # C
    "Abrir WhatsApp",        # D
    "E-mail",                # E
    "Nº projetos rascunho",  # F
    "Tem vigente?",          # G
    "CAC",                   # H
    "Falta",                 # I
    "Detalhe por projeto",   # J
    "Migrado?",              # K
    "Última edição",         # L
    "Prioridade",            # M
    # --- MANUAL (time edita; sync preserva) ---
    "Responsável",           # N
    "Data 1º contato",       # O
    "Canal",                 # P
    "Status do contato",     # Q
    "Follow-ups",            # R
    "Observações",           # S
    # --- DESFECHO (sync carimba) ---
    "Rascunhos restantes",   # T
    "Finalizou?",            # U
    "Detectado em",          # V
    "Dias até finalizar",    # W
]
COL = {h: i for i, h in enumerate(HEADER)}
N_AUTO = COL["Prioridade"] + 1            # A:M  -> 13
MANUAL_START = COL["Responsável"]         # N (idx 13)
MANUAL_END = COL["Observações"]           # S (idx 18)
DESF_START = COL["Rascunhos restantes"]   # T (idx 19)
DESF_END = COL["Dias até finalizar"]      # W (idx 22)
STATUS_DEFAULT = "Não contatado"
CANAL_OPCOES = ["WhatsApp", "E-mail", "Ligação"]
STATUS_OPCOES = ["Não contatado", "Contatado", "Respondeu", "Sem resposta", "Opt-out"]


def _col_a1(idx0):
    """0-based col index -> letra A1 (suficiente p/ ate Z)."""
    return chr(ord("A") + idx0)


# ===================================================
# DERIVACAO
# ===================================================

def field_present(d, key):
    if key == "__ods__":
        ods = d.get("ods")
        return bool(ods) and (len(ods) > 0 if isinstance(ods, list) else True)
    if key == "__diario__":
        return bool(d.get("diarioOficialUrl") or d.get("existingDiarioOficialUrl"))
    return bool(d.get(key))


def missing_labels(d):
    return [label for key, label in FIELD_LABELS if not field_present(d, key)]


def is_rascunho(d):
    return len(missing_labels(d)) > 0


def completude(d):
    return TOTAL_FIELDS - len(missing_labels(d))


def cac_situacao(d, today_str):
    ds = sync.to_date(d.get("cacExpirationDate"))
    if not ds:
        return ("sem", "")
    return ("vigente" if ds >= today_str else "expirado", ds)


def _br(ds):
    """'AAAA-MM-DD' -> 'DD/MM' (vazio se nao tiver)."""
    if not ds or len(ds) < 10:
        return ds or ""
    return f"{ds[8:10]}/{ds[5:7]}"


def _parse_date_any(s):
    """Le data ISO ('AAAA-MM-DD') ou BR ('DD/MM/AAAA'), tolerante ao que o
    Sheets devolve no round-trip. -> datetime.date ou None."""
    s = (s or "").strip()
    if not s:
        return None
    try:
        return datetime.date.fromisoformat(s[:10])
    except ValueError:
        pass
    m = re.match(r"^(\d{2})/(\d{2})/(\d{4})$", s)
    if m:
        try:
            return datetime.date(int(m.group(3)), int(m.group(2)), int(m.group(1)))
        except ValueError:
            return None
    return None


def wa_link(phone):
    """(url, valido). 10/11 digitos -> +55; 12-13 ja com 55 -> direto."""
    digits = re.sub(r"\D", "", str(phone or ""))
    if len(digits) in (10, 11):
        return "https://wa.me/55" + digits, True
    if len(digits) in (12, 13) and digits.startswith("55"):
        return "https://wa.me/" + digits, True
    return "", False


def _dedup_keep_order(labels):
    seen, out = set(), []
    for x in labels:
        if x not in seen:
            seen.add(x)
            out.append(x)
    return out


def build_owner_auto(uid, user, projs, today_str):
    """projs = [(pid, dict)] em rascunho desse dono. Retorna dict de AUTO."""
    name = (user or {}).get("name") or ""
    phone = (user or {}).get("phone") or ""
    email = (user or {}).get("email") or ""
    url, valido = wa_link(phone)

    cacs = [cac_situacao(p, today_str) for _, p in projs]
    vigentes = [ds for sit, ds in cacs if sit == "vigente"]
    expirados = [ds for sit, ds in cacs if sit == "expirado"]
    tem_vigente = bool(vigentes)
    if vigentes:
        cac_disp = f"Vigente até {_br(min(vigentes))}"
    elif expirados:
        cac_disp = f"Todos expirados (últ. {_br(max(expirados))})"
    else:
        cac_disp = "Sem data"

    # Falta = uniao dedup dos campos faltando (ordem FIELD_LABELS)
    falta_all = []
    for _, p in projs:
        falta_all += missing_labels(p)
    falta = ", ".join(_dedup_keep_order(falta_all))

    # Detalhe so quando ha >1 projeto (pro caso 1-projeto, "Falta" basta)
    if len(projs) > 1:
        partes = []
        for _, p in projs:
            t = (p.get("title") or "(sem título)").strip()
            partes.append(f"{t}: falta {', '.join(missing_labels(p)) or '—'}")
        detalhe = " | ".join(partes)
    else:
        detalhe = ""

    # Migrado = nivel PESSOA (veio da base antiga), nao do projeto: um usuario
    # migrado pode criar projeto novo na plataforma (engajou). O grao da aba e
    # a pessoa, entao a coluna reflete o usuario.
    migrado = "sim" if (user or {}).get("legacyId") else "nao"
    edicoes = [sync.to_date(p.get("updatedAt")) for _, p in projs]
    ultima = max([e for e in edicoes if e], default="")

    comp_best = max(completude(p) for _, p in projs)
    base = 2 if tem_vigente else (1 if (vigentes or expirados) else 0)
    prioridade = base * 100 + comp_best

    return {
        "owner_hash": ohash(uid),
        "Proponente": name,
        "WhatsApp": phone,
        "Abrir WhatsApp": url if valido else "validar nº / usar e-mail",
        "E-mail": email,
        "Nº projetos rascunho": len(projs),
        "Tem vigente?": "sim" if tem_vigente else "nao",
        "CAC": cac_disp,
        "Falta": falta,
        "Detalhe por projeto": detalhe,
        "Migrado?": migrado,
        "Última edição": ultima,
        "Prioridade": prioridade,
        "_tel_valido": valido,
    }


def build_finisher_auto(owner_hash, uid, user, existing_row):
    """Dono que saiu do rascunho: contato fresco se o user existe, senao o
    ultimo AUTO conhecido da planilha."""
    if user:
        phone = user.get("phone") or ""
        url, valido = wa_link(phone)
        return {
            "owner_hash": owner_hash,
            "Proponente": user.get("name") or "",
            "WhatsApp": phone,
            "Abrir WhatsApp": url if valido else "validar nº / usar e-mail",
            "E-mail": user.get("email") or "",
            "Nº projetos rascunho": 0,
            "Tem vigente?": "—",
            "CAC": "—",
            "Falta": "(finalizou)",
            "Detalhe por projeto": "",
            "Migrado?": "sim" if user.get("legacyId") else "nao",
            "Última edição": "",
            "Prioridade": 0,
            "_tel_valido": valido,
        }
    # fallback: reaproveita AUTO da planilha
    d = {h: (existing_row[COL[h]] if COL[h] < len(existing_row) else "") for h in HEADER[:N_AUTO]}
    d["Nº projetos rascunho"] = 0
    d["Falta"] = "(finalizou)"
    d["Prioridade"] = 0
    d["_tel_valido"] = bool(d.get("Abrir WhatsApp", "").startswith("http"))
    return d


# ===================================================
# MERGE / ESCRITA
# ===================================================

def _auto_list(auto):
    return [auto.get(h, "") for h in HEADER[:N_AUTO]]


def load_existing(ws):
    """Retorna (ordem_keys, by_key) preservando a ordem das linhas atuais."""
    vals = ws.get_all_values()
    if not vals or vals[0][:1] != ["owner_hash"]:
        return [], {}
    ordem, by_key = [], {}
    for row in vals[1:]:
        if not row or not row[0]:
            continue
        ordem.append(row[0])
        by_key[row[0]] = row + [""] * (len(HEADER) - len(row))
    return ordem, by_key


def merge_rows(groups, users_by_id, today_str, ordem_exist, by_key, excluded_hashes=frozenset()):
    """Produz a estrutura final preservando linhas existentes e anexando novas.
    Chaves em excluded_hashes sao REMOVIDAS (nao retidas como finalizador).
    Retorna (final_keys, auto_by_key, desf_by_key, status_by_key, novos_keys)."""
    hash_to_uid = {ohash(uid): uid for uid in users_by_id}
    groups_by_hash = {ohash(uid): (uid, projs) for uid, projs in groups.items()}

    auto_by_key, desf_by_key, status_by_key = {}, {}, {}

    def desfecho(key, restantes):
        prev = by_key.get(key)
        prev_det = prev[COL["Detectado em"]] if prev else ""
        prev_contato = prev[COL["Data 1º contato"]] if prev else ""
        detectado = prev_det
        if restantes == 0 and not detectado:
            detectado = today_str
        dias = ""
        d0, d1 = _parse_date_any(prev_contato), _parse_date_any(detectado)
        if d0 and d1:
            dias = (d1 - d0).days
        return [restantes, "Sim" if restantes == 0 else "Não", detectado, dias]

    def status_of(key):
        prev = by_key.get(key)
        s = prev[COL["Status do contato"]].strip() if prev else ""
        return s or STATUS_DEFAULT

    # 1) linhas existentes (mantem ordem/posicao)
    for key in ordem_exist:
        if key in excluded_hashes:
            continue  # conta interna/teste: remove da lista
        if key in groups_by_hash:
            uid, projs = groups_by_hash[key]
            auto = build_owner_auto(uid, users_by_id.get(uid), projs, today_str)
            auto_by_key[key] = auto
            desf_by_key[key] = desfecho(key, len(projs))
        else:  # finalizador retido
            uid = hash_to_uid.get(key)
            auto = build_finisher_auto(key, uid, users_by_id.get(uid), by_key.get(key, []))
            auto_by_key[key] = auto
            desf_by_key[key] = desfecho(key, 0)
        status_by_key[key] = status_of(key)

    # 2) pessoas novas (ainda em rascunho, nao vistas) -> anexa ordenado por prioridade
    novos = []
    for uid, projs in groups.items():
        key = ohash(uid)
        if key in by_key:
            continue
        auto = build_owner_auto(uid, users_by_id.get(uid), projs, today_str)
        auto_by_key[key] = auto
        desf_by_key[key] = desfecho(key, len(projs))
        status_by_key[key] = STATUS_DEFAULT
        novos.append((key, auto["Prioridade"]))
    novos.sort(key=lambda x: -x[1])
    novos_keys = [k for k, _ in novos]

    final_keys = list(ordem_exist) + novos_keys
    return final_keys, auto_by_key, desf_by_key, status_by_key, novos_keys


def write_merge(ws, final_keys, auto_by_key, desf_by_key, by_key, prev_n=0):
    """Reescreve a tabela inteira keyed por owner_hash (padrao Sprint D): o
    bloco MANUAL e LIDO da planilha e regravado na linha do MESMO owner_hash,
    entao append/remocao/reorder nunca desalinha o manual com o dono. Linhas
    novas recebem defaults. Idempotente."""
    n = len(final_keys)
    last = n + 1  # linha 1 = header
    ws.update(values=[HEADER], range_name="A1", value_input_option="USER_ENTERED")
    if n == 0:
        if prev_n > 0:
            ws.batch_clear([f"A2:W{prev_n + 1}"])
        return

    rows = []
    for k in final_keys:
        auto = _auto_list(auto_by_key[k])                       # A:M
        prev = by_key.get(k)
        if prev is not None:
            manual = [prev[i] if i < len(prev) else "" for i in range(MANUAL_START, MANUAL_END + 1)]
        else:
            manual = ["", "", "", STATUS_DEFAULT, "", ""]       # N:S defaults p/ linha nova
        rows.append(auto + manual + desf_by_key[k])             # + T:W
    ws.update(values=rows, range_name=f"A2:W{last}", value_input_option="USER_ENTERED")

    # Linhas que sumiram (ex.: exclusao de conta interna): limpa o rabo
    if prev_n > n:
        ws.batch_clear([f"A{last + 1}:W{prev_n + 1}"])


def write_resumo(sh, status_by_key, desf_by_key, final_keys):
    import gspread
    total = len(final_keys)
    finalizados = sum(1 for k in final_keys if desf_by_key[k][1] == "Sim")
    cont_status = [status_by_key[k] for k in final_keys]
    a_contatar = sum(1 for s in cont_status if s in ("", STATUS_DEFAULT))
    contatados = sum(1 for s in cont_status if s in ("Contatado", "Respondeu", "Sem resposta"))
    responderam = sum(1 for s in cont_status if s == "Respondeu")
    optouts = sum(1 for s in cont_status if s == "Opt-out")
    pct = f"{(finalizados / total * 100):.1f}%" if total else "0%"
    linhas = [
        ["Métrica", "Valor"],
        ["Pessoas na lista", total],
        ["A contatar", a_contatar],
        ["Contatados", contatados],
        ["Responderam", responderam],
        ["Finalizados (saíram do rascunho)", finalizados],
        ["% conversão", pct],
        ["Opt-outs", optouts],
    ]
    try:
        ws = sh.worksheet(TAB_RESUMO)
    except gspread.exceptions.WorksheetNotFound:
        ws = sh.add_worksheet(title=TAB_RESUMO, rows=20, cols=2)
    ws.clear()
    ws.update(values=linhas, range_name="A1", value_input_option="USER_ENTERED")


def ensure_formatting(sh, ws):
    """First-run: freeze header, oculta owner_hash, header bold, dropdowns
    (Canal/Status) e formatacao condicional. Idempotente o suficiente p/ rodar
    so na criacao (conditional rules nao sao re-adicionadas em runs seguintes)."""
    sid = ws.id
    reqs = [
        {"updateSheetProperties": {
            "properties": {"sheetId": sid, "gridProperties": {"frozenRowCount": 1}},
            "fields": "gridProperties.frozenRowCount"}},
        {"updateDimensionProperties": {
            "range": {"sheetId": sid, "dimension": "COLUMNS", "startIndex": 0, "endIndex": 1},
            "properties": {"hiddenByUser": True}, "fields": "hiddenByUser"}},
        {"repeatCell": {
            "range": {"sheetId": sid, "startRowIndex": 0, "endRowIndex": 1},
            "cell": {"userEnteredFormat": {"textFormat": {"bold": True},
                                            "backgroundColor": {"red": 0.95, "green": 0.6, "blue": 0.1}}},
            "fields": "userEnteredFormat(textFormat,backgroundColor)"}},
    ]
    # dropdowns
    def dv(col_idx, opcoes):
        return {"setDataValidation": {
            "range": {"sheetId": sid, "startRowIndex": 1,
                      "startColumnIndex": col_idx, "endColumnIndex": col_idx + 1},
            "rule": {"condition": {"type": "ONE_OF_LIST",
                                   "values": [{"userEnteredValue": o} for o in opcoes]},
                     "showCustomUi": True, "strict": False}}}
    reqs.append(dv(COL["Canal"], CANAL_OPCOES))
    reqs.append(dv(COL["Status do contato"], STATUS_OPCOES))

    # conditional: Finalizou?=Sim -> verde
    def cond(col_idx, value, color):
        return {"addConditionalFormatRule": {"index": 0, "rule": {
            "ranges": [{"sheetId": sid, "startRowIndex": 1,
                        "startColumnIndex": col_idx, "endColumnIndex": col_idx + 1}],
            "booleanRule": {"condition": {"type": "TEXT_EQ",
                                          "values": [{"userEnteredValue": value}]},
                            "format": {"backgroundColor": color}}}}}
    reqs.append(cond(COL["Finalizou?"], "Sim", {"red": 0.72, "green": 0.88, "blue": 0.72}))
    reqs.append(cond(COL["Tem vigente?"], "sim", {"red": 0.85, "green": 0.94, "blue": 0.83}))
    reqs.append(cond(COL["Status do contato"], "Opt-out", {"red": 0.96, "green": 0.78, "blue": 0.78}))
    sh.batch_update({"requests": reqs})


# ===================================================
# MAIN
# ===================================================

def main():
    ap = argparse.ArgumentParser(description="Mutirao rascunho -> planilha do time")
    ap.add_argument("--dry-run", action="store_true", help="Le Firestore e imprime contadores, sem escrever")
    ap.add_argument("--sample", type=int, default=0,
                    help="LOCAL-ONLY: imprime N contatos REAIS (PII) p/ sign-off. NUNCA usar no CI.")
    args = ap.parse_args()

    today_str = datetime.datetime.now(sync.BRT).strftime("%Y-%m-%d")
    print("=== Mutirao rascunho (Firestore -> planilha do time) ===")
    db = sync.init_firestore()
    users_by_id = {uid: d for uid, d in sync.load_collection(db, "users")}
    projects_raw = sync.load_collection(db, "projects")
    print(f"firestore: users={len(users_by_id)} projects={len(projects_raw)}")

    groups = defaultdict(list)
    sem_owner = 0
    excluidos = 0
    for pid, p in projects_raw:
        if not is_rascunho(p):
            continue
        oid = p.get("ownerId") or ""
        if not oid:
            sem_owner += 1
            continue
        email = ((users_by_id.get(oid) or {}).get("email") or "").strip().lower()
        if email in EXCLUDE_EMAILS:
            excluidos += 1
            continue
        groups[oid].append((pid, p))

    n_proj_rasc = sum(len(v) for v in groups.values())
    donos = len(groups)
    vigentes_donos = sum(1 for projs in groups.values()
                         if any(cac_situacao(p, today_str)[0] == "vigente" for _, p in projs))
    tel_validos = sum(1 for uid, projs in groups.items()
                      if wa_link((users_by_id.get(uid) or {}).get("phone"))[1])
    # frequencia de campos faltando (sobre projetos em rascunho)
    freq = defaultdict(int)
    for projs in groups.values():
        for _, p in projs:
            for lbl in missing_labels(p):
                freq[lbl] += 1
    freq_top = sorted(freq.items(), key=lambda x: -x[1])

    print(f"rascunho: {n_proj_rasc} projetos | {donos} pessoas | "
          f"{vigentes_donos} com vigente | tel_valido={tel_validos} | "
          f"sem_owner={sem_owner} | excluidos={excluidos}")
    print("campos faltando (freq):", {k: v for k, v in freq_top})

    if args.sample:
        print(f"\n[AMOSTRA LOCAL — CONTEM PII — {args.sample} contatos] "
              f"(NUNCA rodar --sample no CI/repo publico)")
        shown = 0
        for uid, projs in sorted(groups.items(),
                                 key=lambda kv: -build_owner_auto(kv[0], users_by_id.get(kv[0]), kv[1], today_str)["Prioridade"]):
            a = build_owner_auto(uid, users_by_id.get(uid), projs, today_str)
            print(f"  - {a['Proponente']} | {a['WhatsApp']} | {a['Abrir WhatsApp']} | "
                  f"{a['E-mail']} | nº={a['Nº projetos rascunho']} | {a['Tem vigente?']} | "
                  f"{a['CAC']} | falta: {a['Falta']}")
            shown += 1
            if shown >= args.sample:
                break

    if args.dry_run:
        print("\nmutirao_rascunho: DRY-RUN (nada escrito)")
        return

    if not OUTREACH_SPREADSHEET_ID:
        raise SystemExit("OUTREACH_SPREADSHEET_ID ausente (env ou ~/.brada-secrets/plataforma-sync.env).")

    import gspread
    gc = sync.get_sheets_client()
    sh = gc.open_by_key(OUTREACH_SPREADSHEET_ID)
    try:
        ws = sh.worksheet(TAB)
        first_run = False
    except gspread.exceptions.WorksheetNotFound:
        ws = sh.add_worksheet(title=TAB, rows=max(1000, donos + 200), cols=len(HEADER))
        first_run = True

    ordem_exist, by_key = load_existing(ws)
    excluded_hashes = {ohash(uid) for uid, u in users_by_id.items()
                       if ((u.get("email") or "").strip().lower() in EXCLUDE_EMAILS)}
    final_keys, auto_by_key, desf_by_key, status_by_key, novos_keys = merge_rows(
        groups, users_by_id, today_str, ordem_exist, by_key, excluded_hashes)
    write_merge(ws, final_keys, auto_by_key, desf_by_key, by_key, prev_n=len(ordem_exist))
    write_resumo(sh, status_by_key, desf_by_key, final_keys)
    if first_run:
        ensure_formatting(sh, ws)

    finalizados = sum(1 for k in final_keys if desf_by_key[k][1] == "Sim")
    print(f"mutirao_rascunho: OK | {len(final_keys)} pessoas "
          f"({len(novos_keys)} novas) | finalizados={finalizados} | first_run={first_run}")


if __name__ == "__main__":
    main()
