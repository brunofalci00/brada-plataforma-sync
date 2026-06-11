"""
Leitura da PLATAFORMA ANTIGA (planilha "Todos os Projetos Brada", aba
"Projetos Plataforma") pro KPI comparativo de migracao.

Por que existe: a antiga tinha ~200 projetos ativos; a nova tem ~4 migrados
visiveis. O dashboard da gerencia precisa do comparativo honesto + serie da
reativacao. Diagnostico completo no vault:
01_Projetos/Novo_Site_Brada/Migracao/diagnostico_gap_reativacao_pos_golive_11jun.md

Decisoes de desenho:
- BASELINE CONGELADO POR FILTRO (nao por numero hardcoded): a planilha segue
  recebendo linhas do Apps Script da antiga (+7 pos-corte ja observados).
  Baseline = fim_execucao >= CORTE e data_adicao <= CORTE (go-live 08/06).
  Reprodutivel e imune a linhas novas.
- Serie VIVA em paralelo (situacao sem corte): mede a decadencia da base
  (136 vencem em 2026) e detecta cadastros novos na antiga.
- Guard de data: ano <1990 ou >2100 -> bucket 'data_invalida' (existe um
  typo real ano 7202 na fonte) + aviso em meta_sync. Apps Script NUNCA
  atualiza linha: projeto renovado na antiga aparece expirado aqui
  (subconta o denominador — caveat documentado no dicionario de KPIs).
- PII: e-mail/nome/telefone da planilha NAO saem deste modulo; so legacy_id
  (que vira hash no sync) e datas.
"""

import datetime
import re

PLANILHA_ANTIGA_ID = "1fKsnd4Q3o4mM93KZcshfPs3DnA1QnpcWzPbRe0YQVdc"
ABA_PROJETOS = "Projetos Plataforma"
CORTE_GO_LIVE = datetime.date(2026, 6, 8)


def _parse_br(s):
    """DD/MM/YYYY -> date | None (vazio) | 'INVALIDA' (typo tipo ano 7202)."""
    s = (s or "").strip()
    if not s:
        return None
    for fmt in ("%d/%m/%Y", "%Y-%m-%d", "%d/%m/%y"):
        try:
            d = datetime.datetime.strptime(s, fmt).date()
            return d if 1990 <= d.year <= 2100 else "INVALIDA"
        except ValueError:
            continue
    return "INVALIDA"


def carregar_planilha(gc):
    """Le a aba canonica. Retorna lista de dicts:
    {legacy_id, fim_execucao (date|None|'INVALIDA'), data_adicao (idem)}."""
    ws = gc.open_by_key(PLANILHA_ANTIGA_ID).worksheet(ABA_PROJETOS)
    vals = ws.get_all_values()
    header = vals[0]
    idx = {re.sub(r"\s+", " ", h.strip()): i for i, h in enumerate(header) if h.strip()}

    def col(row, *names):
        for n in names:
            i = idx.get(n)
            if i is not None and i < len(row):
                return row[i].strip()
        return ""

    out = []
    for r in vals[1:]:
        if not any(c.strip() for c in r):
            continue
        out.append({
            "legacy_id": col(r, "Id do projeto"),
            "fim_execucao": _parse_br(col(r, "Data Fim de Execucao", "Data Fim de Execução")),
            "data_adicao": _parse_br(col(r, "Data de adição na plataforma",
                                          "Data de adicao na plataforma")),
        })
    return out


def situacao(fim_execucao, ref):
    if fim_execucao is None:
        return "sem_data"
    if fim_execucao == "INVALIDA":
        return "data_invalida"
    return "ativo" if fim_execucao >= ref else "expirado"


def no_baseline(row):
    """Congelado no go-live: ativo em 08/06 E ja existia na planilha em 08/06."""
    fim, adicao = row["fim_execucao"], row["data_adicao"]
    if not isinstance(fim, datetime.date):
        return False
    if isinstance(adicao, datetime.date) and adicao > CORTE_GO_LIVE:
        return False
    return fim >= CORTE_GO_LIVE
