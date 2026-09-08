# -*- coding: utf-8 -*-
"""
Filtros compartilhados pelas reguas de e-mail da plataforma Brada.

Fonte unica de tres coisas que estavam espalhadas ou erradas:

  1. QUEM NUNCA RECEBE  — interno, parceiro, dominio descartavel, conta de teste.
     Em 05/08 sairam e-mails para "xxxx", "Teste", "ccc" e para criape.com.br.

  2. QUANDO A PESSOA ACESSOU  — o mais recente entre o `lastLogin` do doc em
     `users` e o `last_sign_in_timestamp` do Firebase Auth. Nenhuma das duas
     fontes serve sozinha: o Auth so mexe quando a pessoa faz LOGIN de novo
     (quem volta com a sessao viva congela nele), e o campo do doc so existe
     para quem acessou depois de a plataforma passar a grava-lo. Em 08/09/2026,
     ler so o Auth dava 21 pessoas como sumidas ha 30+ dias tendo acessado
     dentro da janela.

  3. QUEM JA FOI TOCADO  — dedup entre reguas, pra ninguem receber dois e-mails
     nossos na mesma semana.

Este modulo nao depende de nenhuma regua: sao as reguas que dependem dele.
"""
import datetime as dt
import re

# Fuso de Brasilia. Precisa bater com `sync.BRT`, e `test_login_index` trava
# isso; fica duplicado aqui de proposito para `filtros` continuar sem importar
# `sync` no topo (as reguas leem este modulo antes de qualquer credencial).
BRT = dt.timezone(dt.timedelta(hours=-3))


def hoje_brt() -> dt.date:
    """Hoje em Brasilia, nao na hora local da maquina.

    `dt.date.today()` devolve o dia do relogio do processo: BRT na minha
    maquina, UTC no runner do GitHub Actions. Como o outro lado da subtracao
    (a data do login) e convertido em BRT, o dia de referencia tambem tem que
    ser — senao a mesma pessoa tem idades diferentes conforme onde a regua roda.
    """
    return dt.datetime.now(BRT).date()

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
# 2. Acesso — Firebase Auth E `lastLogin` do Firestore, o mais recente
# --------------------------------------------------------------------------- #
def indice_login(db) -> dict:
    """uid -> timestamp do ultimo acesso em ms (ou None). Uma chamada por run.

    `db` e obrigatorio: o sinal de acesso e o mais recente entre o Auth e o
    `lastLogin` do doc em `users`, e o doc so se le com o Firestore na mao.
    Deixar o parametro opcional so serviria para alguem, sem perceber, voltar a
    decidir "sumiu" so pelo Auth — que e exatamente o defeito corrigido aqui.
    """
    # Import tardio: `sync` so tem stdlib no topo, mas nao ha razao pra pagar o
    # custo em quem nunca chama isso.
    from sync import load_login_index
    return load_login_index(db=db)


def dias_desde_login(uid: str, indice: dict, hoje: dt.date = None):
    """Dias desde o ultimo acesso. None = nunca acessou.

    O indice guarda EPOCH em milissegundos, que nao tem fuso; a data tem. A
    conversao usa BRT explicitamente, e nao `fromtimestamp` sem argumento, que
    le a hora LOCAL do processo: o mesmo instante cai num dia aqui (BRT) e
    noutro no runner do GitHub Actions (UTC). Em 08/09/2026 sao 11 pessoas cuja
    data de acesso muda de dia entre os dois fusos — perto do corte de 30 dias,
    isso e a diferenca entre receber e nao receber "voce sumiu".
    """
    ms = (indice or {}).get(uid)
    if not ms:
        return None
    hoje = hoje or hoje_brt()
    return (hoje - dt.datetime.fromtimestamp(ms / 1000, BRT).date()).days


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
        # `astimezone(BRT)` e nao `.date()` cru: o `enviadoEm` e um timestamp
        # do servidor em UTC, e um envio das 22h BRT cai no dia seguinte em UTC.
        if isinstance(ts, dt.datetime):
            # Naive lido como UTC, mesma convencao de `sync._ts_ms` e
            # `sync.to_date`. `astimezone` sozinho assume a hora LOCAL do
            # processo quando o datetime nao tem fuso, que e o vies que esta
            # linha veio corrigir: sem o `replace`, o mesmo `enviadoEm` sem
            # tzinfo daria idades diferentes aqui (BRT) e no runner (UTC).
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=dt.timezone.utc)
            d = ts.astimezone(BRT).date()
        else:
            d = dt.date.fromisoformat(str(ts)[:10])
    except Exception:
        return None
    return (hoje - d).days


def registrar_supressao(db, colecao: str, uid: str):
    """
    Grava o descadastro na colecao de controle da regua.

    Sem isto, quem pede descadastro NUNCA sai da fila: nao entra na colecao de
    controle (porque nunca recebeu nada), entao aparece em toda execucao e e
    reconsultado no LeadLovers de novo. A regua da vitrine tinha 49 pessoas
    nessa situacao — com cron semanal viraria 49 chamadas de API por semana,
    para sempre, sem nenhum efeito.

    O documento vai de proposito SEM `enviadoEm`: `uids_tocados` so conta quem
    tem essa data, entao o dedup entre reguas continua enxergando so quem
    realmente recebeu e-mail.
    """
    from google.cloud import firestore
    db.collection(colecao).document(uid).set({
        "suprimido": True,
        "checadoEm": firestore.SERVER_TIMESTAMP,
    })


def motivo_ja_resolvido(registro: dict) -> str:
    """Por que esta pessoa ja saiu da fila: envio anterior ou descadastro."""
    return "descadastrado (registrado)" if (registro or {}).get("suprimido") else "ja enviado"


def exigir_checagem_supressao(args):
    """
    Barra `--apply` sem checagem de descadastro nas reguas de campanha.

    A spec ja dizia que a checagem e obrigatoria, mas era regra escrita, nao
    regra imposta. O modo de falha e concreto: a fila continua mostrando quem
    pediu descadastro (eles nunca entram na colecao de controle, porque nunca
    receberam), entao um `--apply` sem a flag manda e-mail para exatamente as
    pessoas que optaram por sair. Alem de violar a LGPD, a reclamacao de spam
    cai no `noreply@brada.social`, que e o remetente da verificacao de e-mail
    dos cadastros novos.

    `--sem-checagem` existe como saida de emergencia consciente (ex.: API do
    LeadLovers fora do ar e disparo que nao pode esperar), mas obriga a
    escrever isso na linha de comando.
    """
    if getattr(args, "apply", False) and not getattr(args, "checar_supressao", False) \
            and not getattr(args, "sem_checagem", False):
        import sys as _sys
        _sys.exit(
            "ERRO: --apply exige --checar-supressao.\n"
            "  Sem ela, a fila inclui quem pediu descadastro e o disparo vira\n"
            "  violacao de LGPD no remetente que envia a verificacao de cadastro.\n"
            "  Se for mesmo intencional, use --sem-checagem."
        )


def uids_tocados(db, dias: int = JANELA_DEDUP_DIAS, hoje: dt.date = None,
                 dono_de_projeto: dict = None) -> dict:
    """
    uid -> nome da regua que tocou a pessoa nos ultimos `dias`.

    `dono_de_projeto` (projectId -> ownerId) e necessario porque o controle da
    regua de expiracao e por PROJETO. Sem ele, quem recebeu aviso de prazo
    ontem entra numa regua nova hoje.
    """
    hoje = hoje or hoje_brt()
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
