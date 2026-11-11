"""Leitura compartilhada do Google Analytics Data API (GA4).

Usada por sync_comunidade.py (propriedades da Comunidade) e por sync.py
(propriedade da Plataforma, 537748462, match.brada.social). Credencial e
sempre a mesma: a conta de servico
dashboard-leitura@brada-comunidade-c1ba5.iam.gserviceaccount.com, que
precisa do papel Leitor concedido manualmente em cada propriedade nova
(nao e automatico - ver handoff_dashboard_ecossistema_11nov).

So leitura, so agregados: o GA4 ja agrega por sessao/pagina, nunca por
pessoa, entao nada daqui carrega PII.
"""

import json
import os

ESCOPOS = ["https://www.googleapis.com/auth/cloud-platform",
           "https://www.googleapis.com/auth/analytics.readonly"]
URL_RELATORIO = "https://analyticsdata.googleapis.com/v1beta/properties/{}:runReport"
TOP_PAGINAS = 4


def credenciais(sa_json, sa_file, token_env=None):
    """sa_json: valor de uma env var com o JSON inteiro (CI, secret).
    sa_file: caminho local (dev, ~/.brada-secrets/*.json).
    token_env: nome de uma env var com access token (dev, sem nenhuma
    das duas acima - ex.: token tirado do Firebase CLI de quem ja tem
    acesso ao projeto)."""
    if sa_json:
        from google.oauth2 import service_account
        info = json.loads(sa_json.encode("utf-8").decode("utf-8-sig"))
        return service_account.Credentials.from_service_account_info(info, scopes=ESCOPOS)
    if sa_file and os.path.exists(sa_file):
        from google.oauth2 import service_account
        return service_account.Credentials.from_service_account_file(sa_file, scopes=ESCOPOS)
    token = os.environ.get(token_env) if token_env else None
    if token:
        from google.oauth2.credentials import Credentials
        return Credentials(token)
    raise SystemExit("Sem credencial de Analytics (SA JSON, SA file ou access token).")


def _relatorio(sessao, propriedade, dimensoes, metricas, limite=10):
    corpo = {
        "dateRanges": [{"startDate": "30daysAgo", "endDate": "today"}],
        "dimensions": [{"name": d} for d in dimensoes],
        "metrics": [{"name": m} for m in metricas],
        "limit": limite,
        "orderBys": [{"metric": {"metricName": metricas[0]}, "desc": True}],
    }
    r = sessao.post(URL_RELATORIO.format(propriedade), json=corpo, timeout=60)
    r.raise_for_status()
    return [([d["value"] for d in row.get("dimensionValues", [])],
             [float(m["value"]) for m in row["metricValues"]]) for row in r.json().get("rows", [])]


def coletar(creds, propriedades, dimensao_pagina="unifiedScreenName"):
    """Le so a primeira propriedade que responder. Falha de GA nao derruba
    quem chamou: devolve None, e quem publica decide o que mostrar no lugar."""
    from google.auth.transport.requests import AuthorizedSession
    sessao = AuthorizedSession(creds)
    for prop in propriedades:
        try:
            return {
                "propriedade": prop,
                "totais": _relatorio(sessao, prop, [], ["activeUsers", "sessions", "screenPageViews"]),
                "novo_recorrente": _relatorio(sessao, prop, ["newVsReturning"], ["activeUsers"]),
                "paginas": _relatorio(sessao, prop, [dimensao_pagina], ["screenPageViews"], 20),
            }
        except Exception as erro:  # noqa: BLE001 - registra e segue sem o bloco
            print(f"::warning::Google Analytics {prop} indisponivel: {erro}")
    return None


def _taxa(parte, todo):
    return parte / todo if todo else 0


def resumo(ga, limpar_pagina=lambda nome: nome):
    """ga = saida de coletar(), ou None (nenhuma propriedade respondeu).
    Devolve ativos/sessoes/paginas_vistas/recorrentes/taxa_retorno/top, ou
    None."""
    if not ga:
        return None
    totais = ga["totais"][0][1] if ga["totais"] else [0, 0, 0]
    por_tipo = {dims[0]: vals[0] for dims, vals in ga["novo_recorrente"]}
    paginas = [(vals[0], limpar_pagina(dims[0])) for dims, vals in ga["paginas"]
               if dims and dims[0] not in ("", "(not set)")]
    return {
        "ativos": totais[0], "sessoes": totais[1], "paginas_vistas": totais[2],
        "recorrentes": por_tipo.get("returning", 0),
        "taxa_retorno": _taxa(por_tipo.get("returning", 0), totais[0]),
        "top": paginas[:TOP_PAGINAS],
    }
