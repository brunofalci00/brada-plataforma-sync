"""
Outreach FRIOS: leads migrados SEM nenhum projeto -> planilha operacional do time.

Complemento do mutirao_rascunho (que cobre quem TEM projeto em rascunho). Aqui
listamos, POR PESSOA, os usuarios migrados (legacyId) role=ONG que nunca criaram
projeto na plataforma nova -> leads mais frios, prioridade menor, candidatos a
encaminhar pra Automatize (reativacao) ou pro time interno tratar. Detecta no
sync quem "ativou" (passou a ter >=1 projeto). Planilha DEDICADA e privada
(OUTREACH_SPREADSHEET_ID), MESMA do mutirao, ABA SEPARADA.

PII (repo PUBLICO = logs publicos no GitHub Actions):
- A planilha de saida CONTEM PII (nome/telefone/e-mail) de proposito: e privada,
  compartilhada so com o time + a service account.
- O STDOUT/LOG NUNCA imprime PII. Run normal e dry-run imprimem so contadores.
  --sample N e LOCAL-ONLY (imprime aviso); NUNCA usar --sample no CI.

Merge race-safe ESPELHADO do sync_outreach_rascunho (MANTER SINCRONIA se mexer
la): linhas estaveis por owner_hash (col A); run normal escreve SO AUTO (A:L) e
DESFECHO (S:V), nunca toca o MANUAL (M:R); full-rewrite keyed so quando uma linha
some (exclusao). Ativadores (criaram projeto) NAO sao removidos: viram
"Criou projeto?=sim" com "Detectado em" carimbado.

Reusa helpers puros do sync_outreach_rascunho (ohash/wa_link/_parse_date_any/
_col_a1/EXCLUDE_EMAILS) e do sync.py (Firestore/Auth/datas).
"""

import argparse
import datetime
import os
import re
from collections import Counter, defaultdict

import sync  # init_firestore, load_collection, load_auth_login_index, to_date, ms_to_date, BRT, sim_nao
from sync_outreach_rascunho import ohash, wa_link, _parse_date_any, _col_a1, is_excluded

OUTREACH_SPREADSHEET_ID = os.environ.get("OUTREACH_SPREADSHEET_ID", "")
TAB = "outreach_frios"
TAB_RESUMO = "Resumo Frios"  # NAO "Resumo" (o mutirao da clear() nele)

# ===================================================
# HEADER (22 col, A..V)
# ===================================================
HEADER = [
    "owner_hash",            # A  (oculto)
    "Proponente",            # B
    "WhatsApp",              # C
    "Abrir WhatsApp",        # D
    "E-mail",                # E
    "Tipo",                  # F  PJ/PF/""
    "Logou?",                # G
    "Último login",          # H
    "Onda de migração",      # I
    "Temperatura",           # J  Morno/Frio
    "Prioridade",            # K
    "Alerta",                # L  tel já ativo / tel duplicado / ""
    # --- MANUAL (time edita; sync preserva) ---
    "Encaminhado para",      # M
    "Responsável",           # N
    "Data 1º contato",       # O
    "Canal",                 # P
    "Status do contato",     # Q
    "Observações",           # R
    # --- DESFECHO (sync carimba) ---
    "Criou projeto?",        # S
    "Detectado em",          # T
    "Status do projeto criado",  # U
    "Dias até criar",        # V
]
COL = {h: i for i, h in enumerate(HEADER)}
N_AUTO = COL["Alerta"] + 1                 # A:L  -> 12
MANUAL_START = COL["Encaminhado para"]     # M (idx 12)
MANUAL_END = COL["Observações"]            # R (idx 17)
DESF_START = COL["Criou projeto?"]         # S (idx 18)
DESF_END = COL["Dias até criar"]           # V (idx 21)
MANUAL_DEFAULT = ["", "", "", "", "Não contatado", ""]  # M..R; Status do contato (Q) = 5o
STATUS_DEFAULT = "Não contatado"
ENCAMINHADO_OPCOES = ["Automatize (reativação)", "Interno", "E-mail", "—"]
CANAL_OPCOES = ["WhatsApp", "E-mail", "Ligação"]
STATUS_OPCOES = ["Não contatado", "Contatado", "Respondeu", "Sem resposta", "Opt-out"]


def _norm_phone(p):
    return re.sub(r"\D", "", str(p or ""))


def _tipo(user):
    tp = str((user or {}).get("tipoPessoa") or "")
    if "Jur" in tp:   # "Pessoa Jurídica..." (tolerante a acento quebrado)
        return "PJ"
    if "Fís" in tp or "Fis" in tp:
        return "PF"
    return ""


# ===================================================
# DERIVACAO
# ===================================================

def build_cold_auto(uid, user, login_idx, alerta):
    user = user or {}
    name = user.get("name") or ""
    phone = user.get("phone") or ""
    email = user.get("email") or ""
    url, valido = wa_link(phone)
    ms = login_idx.get(uid)
    logou = bool(ms)
    return {
        "owner_hash": ohash(uid),
        "Proponente": name,
        "WhatsApp": phone,
        "Abrir WhatsApp": url if valido else "validar nº / usar e-mail",
        "E-mail": email,
        "Tipo": _tipo(user),
        "Logou?": sync.sim_nao(logou),
        "Último login": sync.ms_to_date(ms) if ms else "",
        "Onda de migração": sync.to_date(user.get("migratedAt") or user.get("createdAt")),
        "Temperatura": "Morno" if logou else "Frio",
        "Prioridade": 1 if logou else 0,
        "Alerta": alerta,
        "_tel_valido": valido,
    }


def build_activator_auto(owner_hash, uid, user, login_idx, existing_row):
    """Saiu do cold (criou projeto). Contato fresco se o user existe, senao
    reaproveita o ultimo AUTO conhecido da planilha."""
    if user:
        a = build_cold_auto(uid, user, login_idx, "")
        a["Prioridade"] = -1  # ja ativou: nao disputa o topo dos novos
        return a
    d = {h: (existing_row[COL[h]] if COL[h] < len(existing_row) else "") for h in HEADER[:N_AUTO]}
    d["Prioridade"] = -1
    d["_tel_valido"] = bool(str(d.get("Abrir WhatsApp", "")).startswith("http"))
    return d


def _status_primeiro_projeto(uid, proj_by_owner):
    """Status do projeto que tirou o lead do cold (o mais antigo)."""
    lst = proj_by_owner.get(uid) or []
    if not lst:
        return ""
    lst = sorted(lst, key=lambda t: t[0] or "")  # ISO ordena lexicograficamente
    return lst[0][1] or ""


# ===================================================
# MERGE / ESCRITA (espelhado de sync_outreach_rascunho — manter sincronia)
# ===================================================

def _auto_list(auto):
    return [auto.get(h, "") for h in HEADER[:N_AUTO]]


def load_existing(ws):
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


def merge_rows(cold, users_by_id, proj_by_owner, login_idx, alerta_by_uid,
               today_str, ordem_exist, by_key, excluded_hashes=frozenset()):
    """cold = {uid: user} (populacao atual). Retorna
    (final_keys, auto_by_key, desf_by_key, status_by_key, novos_keys)."""
    hash_to_uid = {ohash(uid): uid for uid in users_by_id}
    cold_by_hash = {ohash(uid): (uid, u) for uid, u in cold.items()}
    auto_by_key, desf_by_key, status_by_key = {}, {}, {}

    def desfecho(key, criou, status_proj):
        prev = by_key.get(key)
        prev_det = prev[COL["Detectado em"]] if prev else ""
        prev_contato = prev[COL["Data 1º contato"]] if prev else ""
        detectado = prev_det
        if criou and not detectado:
            detectado = today_str
        dias = ""
        d0, d1 = _parse_date_any(prev_contato), _parse_date_any(detectado)
        if d0 and d1:
            dias = (d1 - d0).days
        return ["sim" if criou else "nao", detectado, status_proj, dias]

    def status_of(key):
        prev = by_key.get(key)
        s = prev[COL["Status do contato"]].strip() if prev else ""
        return s or STATUS_DEFAULT

    # 1) linhas existentes (mantem ordem/posicao)
    for key in ordem_exist:
        if key in excluded_hashes:
            continue
        if key in cold_by_hash:
            uid, user = cold_by_hash[key]
            auto_by_key[key] = build_cold_auto(uid, user, login_idx, alerta_by_uid.get(uid, ""))
            desf_by_key[key] = desfecho(key, False, "")
        else:  # ativador retido (criou projeto)
            uid = hash_to_uid.get(key)
            status_proj = _status_primeiro_projeto(uid, proj_by_owner) if uid else ""
            auto_by_key[key] = build_activator_auto(key, uid, users_by_id.get(uid),
                                                    login_idx, by_key.get(key, []))
            desf_by_key[key] = desfecho(key, True, status_proj)
        status_by_key[key] = status_of(key)

    # 2) novos cold -> anexa ordenado por prioridade (mornos no topo)
    novos = []
    for uid, user in cold.items():
        key = ohash(uid)
        if key in by_key:
            continue
        auto = build_cold_auto(uid, user, login_idx, alerta_by_uid.get(uid, ""))
        auto_by_key[key] = auto
        desf_by_key[key] = desfecho(key, False, "")
        status_by_key[key] = STATUS_DEFAULT
        novos.append((key, auto["Prioridade"]))
    novos.sort(key=lambda x: -x[1])
    novos_keys = [k for k, _ in novos]

    # exclui as chaves removidas (conta interna que estava na planilha) -> cai no
    # full-rewrite keyed do write_merge, que realinha e limpa a linha.
    final_keys = [k for k in ordem_exist if k not in excluded_hashes] + novos_keys
    return final_keys, auto_by_key, desf_by_key, status_by_key, novos_keys


def write_merge(ws, final_keys, auto_by_key, desf_by_key, by_key, novos_keys, prev_n=0):
    """Merge hibrido keyed por owner_hash. Run normal escreve SO AUTO (A:L) e
    DESFECHO (S:V); MANUAL (M:R) intocado. Full-rewrite keyed so quando uma linha
    some (realinha o MANUAL ao dono)."""
    n = len(final_keys)
    last = n + 1
    ws.update(values=[HEADER], range_name="A1", value_input_option="USER_ENTERED")
    if n == 0:
        if prev_n > 0:
            ws.batch_clear([f"A2:V{prev_n + 1}"])
        return

    final_set = set(final_keys)
    removeu = any(k not in final_set for k in by_key)

    if removeu:
        rows = []
        for k in final_keys:
            prev = by_key.get(k)
            manual = ([prev[i] if i < len(prev) else "" for i in range(MANUAL_START, MANUAL_END + 1)]
                      if prev is not None else list(MANUAL_DEFAULT))
            rows.append(_auto_list(auto_by_key[k]) + manual + desf_by_key[k])
        ws.update(values=rows, range_name=f"A2:V{last}", value_input_option="USER_ENTERED")
    else:
        a_end = _col_a1(N_AUTO - 1)          # L
        t_start, w_end = _col_a1(DESF_START), _col_a1(DESF_END)  # S, V
        ws.update(values=[_auto_list(auto_by_key[k]) for k in final_keys],
                  range_name=f"A2:{a_end}{last}", value_input_option="USER_ENTERED")
        ws.update(values=[desf_by_key[k] for k in final_keys],
                  range_name=f"{t_start}2:{w_end}{last}", value_input_option="USER_ENTERED")
        if novos_keys:
            first_new = (n - len(novos_keys)) + 2
            n_start, s_end = _col_a1(MANUAL_START), _col_a1(MANUAL_END)  # M, R
            ws.update(values=[list(MANUAL_DEFAULT) for _ in novos_keys],
                      range_name=f"{n_start}{first_new}:{s_end}{last}",
                      value_input_option="USER_ENTERED")

    if prev_n > n:
        ws.batch_clear([f"A{last + 1}:V{prev_n + 1}"])


def write_resumo(sh, status_by_key, desf_by_key, auto_by_key, by_key, final_keys):
    import gspread
    total = len(final_keys)
    mornos = sum(1 for k in final_keys if auto_by_key[k].get("Temperatura") == "Morno")
    frios = total - mornos
    ativaram = sum(1 for k in final_keys if desf_by_key[k][0] == "sim")
    # encaminhamento (coluna manual M) lido do estado atual da planilha
    enc = Counter()
    for k in final_keys:
        prev = by_key.get(k)
        v = (prev[COL["Encaminhado para"]].strip() if prev and COL["Encaminhado para"] < len(prev) else "")
        enc[v or "(não roteado)"] += 1
    pct = f"{(ativaram / total * 100):.1f}%" if total else "0%"
    linhas = [
        ["Métrica", "Valor"],
        ["Leads frios na lista", total],
        ["Mornos (logaram, sem projeto)", mornos],
        ["Frios (nunca logaram)", frios],
        ["Ativaram (criaram projeto)", ativaram],
        ["% ativação", pct],
        ["", ""],
        ["Encaminhamento", ""],
    ]
    for dest, q in sorted(enc.items(), key=lambda x: -x[1]):
        linhas.append([f"  {dest}", q])
    try:
        ws = sh.worksheet(TAB_RESUMO)
    except gspread.exceptions.WorksheetNotFound:
        ws = sh.add_worksheet(title=TAB_RESUMO, rows=30, cols=2)
    ws.clear()
    ws.update(values=linhas, range_name="A1", value_input_option="USER_ENTERED")


def ensure_formatting(sh, ws):
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

    def dv(col_idx, opcoes):
        return {"setDataValidation": {
            "range": {"sheetId": sid, "startRowIndex": 1,
                      "startColumnIndex": col_idx, "endColumnIndex": col_idx + 1},
            "rule": {"condition": {"type": "ONE_OF_LIST",
                                   "values": [{"userEnteredValue": o} for o in opcoes]},
                     "showCustomUi": True, "strict": False}}}
    reqs.append(dv(COL["Encaminhado para"], ENCAMINHADO_OPCOES))
    reqs.append(dv(COL["Canal"], CANAL_OPCOES))
    reqs.append(dv(COL["Status do contato"], STATUS_OPCOES))

    def cond_eq(col_idx, value, color):
        return {"addConditionalFormatRule": {"index": 0, "rule": {
            "ranges": [{"sheetId": sid, "startRowIndex": 1,
                        "startColumnIndex": col_idx, "endColumnIndex": col_idx + 1}],
            "booleanRule": {"condition": {"type": "TEXT_EQ",
                                          "values": [{"userEnteredValue": value}]},
                            "format": {"backgroundColor": color}}}}}

    def cond_notblank(col_idx, color):
        return {"addConditionalFormatRule": {"index": 0, "rule": {
            "ranges": [{"sheetId": sid, "startRowIndex": 1,
                        "startColumnIndex": col_idx, "endColumnIndex": col_idx + 1}],
            "booleanRule": {"condition": {"type": "NOT_BLANK"},
                            "format": {"backgroundColor": color}}}}}

    reqs.append(cond_eq(COL["Criou projeto?"], "sim", {"red": 0.72, "green": 0.88, "blue": 0.72}))
    reqs.append(cond_eq(COL["Temperatura"], "Morno", {"red": 0.99, "green": 0.90, "blue": 0.71}))
    reqs.append(cond_eq(COL["Status do contato"], "Opt-out", {"red": 0.96, "green": 0.78, "blue": 0.78}))
    reqs.append(cond_notblank(COL["Alerta"], {"red": 0.98, "green": 0.85, "blue": 0.65}))
    sh.batch_update({"requests": reqs})


# ===================================================
# MAIN
# ===================================================

def main():
    ap = argparse.ArgumentParser(description="Outreach frios (migrados sem projeto) -> planilha do time")
    ap.add_argument("--dry-run", action="store_true", help="Le Firestore e imprime contadores, sem escrever")
    ap.add_argument("--sample", type=int, default=0,
                    help="LOCAL-ONLY: imprime N contatos REAIS (PII) p/ sign-off. NUNCA usar no CI.")
    args = ap.parse_args()

    today_str = datetime.datetime.now(sync.BRT).strftime("%Y-%m-%d")
    print("=== Outreach frios (migrados sem projeto -> planilha do time) ===")
    db = sync.init_firestore()
    users_by_id = {uid: d for uid, d in sync.load_collection(db, "users")}
    projects_raw = sync.load_collection(db, "projects")
    try:
        login_idx = sync.load_auth_login_index()
    except Exception as e:  # Auth indisponivel: degrada (todos viram Frio), nao quebra
        login_idx = {}
        print(f"AVISO: login_idx indisponivel ({e}); Logou?/Temperatura degradam pra Frio")

    # owners (uid com >=1 projeto) + status do 1o projeto por owner (p/ ativadores)
    owners = set()
    proj_by_owner = defaultdict(list)
    for pid, p in projects_raw:
        oid = p.get("ownerId") or ""
        if oid:
            owners.add(oid)
            proj_by_owner[oid].append((sync.to_date(p.get("createdAt")), str(p.get("status") or "").strip()))

    # populacao COLD: migrado (legacyId) + role ONG + sem projeto + nao-excluido
    cold = {}
    excluidos = 0
    for uid, d in users_by_id.items():
        if not d.get("legacyId"):
            continue
        if str(d.get("role") or "") != "ONG":
            continue
        if uid in owners:
            continue
        if is_excluded(d.get("email")):
            excluidos += 1
            continue
        cold[uid] = d

    # ALERTA: telefone duplicado (ja-ativo / dentro do cold)
    phone_to_uids = defaultdict(list)
    for uid, d in users_by_id.items():
        ph = _norm_phone(d.get("phone"))
        if len(ph) >= 10:
            phone_to_uids[ph].append(uid)
    cold_uids = set(cold)
    alerta_by_uid = {}
    for uid in cold:
        ph = _norm_phone(users_by_id[uid].get("phone"))
        if len(ph) < 10:
            alerta_by_uid[uid] = ""
            continue
        outros = [u for u in phone_to_uids[ph] if u != uid]
        if any(u in owners for u in outros):
            alerta_by_uid[uid] = "tel já ativo em outra conta"
        elif any(u in cold_uids for u in outros):
            alerta_by_uid[uid] = "tel duplicado na lista"
        else:
            alerta_by_uid[uid] = ""

    # contadores (sem PII)
    n = len(cold)
    tel_validos = sum(1 for uid in cold if wa_link(cold[uid].get("phone"))[1])
    mornos = sum(1 for uid in cold if login_idx.get(uid))
    frios = n - mornos
    pj = sum(1 for uid in cold if _tipo(cold[uid]) == "PJ")
    pf = sum(1 for uid in cold if _tipo(cold[uid]) == "PF")
    sem_tipo = n - pj - pf
    alerta_ja_ativo = sum(1 for v in alerta_by_uid.values() if v == "tel já ativo em outra conta")
    alerta_dup = sum(1 for v in alerta_by_uid.values() if v == "tel duplicado na lista")
    print(f"firestore: users={len(users_by_id)} projects={len(projects_raw)} owners_com_proj={len(owners)}")
    print(f"cold (migrado+ONG+sem projeto): {n} | tel_valido={tel_validos} | mornos={mornos} | "
          f"frios={frios} | PJ={pj} PF={pf} sem_tipo={sem_tipo} | excluidos={excluidos}")
    print(f"alerta: ja_ativo_outra_conta={alerta_ja_ativo} | duplicado_na_lista={alerta_dup}")

    if args.sample:
        print(f"\n[AMOSTRA LOCAL — CONTEM PII — {args.sample} contatos] "
              f"(NUNCA rodar --sample no CI/repo publico)")
        ordenado = sorted(cold.items(), key=lambda kv: -(1 if login_idx.get(kv[0]) else 0))
        for uid, u in ordenado[:args.sample]:
            a = build_cold_auto(uid, u, login_idx, alerta_by_uid.get(uid, ""))
            print(f"  - {a['Proponente']} | {a['WhatsApp']} | {a['Abrir WhatsApp']} | {a['E-mail']} | "
                  f"{a['Tipo']} | {a['Temperatura']} | onda={a['Onda de migração']} | alerta={a['Alerta'] or '-'}")

    if args.dry_run:
        print("\noutreach_frios: DRY-RUN (nada escrito)")
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
        ws = sh.add_worksheet(title=TAB, rows=max(1000, n + 200), cols=len(HEADER))
        first_run = True

    ordem_exist, by_key = load_existing(ws)
    excluded_hashes = {ohash(uid) for uid, u in users_by_id.items()
                       if is_excluded(u.get("email"))}
    final_keys, auto_by_key, desf_by_key, status_by_key, novos_keys = merge_rows(
        cold, users_by_id, proj_by_owner, login_idx, alerta_by_uid,
        today_str, ordem_exist, by_key, excluded_hashes)
    write_merge(ws, final_keys, auto_by_key, desf_by_key, by_key, novos_keys, prev_n=len(ordem_exist))
    write_resumo(sh, status_by_key, desf_by_key, auto_by_key, by_key, final_keys)
    if first_run:
        ensure_formatting(sh, ws)

    ativaram = sum(1 for k in final_keys if desf_by_key[k][0] == "sim")
    print(f"outreach_frios: OK | {len(final_keys)} pessoas ({len(novos_keys)} novas) | "
          f"ativaram={ativaram} | first_run={first_run}")


if __name__ == "__main__":
    main()
