# -*- coding: utf-8 -*-
"""
Filtros compartilhados pelas reguas de e-mail da plataforma Brada.

Fonte unica de tres coisas que estavam espalhadas ou erradas:

  1. QUEM NUNCA RECEBE  — interno, parceiro, dominio descartavel, conta de teste.
     Em 05/08 sairam e-mails para "xxxx", "Teste", "ccc" e para criape.com.br.

  2. QUANDO A PESSOA ACESSOU  — o campo `lastLoginAt` do documento em `users`
     esta VAZIO nos 1.096 usuarios. O dado real vive no Firebase Auth
     (`last_sign_in_timestamp`), e so 142 pessoas tem login. Qualquer regua que
     leia o Firestore trata a base inteira como inativa.

  3. QUEM JA FOI TOCADO  — dedup entre reguas, pra ninguem receber dois e-mails
     nossos na mesma semana.

Este modulo nao depende de nenhuma regua: sao as reguas que dependem dele.
"""
import datetime as dt
import re

# --------------------------------------------------------------------------- #
# 1. Exclusoes
# --------------------------------------------------------------------------- #
DOMINIOS_INTERNOS = ("@brada.social", "@somosbrada.com.br")

EMAILS_INTERNOS = {
    "marketing@brada.social", "suporte@brada.social", "inovacao@brada.social",
    "evaristo.ramalho@somosbrada.com.br", "carolina.barbosa@somosbrada.com.br",
    "diego.baptista@somosbrada.com.br",
}

# Parceiros e fornecedores com conta na plataforma. Nao sao proponentes: sao
# operacao. criape.com.br inclui a Vanessa (SUPER_ADMIN); iasmartsites.com e o
# fornecedor que construiu a plataforma.
DOMINIOS_PARCEIROS = ("criape.com.br", "iasmartsites.com")

# Dominios descartaveis usados nos cadastros de teste. Enumerados, nao inferidos:
# tentei deduzir pelo padrao do local-part (letras aleatorias + digitos, tipo
# `xahid35602`) e a regra pegou 15 PESSOAS REAIS — cmalmeida1201@gmail.com e a
# Cintia, marciopi5858@gmail.com e o Marcio. Heuristica descartada por medicao.
DOMINIOS_DESCARTAVEIS = (
    "teste.com", "teste.com.br",
    "ibtrades.com", "locawin.com", "mugadget.com", "soebing.com",
)

# Nome de pessoa ou titulo de projeto que so pode ser cadastro de teste.
_TESTE = re.compile(r"^(teste?\d*|test\d*|asd+|qwe+|xxx+|ccc+|aaa+|\.+|\d+)$", re.I)


def _dominio(email: str) -> str:
    _, _, d = (email or "").strip().lower().partition("@")
    return d


def e_interno(email: str) -> bool:
    """Mantido com este nome porque as reguas ja importam assim."""
    e = (email or "").strip().lower()
    return (not e) or e in EMAILS_INTERNOS or e.endswith(DOMINIOS_INTERNOS)


def e_texto_de_teste(texto: str) -> bool:
    """Nome de usuario ou titulo de projeto obviamente descartavel."""
    t = (texto or "").strip()
    if not t:
        return False
    if _TESTE.match(t):
        return True
    # "aaa", "ab", "..": pouca variedade de caracteres nao e nome de ninguem.
    return len(t) <= 3 or len(set(t.lower().replace(" ", ""))) <= 2


def motivo_exclusao(email: str, nome: str = "") -> str:
    """
    Devolve o motivo pelo qual esta pessoa nao recebe e-mail, ou "" se pode.

    Devolve o motivo em vez de True/False porque e o que aparece no resumo do
    dry-run: sem isso ninguem sabe POR QUE a fila encolheu.
    """
    e = (email or "").strip().lower()
    if not e:
        return "sem e-mail"
    if e_interno(e):
        return "interno"
    d = _dominio(e)
    if d in DOMINIOS_PARCEIROS:
        return "parceiro"
    if d in DOMINIOS_DESCARTAVEIS:
        return "dominio descartavel"
    if e_texto_de_teste(nome):
        return "conta de teste"
    return ""


# --------------------------------------------------------------------------- #
# 2. Login — Firebase Auth, nao Firestore
# --------------------------------------------------------------------------- #
def indice_login() -> dict:
    """uid -> timestamp do ultimo acesso em ms (ou None). Uma chamada por run."""
    # Import tardio: `sync` so tem stdlib no topo, mas nao ha razao pra pagar o
    # custo em quem nunca chama isso.
    from sync import load_auth_login_index
    return load_auth_login_index()


def dias_desde_login(uid: str, indice: dict, hoje: dt.date = None):
    """Dias desde o ultimo acesso. None = nunca acessou."""
    ms = (indice or {}).get(uid)
    if not ms:
        return None
    hoje = hoje or dt.date.today()
    return (hoje - dt.datetime.fromtimestamp(ms / 1000).date()).days


# --------------------------------------------------------------------------- #
# 3. Dedup entre reguas
# --------------------------------------------------------------------------- #
# Cada regua registra o que enviou. As chaves NAO tem o mesmo formato: a de
# expiracao e por projeto (o mesmo dono pode ter varios), as outras por pessoa.
CONTROLES_POR_PESSOA = ("regua_rascunho_envios", "regua_vitrine_envios")
CONTROLE_POR_PROJETO = "regua_expiracao_envios"

JANELA_DEDUP_DIAS = 14


def _dias_desde(ts, hoje: dt.date):
    if ts is None:
        return None
    try:
        d = ts.date() if hasattr(ts, "date") else dt.date.fromisoformat(str(ts)[:10])
    except Exception:
        return None
    return (hoje - d).days


def uids_tocados(db, dias: int = JANELA_DEDUP_DIAS, hoje: dt.date = None,
                 dono_de_projeto: dict = None) -> dict:
    """
    uid -> nome da regua que tocou a pessoa nos ultimos `dias`.

    `dono_de_projeto` (projectId -> ownerId) e necessario porque o controle da
    regua de expiracao e por PROJETO. Sem ele, quem recebeu aviso de prazo
    ontem entra numa regua nova hoje.
    """
    hoje = hoje or dt.date.today()
    tocados = {}

    for col in CONTROLES_POR_PESSOA:
        for d in db.collection(col).stream():
            x = d.to_dict() or {}
            idade = _dias_desde(x.get("enviadoEm"), hoje)
            if idade is not None and idade <= dias:
                tocados[d.id] = col

    if dono_de_projeto:
        for d in db.collection(CONTROLE_POR_PROJETO).stream():
            x = d.to_dict() or {}
            idade = _dias_desde(x.get("enviadoEm"), hoje)
            if idade is None or idade > dias:
                continue
            pid = x.get("projectId") or d.id.split("__")[0]
            dono = dono_de_projeto.get(pid)
            if dono:
                tocados.setdefault(dono, CONTROLE_POR_PROJETO)

    return tocados
