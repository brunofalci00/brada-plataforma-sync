"""
Publicar projetos em RASCUNHO VIGENTES (tese Tamyris 17/06).

Tira do rascunho os projetos com CAC vigente (status='Disponível'), pra
aparecerem pro investidor e contarem como ativos no dashboard. Eles seguem
incompletos (sem descrição/orçamento/diário, que só o dono preenche) -> marca
`dadosEmAtualizacao` pro selo/segmentação. O mutirão de WhatsApp segue cobrando
a completar.

So escreve DADO no Firestore (status + markers). NAO mexe no codigo da
plataforma. Auditado 17/06: publicar NAO dispara e-mail/notificacao (sem Cloud
Function em projects); unica vitrine de investidor = matchmaking rankeado por IA.

Seguranca:
- Escrita POR DOC em TRANSACAO que rele o status (status e mutavel pelo dono;
  batch cego poderia rebaixar um 'Em Execução'/'Concluído' que o dono avancou).
- Idempotente: pula sse status != 'Rascunho'. Rascunho COM autoPublicadoEm =
  re-publicacao (o dono editou e a plataforma reverteu) -> re-publica e bumpa.
- NUNCA escreve description/budget/diario/progress/updatedAt.
- Reversivel: --revert RUN_ID restaura (so o que esta run tocou e ainda 'Disponível').
- Log CSV de backup por run em logs/.

Regra de "completo"/"rascunho" = mesma porta dos 11 campos do front, reusada de
sync_outreach_rascunho.py (sem drift).

Uso:
  python publicar_rascunhos_vigentes.py                      # dry-run (default)
  python publicar_rascunhos_vigentes.py --apply --canario 10 # canario estratificado
  python publicar_rascunhos_vigentes.py --apply              # full
  python publicar_rascunhos_vigentes.py --revert 2026-06-17T15:30:00-03:00
"""

import argparse
import csv
import datetime
from collections import Counter
from pathlib import Path

import sync  # init_firestore, to_date, BRT, hash_id
from sync_outreach_rascunho import missing_labels  # porta exata dos 11 campos

LOG_DIR = Path(__file__).resolve().parent / "logs"
STATUS_PUBLICADO = "Disponível"
STATUS_RASCUNHO = "Rascunho"
STATUS_AVANCADOS = ("Em Execução", "Concluído", "Aprovado")  # nunca reverter por cima


def is_complete(d):
    return not missing_labels(d)


def vigente(d, today_str):
    ds = sync.to_date(d.get("cacExpirationDate"))
    return bool(ds) and ds >= today_str


# ===================================================
# CANDIDATOS
# ===================================================

def build_candidates(db, today_str):
    """Retorna (candidatos, stats). candidato = status=='Rascunho' E vigente."""
    cand = []
    stats = Counter()
    for doc in db.collection("projects").stream():
        d = doc.to_dict() or {}
        status = str(d.get("status") or "").strip()
        if status != STATUS_RASCUNHO:
            stats["ja_visivel_ou_outro"] += 1
            continue
        if not vigente(d, today_str):
            stats["rascunho_expirado_ou_sem_data"] += 1
            continue
        # candidato
        if d.get("autoPublicadoEm"):
            stats["re_publica"] += 1   # voltou pra rascunho (dono editou)
        else:
            stats["novos"] += 1
        if is_complete(d):
            stats["anomalia_completo_mas_rascunho"] += 1
        cand.append((doc.id, d))
    return cand, stats


def _estratificar(cand):
    """Canario representativo: prioriza dono recem-editado (maior risco de
    reversao), depois mistura migrado/nativo."""
    def risco(item):
        _, d = item
        return (1 if d.get("updatedAt") else 0, 1 if d.get("legacyId") else 0)
    return sorted(cand, key=risco, reverse=True)


# ===================================================
# ESCRITA (transacional por doc)
# ===================================================

def publish_one(db, doc_id, run_iso):
    from google.cloud import firestore
    ref = db.collection("projects").document(doc_id)
    txn = db.transaction()

    @firestore.transactional
    def _do(transaction):
        snap = ref.get(transaction=transaction)
        d = snap.to_dict() or {}
        if str(d.get("status") or "").strip() != STATUS_RASCUNHO:
            return ("skip_nao_rascunho", str(d.get("status") or ""))
        incompleto = not is_complete(d)
        transaction.update(ref, {
            "status": STATUS_PUBLICADO,
            "autoPublicadoEm": run_iso,
            "dadosEmAtualizacao": incompleto,
            "autoPublicadoStatusAnterior": STATUS_RASCUNHO,
            "autoPublicadoCount": firestore.Increment(1),
        })
        return ("publicado", "selo" if incompleto else "completo")

    return _do(txn)


def revert_one(db, doc_id, run_iso):
    from google.cloud import firestore
    ref = db.collection("projects").document(doc_id)
    txn = db.transaction()

    @firestore.transactional
    def _do(transaction):
        snap = ref.get(transaction=transaction)
        d = snap.to_dict() or {}
        if d.get("autoPublicadoEm") != run_iso:
            return ("skip_outra_run", str(d.get("status") or ""))
        status = str(d.get("status") or "").strip()
        if status in STATUS_AVANCADOS:
            return ("skip_dono_avancou", status)   # nao destruir progresso real
        if status != STATUS_PUBLICADO:
            return ("skip_estado_inesperado", status)
        if is_complete(d):
            # dono completou: mantem publicado, so tira o selo
            transaction.update(ref, {"dadosEmAtualizacao": firestore.DELETE_FIELD})
            return ("mantido_completo", status)
        transaction.update(ref, {
            "status": STATUS_RASCUNHO,
            "dadosEmAtualizacao": firestore.DELETE_FIELD,
            "autoPublicadoEm": firestore.DELETE_FIELD,
            "autoPublicadoStatusAnterior": firestore.DELETE_FIELD,
        })
        return ("revertido", STATUS_RASCUNHO)

    return _do(txn)


def _open_log(prefix, run_iso, header):
    LOG_DIR.mkdir(exist_ok=True)
    safe = run_iso.replace(":", "").replace("-", "").replace("T", "_")[:15]
    path = LOG_DIR / f"{prefix}_{safe}.csv"
    fh = open(path, "w", newline="", encoding="utf-8-sig")
    w = csv.writer(fh)
    w.writerow(header)
    return fh, w, path


# ===================================================
# MAIN
# ===================================================

def main():
    ap = argparse.ArgumentParser(description="Publicar rascunhos vigentes")
    ap.add_argument("--apply", action="store_true", help="escreve no Firestore (default: dry-run)")
    ap.add_argument("--canario", type=int, default=0, help="aplica so nos N (estratificado por risco)")
    ap.add_argument("--revert", metavar="RUN_ISO", default="", help="reverte os docs daquela run (autoPublicadoEm==RUN_ISO)")
    args = ap.parse_args()

    today_str = datetime.datetime.now(sync.BRT).strftime("%Y-%m-%d")
    print("=== Publicar rascunhos vigentes ===")
    db = sync.init_firestore()

    # ---- REVERT ----
    if args.revert:
        run_iso = args.revert
        from google.cloud import firestore
        alvo = [doc.id for doc in
                db.collection("projects").where("autoPublicadoEm", "==", run_iso).stream()]
        print(f"revert run={run_iso}: {len(alvo)} docs marcados")
        if not args.apply:
            print("[dry-run] nada revertido. Use --apply --revert RUN_ISO pra valer.")
            return
        fh, w, path = _open_log("revert_rascunhos", run_iso, ["doc_id", "resultado", "status_final"])
        res = Counter()
        with fh:
            for doc_id in alvo:
                r, st = revert_one(db, doc_id, run_iso)
                res[r] += 1
                w.writerow([doc_id, r, st])
        print("revert:", dict(res), "| log:", path)
        return

    # ---- DRY-RUN / PUBLISH ----
    cand, stats = build_candidates(db, today_str)
    print(f"candidatos (rascunho+vigente): {len(cand)} | {dict(stats)}")
    miss = Counter(lbl for _, d in cand for lbl in missing_labels(d))
    print("falta nos candidatos:", dict(miss.most_common()))
    donos = len(set(d.get("ownerId") for _, d in cand))
    print(f"donos distintos: {donos}")

    alvo = _estratificar(cand)
    if args.canario:
        alvo = alvo[: args.canario]

    if not args.apply:
        print(f"[dry-run] NADA escrito. Publicaria {len(alvo)} doc(s)"
              f"{f' (canario {args.canario})' if args.canario else ''}. Amostra (5):")
        for doc_id, d in alvo[:5]:
            print(f"  doc={doc_id} legacy={d.get('legacyId') or '-'} "
                  f"cac={sync.to_date(d.get('cacExpirationDate'))} "
                  f"selo={'sim' if not is_complete(d) else 'nao'} "
                  f"editou={'sim' if d.get('updatedAt') else 'nao'}")
        return

    run_iso = datetime.datetime.now(sync.BRT).isoformat(timespec="seconds")
    print(f">>> APLICANDO em {len(alvo)} doc(s) | RUN_ID={run_iso}"
          f"{f' (canario {args.canario})' if args.canario else ''}")
    fh, w, path = _open_log("publicar_rascunhos", run_iso,
                            ["doc_id", "legacy_id", "status_antes", "resultado", "selo", "autoPublicadoEm"])
    res = Counter()
    with fh:
        for doc_id, d in alvo:
            r, extra = publish_one(db, doc_id, run_iso)
            res[r] += 1
            w.writerow([doc_id, d.get("legacyId") or "", STATUS_RASCUNHO, r, extra, run_iso])
    print("resultado:", dict(res))
    print(f"RUN_ID={run_iso}  (pra desfazer: --apply --revert {run_iso})")
    print(f"log: {path}")


if __name__ == "__main__":
    main()
