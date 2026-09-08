# -*- coding: utf-8 -*-
"""
O sinal de acesso: o mais recente entre o `lastLogin` do doc e o Auth.

Toda fonte aqui e SINTETICA e escrita no proprio teste. Nada consulta o
Firestore nem o Auth, e nenhuma assercao conta a populacao de hoje: um teste
que dissesse "sao 21 pessoas" reprovaria assim que alguem melhorasse o codigo,
e um que copiasse o comportamento atual cimentaria o defeito.
"""
import collections
import datetime as dt
import types

import pytest

import filtros
import sync

UTC = dt.timezone.utc
BRT = sync.BRT


def _idx(**uid_para_ms):
    """Indice de login sintetico: uid -> epoch ms."""
    return dict(uid_para_ms)


def _users(pares):
    """Colecao `users` sintetica: [(uid, doc), ...]."""
    return [(uid, doc) for uid, doc in pares]


def _fake_auth(monkeypatch, indice):
    """Troca a leitura do Firebase Auth por um dicionario escrito no teste."""
    monkeypatch.setattr(sync, "load_auth_login_index", lambda: dict(indice))


# --------------------------------------------------------------------------- #
# 1. O merge
# --------------------------------------------------------------------------- #
def test_doc_mais_novo_que_o_auth_vence(monkeypatch):
    """O caso que motivou a mudanca: sessao viva nao mexe no Auth.

    Quem fica logada volta sem autenticar de novo, entao o Auth congela na
    ultima senha digitada enquanto o doc acompanha o acesso.
    """
    auth = int(dt.datetime(2026, 8, 28, 12, 0, tzinfo=UTC).timestamp() * 1000)
    doc = dt.datetime(2026, 9, 8, 12, 50, tzinfo=UTC)
    _fake_auth(monkeypatch, {"u1": auth})

    idx = sync.load_login_index(users_raw=_users([("u1", {"lastLogin": doc})]))

    assert idx["u1"] == int(doc.timestamp() * 1000)
    hoje = dt.date(2026, 9, 8)
    assert filtros.dias_desde_login("u1", idx, hoje) == 0
    # Antes, o mesmo dado dava 11 dias so porque o Auth nao tinha se mexido.
    assert filtros.dias_desde_login("u1", _idx(u1=auth), hoje) == 11


def test_auth_mais_novo_que_o_doc_vence(monkeypatch):
    """O merge e o MAXIMO, nao "o doc quando existir".

    Quem sai, expira a sessao e faz login de novo tem o Auth mais novo. Trocar
    o maximo por um fallback do doc envelheceria essa pessoa de volta.
    """
    doc = dt.datetime(2026, 7, 1, 9, 0, tzinfo=UTC)
    auth = int(dt.datetime(2026, 9, 5, 9, 0, tzinfo=UTC).timestamp() * 1000)
    _fake_auth(monkeypatch, {"u1": auth})

    idx = sync.load_login_index(users_raw=_users([("u1", {"lastLogin": doc})]))

    assert idx["u1"] == auth
    assert filtros.dias_desde_login("u1", idx, dt.date(2026, 9, 8)) == 3


def test_sem_nenhuma_das_duas_fontes_continua_nunca_acessou(monkeypatch):
    """Ausencia dupla segue None, e None segue contando como sumido.

    Esta e a regra que NAO podia mudar: as reguas leem `None` como
    "nunca acessou" e mandam e-mail. Se o merge transformasse ausencia em
    "acessou agora", a fila inteira dos frios sumiria em silencio.
    """
    _fake_auth(monkeypatch, {"u1": None})

    idx = sync.load_login_index(users_raw=_users([
        ("u1", {"email": "x"}),        # existe no Auth, sem nenhum login
        ("u2", {"lastLogin": None}),   # campo presente e vazio
    ]))

    assert idx.get("u1") is None
    assert idx.get("u2") is None
    for uid in ("u1", "u2", "u_que_nao_existe"):
        assert filtros.dias_desde_login(uid, idx, dt.date(2026, 9, 8)) is None


def test_doc_sem_lastlogin_nao_apaga_o_auth(monkeypatch):
    """Quem so tem Auth continua com Auth. O merge so acrescenta."""
    auth = int(dt.datetime(2026, 9, 1, 15, 0, tzinfo=UTC).timestamp() * 1000)
    _fake_auth(monkeypatch, {"u1": auth})

    idx = sync.load_login_index(users_raw=_users([("u1", {})]))

    assert idx["u1"] == auth


def test_lastlogin_ilegivel_nao_derruba_o_indice(monkeypatch):
    """Campo com lixo degrada para o Auth em vez de estourar a regua."""
    auth = int(dt.datetime(2026, 9, 1, 15, 0, tzinfo=UTC).timestamp() * 1000)
    _fake_auth(monkeypatch, {"u1": auth, "u2": None})

    idx = sync.load_login_index(users_raw=_users([
        ("u1", {"lastLogin": "ontem de tarde"}),
        ("u2", {"lastLogin": "ontem de tarde"}),
    ]))

    assert idx["u1"] == auth
    assert idx.get("u2") is None


def test_indice_exige_uma_fonte():
    """Sem `db` e sem `users_raw`, o indice nao pode virar Auth puro calado."""
    with pytest.raises(ValueError):
        sync.load_login_index()


def test_indice_aceita_o_mapa_uid_doc_que_o_outreach_ja_tem(monkeypatch):
    """`sync_outreach_frios` guarda os users num dict; nao precisa converter."""
    doc = dt.datetime(2026, 9, 8, 12, 0, tzinfo=UTC)
    _fake_auth(monkeypatch, {"u1": None})

    idx = sync.load_login_index(users_raw={"u1": {"lastLogin": doc}})

    assert idx["u1"] == int(doc.timestamp() * 1000)


# --------------------------------------------------------------------------- #
# 2. Fuso
# --------------------------------------------------------------------------- #
def test_fuso_do_indice_nao_depende_do_relogio_da_maquina():
    """Mesmo instante, quatro formas de escrever: o epoch tem que ser um so.

    Se alguem trocar a normalizacao por `.timestamp()` num datetime sem fuso, o
    naive passa a ser lido como hora LOCAL e as quatro deixam de bater — o
    resultado do teste passaria a depender de onde ele roda.
    """
    instante = dt.datetime(2026, 9, 8, 2, 30, tzinfo=UTC)
    esperado = int(instante.timestamp() * 1000)

    assert sync._ts_ms(instante) == esperado
    assert sync._ts_ms(instante.astimezone(BRT)) == esperado        # 07/09 23:30 em BRT
    assert sync._ts_ms(instante.replace(tzinfo=None)) == esperado   # naive = UTC, por contrato
    assert sync._ts_ms("2026-09-08T02:30:00") == esperado


def test_data_do_acesso_e_lida_em_brt_e_nao_em_utc():
    """Um acesso das 23h30 BRT ja e o dia seguinte em UTC.

    Lido em BRT sao 0 dias; lido em UTC seriam -1, porque o acesso "aconteceu
    amanha". Sem a normalizacao, a idade de todo mundo que acessa de noite
    anda um dia conforme o fuso do processo.
    """
    acesso_utc = dt.datetime(2026, 9, 9, 2, 30, tzinfo=UTC)   # 08/09 23:30 em BRT
    ms = int(acesso_utc.timestamp() * 1000)
    hoje = dt.date(2026, 9, 8)

    assert filtros.dias_desde_login("u1", _idx(u1=ms), hoje) == 0
    assert (hoje - acesso_utc.date()).days == -1   # a leitura ingenua em UTC


def test_corte_de_inatividade_nao_muda_de_lado_por_causa_do_fuso():
    """A pessoa no limite tem que cair do mesmo lado onde quer que a regua rode.

    22h de Brasilia do dia 09/08 e 01h UTC do dia 10/08. Em BRT sao 30 dias
    completos (sumida, pelo corte da vitrine); em UTC seriam 29 (ativa).
    """
    acesso_utc = dt.datetime(2026, 8, 10, 1, 0, tzinfo=UTC)   # 09/08 22:00 em BRT
    ms = int(acesso_utc.timestamp() * 1000)
    hoje = dt.date(2026, 9, 8)
    corte = 30

    dias = filtros.dias_desde_login("u1", _idx(u1=ms), hoje)
    assert dias == 30 and dias >= corte              # BRT: sumida
    assert (hoje - acesso_utc.date()).days == 29     # UTC: ativa, outro veredito


class _ExigeFuso(dt.datetime):
    """`datetime` que recusa `fromtimestamp` e `today` sem fuso declarado.

    Existe porque o defeito de fuso e invisivel na maquina de quem escreve o
    teste: aqui o relogio do processo JA e BRT, entao a versao errada
    (`fromtimestamp(ms)` sem tz) da a mesma resposta e o teste passaria. Esta
    classe testa a chamada em vez do resultado, e por isso reprova em qualquer
    maquina, inclusive no runner do GitHub Actions, que roda em UTC.
    """

    @classmethod
    def fromtimestamp(cls, ts, tz=None):
        if tz is None:
            raise AssertionError("fromtimestamp sem fuso le a hora local do processo")
        return dt.datetime.fromtimestamp(ts, tz)

    @classmethod
    def today(cls):
        raise AssertionError("datetime.today() le a hora local do processo")


class _DataExigeFuso(dt.date):
    """`date` que recusa `today()`.

    Precisa existir separado de `_ExigeFuso`: o defeito original era
    `dt.date.today()`, e bloquear so o `datetime.today()` deixava a versao
    errada passar verde. `fromisoformat` continua funcionando (a subclasse
    herda), que e o unico outro uso de `dt.date` dentro de `filtros`.
    """

    @classmethod
    def today(cls):
        raise AssertionError("date.today() le a hora local do processo")


def _sem_relogio_local(monkeypatch):
    """Troca o `dt` de `filtros` por um que so aceita conversao com fuso."""
    falso = types.SimpleNamespace(
        datetime=_ExigeFuso, date=_DataExigeFuso,
        timedelta=dt.timedelta, timezone=dt.timezone)
    monkeypatch.setattr(filtros, "dt", falso)


def test_o_guarda_de_fuso_bloqueia_os_dois_relogios_locais(monkeypatch):
    """Meta-teste: sem isto o guarda apodrece calado.

    `_sem_relogio_local` so vale enquanto barrar as DUAS portas de saida para o
    relogio do processo. Ja passou verde com uma delas aberta.
    """
    _sem_relogio_local(monkeypatch)
    for chamada in (lambda: filtros.dt.date.today(),
                    lambda: filtros.dt.datetime.today(),
                    lambda: filtros.dt.datetime.fromtimestamp(0)):
        with pytest.raises(AssertionError):
            chamada()
    # o que e legitimo continua funcionando
    assert filtros.dt.date.fromisoformat("2026-09-08") == dt.date(2026, 9, 8)
    assert filtros.dt.datetime.fromtimestamp(0, UTC).year == 1970


def test_conversao_do_login_declara_o_fuso(monkeypatch):
    """A idade do acesso nao pode sair do relogio de quem roda a regua."""
    _sem_relogio_local(monkeypatch)
    ms = int(dt.datetime(2026, 9, 9, 2, 30, tzinfo=UTC).timestamp() * 1000)

    assert filtros.dias_desde_login("u1", _idx(u1=ms), dt.date(2026, 9, 8)) == 0


def test_hoje_brt_declara_o_fuso(monkeypatch):
    """O dia de referencia tambem: BRT dos dois lados da subtracao, ou nenhum."""
    _sem_relogio_local(monkeypatch)

    assert filtros.hoje_brt() == dt.datetime.now(BRT).date()


def test_fuso_de_filtros_e_o_mesmo_de_sync():
    """A constante esta escrita nos dois modulos; ela nao pode divergir."""
    assert filtros.BRT.utcoffset(None) == sync.BRT.utcoffset(None)


def test_hoje_brt_nao_e_o_dia_do_relogio_do_processo():
    """`hoje_brt` sai de um datetime com fuso, nao de `date.today()`."""
    hoje = filtros.hoje_brt()
    assert isinstance(hoje, dt.date)
    assert hoje == dt.datetime.now(filtros.BRT).date()


def test_dedup_mede_a_idade_do_envio_em_brt():
    """`enviadoEm` e timestamp de servidor em UTC; um envio das 22h BRT cai no
    dia seguinte em UTC e pareceria um dia mais novo do que e."""
    enviado = dt.datetime(2026, 9, 9, 1, 0, tzinfo=UTC)   # 08/09 22:00 em BRT
    assert filtros._dias_desde(enviado, dt.date(2026, 9, 9)) == 1
    assert filtros._dias_desde(enviado, dt.date(2026, 9, 8)) == 0


# --------------------------------------------------------------------------- #
# 3. Efeito nas colunas de `raw_users` (dashboard de KPIs)
# --------------------------------------------------------------------------- #
def _linha_de_users(users_raw, login_index):
    """Primeira linha de `build_users` como {coluna: valor}."""
    issues = collections.defaultdict(collections.Counter)
    rows = sync.build_users(users_raw, login_index, {}, issues)
    return dict(zip(sync.HEADER_USERS, rows[0]))


def test_raw_users_conta_acesso_que_so_o_doc_conhece(monkeypatch):
    """`ativo_30d` e `data_ultimo_login` seguem o indice unificado.

    Sao as colunas por tras do "Acesso em 30d": lendo so o Auth, a tela
    subconta acesso e o dashboard mostra a base mais parada do que ela esta.
    """
    agora = dt.datetime.now(UTC)
    auth = int((agora - dt.timedelta(days=45)).timestamp() * 1000)
    doc = agora - dt.timedelta(days=2)
    _fake_auth(monkeypatch, {"u1": auth})

    users_raw = _users([("u1", {"role": "ONG", "lastLogin": doc})])
    depois = _linha_de_users(users_raw, sync.load_login_index(users_raw=users_raw))
    antes = _linha_de_users(users_raw, {"u1": auth})

    assert antes["ativo_30d"] == "nao"
    assert depois["ativo_30d"] == "sim"
    assert antes["logou_alguma_vez"] == depois["logou_alguma_vez"] == "sim"
    assert depois["data_ultimo_login"] == doc.astimezone(BRT).strftime("%Y-%m-%d")


def test_quem_nunca_acessou_continua_fora_do_ativo_30d(monkeypatch):
    """O merge nao inventa acesso para quem nao tem nenhuma das duas fontes."""
    _fake_auth(monkeypatch, {"u1": None})
    users_raw = _users([("u1", {"role": "ONG"})])

    linha = _linha_de_users(users_raw, sync.load_login_index(users_raw=users_raw))

    assert linha["logou_alguma_vez"] == "nao"
    assert linha["ativo_30d"] == "nao"
    assert linha["data_ultimo_login"] == ""
