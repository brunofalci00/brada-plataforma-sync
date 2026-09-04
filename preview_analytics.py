"""Snapshot de recuperação e preview isolado. Não publica raw nem substitui Dashboard."""
import argparse
import datetime
import json
from pathlib import Path
import sync
import dashboard_analytics


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--candidate', required=True)
    ap.add_argument('--snapshot-dir', required=True)
    ap.add_argument('--preview-title', required=True)
    args = ap.parse_args()
    candidate = json.loads(Path(args.candidate).read_text(encoding='utf-8'))
    if candidate['spreadsheet_id'] != sync.SPREADSHEET_ID:
        raise ValueError('Candidato pertence a outra planilha')
    sh = sync.get_sheets_client().open_by_key(sync.SPREADSHEET_ID)
    if args.preview_title in [ws.title for ws in sh.worksheets()]:
        raise ValueError('Aba já existe; não sobrescrevo preview nem aba do usuário')
    target = Path(args.snapshot_dir).resolve()
    target.mkdir(parents=True, exist_ok=False)
    snapshot = sh.fetch_sheet_metadata(params={'includeGridData': True})
    (target / 'spreadsheet-before.json').write_text(json.dumps(snapshot, ensure_ascii=False), encoding='utf-8')
    now = datetime.datetime.fromisoformat(candidate['observed_at'])
    ws = sh.add_worksheet(title=args.preview_title, rows=dashboard_analytics.ROWS, cols=26)
    sh.batch_update({'requests': dashboard_analytics.requests(ws.id, candidate['metrics'], now, sh.fetch_sheet_metadata(), True)})
    print(json.dumps({'preview_gid': ws.id, 'preview_url': sh.url + '#gid=' + str(ws.id), 'snapshot_dir': str(target)}))


if __name__ == '__main__':
    main()
