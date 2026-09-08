"""Regras puras do analytics v1; sem rede, credenciais ou dados pessoais.

Espelhadas no consumidor TypeScript e provadas pela mesma fixture sintética.
Datas de observação não são datas históricas de publicação/ativação.
"""
import re
import unicodedata
from collections import Counter, defaultdict

CONTRACT_VERSION = "2026-09-04.v1"


def valor_aprovado(proposals):
    approved = [p for p in proposals if not proposta_teste(p) and normal(p.get('status')) in ('aprovado', 'approved')]
    if any(p.get('valor_aprovado') in ('', None) for p in approved):
        return ''
    return round(sum(float(p['valor_aprovado']) for p in approved), 2)
PUBLICADOS = {"disponivel", "em execucao", "concluido"}
# Os dois status em que um projeto pode receber dinheiro. Concluido esta fora: ele
# ja publicou e ja terminou, entao nao e nem apto nem fila de reengajamento.
CAPTANDO = {"disponivel", "em execucao"}


def normal(value):
    return "".join(c for c in unicodedata.normalize("NFD", str(value or "").strip().lower())
                   if unicodedata.category(c) != "Mn")


def publicado(project):
    return normal(project.get("status")) in PUBLICADOS


def apto(project):
    return (normal(project.get("status")) in CAPTANDO
            and normal(project.get("expiracao_situacao")) == "vigente")


def a_reativar(project):
    """Publicado para captar, mas com o prazo final de captacao vencido.

    Nao e "quase apto": a tela de descoberta do incentivador ja esconde prazo
    vencido (`Matchmaking.tsx`, plataforma v3), entao o projeto nao aparece para
    ninguem e so volta a captar com renovacao de prazo. E a fila de trabalho da
    regua de reengajamento, e era o unico numero acionavel dentro do antigo card
    "Disponiveis" — que somava estes aos aptos e nao correspondia nem ao que o
    incentivador ve nem ao reporting (Tamyris, 08/09/2026).
    """
    return (normal(project.get("status")) in CAPTANDO
            and normal(project.get("expiracao_situacao")) == "expirado")


def sem_prazo(project):
    """Publicado para captar e sem data de prazo utilizavel.

    Vigia, nao metrica: um projeto assim nao e apto (o `apto` exige vigente) nem
    entra na fila de reativacao, entao sumiria da tela se ninguem o contasse. A
    condicao e por exclusao, e nao `== "sem_data"`, para que um quarto valor de
    `expiracao_situacao` apareca aqui em vez de evaporar.
    """
    return (normal(project.get("status")) in CAPTANDO
            and normal(project.get("expiracao_situacao")) not in ("vigente", "expirado"))


def proposta_teste(proposal):
    return bool(re.search(r"\btest(es?)?\b", normal(proposal.get("edital"))))


def records(header, rows):
    return [dict(zip(header, list(row) + [""] * (len(header) - len(row)))) for row in rows]


def funil_pessoas(users, projects):
    """Coorte recebida: pessoas distintas; resultados são estado atual da foto."""
    cohort = {u["user_hash"] for u in users
              if u.get("user_hash") and normal(u.get("role")) == "ong"
              and normal(u.get("is_migrado")) == "nao"}
    selected = {p["project_hash"]: p for p in projects
                if p.get("project_hash") and p.get("owner_hash") in cohort}
    owned = list(selected.values())
    created = {p["owner_hash"] for p in owned}
    published = {p["owner_hash"] for p in owned if publicado(p)}
    eligible = {p["owner_hash"] for p in owned if apto(p)}
    concentration = Counter(p["owner_hash"] for p in owned)
    return {"cadastros": len(cohort), "criaram": len(created),
            "publicaram": len(published), "aptos": len(eligible),
            "projetos": len(owned), "projetos_aptos": sum(apto(p) for p in owned),
            "maior_proprietario": max(concentration.values(), default=0)}


def recompute_lead_conversion(leads, users):
    """Recalcula TODA a ponte, sem usar o estado congelado do último pull.

    Termos de campanhas não Automatize não atribuem conversão. Vários usuários
    para o mesmo lead são detectados, não sobrescritos pela ordem das linhas.
    """
    by_term = defaultdict(list)
    for user in users:
        term = normal(user.get("utm_term"))
        if term and normal(user.get("origem_canal")) == "automatize":
            by_term[term].append(user)
    out = []
    for lead in leads:
        row = dict(lead)
        matches = by_term.get(normal(lead.get("lead_id")), [])
        row["cadastrou_plataforma"] = "sim" if matches else "nao"
        dates = sorted(u["data_cadastro"] for u in matches if u.get("data_cadastro"))
        row["data_cadastro_plataforma"] = dates[0] if dates else ""
        row["tem_projeto_plataforma"] = ("sim" if any(normal(u.get("tem_projeto")) == "sim"
                                                       for u in matches) else "nao")
        out.append(row)
    return out


def observe_projects(projects, previous, observed_at):
    """Primeira detecção no sync; baseline explícito, nunca retrodata evento."""
    old = {p.get("project_hash"): p for p in previous}
    out = []
    for project in projects:
        row = dict(project)
        prior = old.get(row.get("project_hash"), {})
        started = prior.get("inicio_observacao") or observed_at
        row["inicio_observacao"] = started
        for field, condition in (("primeira_publicacao_observada", publicado),
                                 ("primeira_aptidao_observada", apto)):
            row[field] = prior.get(field) or (observed_at if condition(row) else "")
        out.append(row)
    return out


def merge_leads(previous, incoming, users):
    by_id = {normal(r.get("lead_id")): dict(r) for r in previous if r.get("lead_id")}
    for row in incoming:
        if row.get("lead_id"):
            by_id[normal(row["lead_id"])] = dict(row)
    result = recompute_lead_conversion(list(by_id.values()), users)
    return sorted(result, key=lambda r: str(r.get("coletado_em") or ""), reverse=True)


def validate_source_counts(current, previous):
    for name in ("n_users", "n_projects"):
        count = int(current.get(name, 0))
        old = int(previous.get(name) or 0)
        if count <= 0 or (old and count < old * 0.8):
            raise ValueError(f"Fonte parcial: {name} abaixo do piso; publicação abortada")


def source_health(previous, success, now, warnings):
    """Sucesso de transporte não comprova completude de uma exportação limitada."""
    return {
        "analytics_contract": CONTRACT_VERSION,
        "plataforma_ultima_ingestao": now,
        "automatize_ultima_tentativa": now,
        "automatize_ultima_ingestao": now if success else previous.get("automatize_ultima_ingestao", ""),
        "automatize_estado": "parcial" if success else "indisponivel",
        "automatize_cobertura": "nao_comprovada",
        "automatize_motivo": ", ".join(sorted(warnings)) if warnings else "exportacao_sem_total_verificado",
    }


def atomic_replace_request(sheet_id, row_count, column_count, header, rows):
    """Um updateCells troca valores e limpa a cauda na mesma operação atômica."""
    values = []
    for row in [header] + rows:
        cells = []
        for value in row:
            if value is None or value == "":
                cells.append({})
            elif isinstance(value, bool):
                cells.append({"userEnteredValue": {"boolValue": value}})
            elif isinstance(value, (int, float)):
                cells.append({"userEnteredValue": {"numberValue": value}})
            else:
                cells.append({"userEnteredValue": {"stringValue": str(value)}})
        cells.extend({} for _ in range(column_count - len(cells)))
        values.append({"values": cells})
    values.extend({"values": [{} for _ in range(column_count)]} for _ in range(row_count - len(values)))
    return {"updateCells": {
        "range": {"sheetId": sheet_id, "startRowIndex": 0, "endRowIndex": row_count,
                  "startColumnIndex": 0, "endColumnIndex": column_count},
        "rows": values, "fields": "userEnteredValue",
    }}
