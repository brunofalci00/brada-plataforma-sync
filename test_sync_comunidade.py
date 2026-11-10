import datetime

import sync_comunidade as sc

AGORA = int(datetime.datetime(2026, 11, 10, 12, tzinfo=datetime.timezone.utc).timestamp() * 1000)
DIA = sc.DIA_MS


def _dados(**extra):
    base = {
        "users": [
            {"role": "explorador", "onboardingCompleted": True, "createdAt": "2026-11-02T10:00:00Z"},
            {"role": "idealizador", "onboardingCompleted": False, "createdAt": "2026-10-20T10:00:00Z"},
            {"role": "explorador", "isBradaTeam": True, "createdAt": "2026-11-01"},
            {"role": "idealizador", "email": "Fulano@Brada.social", "createdAt": "2026-11-01"},
            {"role": "admin", "createdAt": "2026-11-01"},
        ],
        "auth": [
            {"email": "a@x.com", "createdAt": AGORA - 20 * DIA, "lastLoginAt": AGORA - 2 * DIA},
            {"email": "b@x.com", "createdAt": AGORA - 10 * DIA, "lastLoginAt": AGORA - 10 * DIA},
            {"email": "c@brada.social", "createdAt": AGORA - 30 * DIA, "lastLoginAt": AGORA},
        ],
        "activation_events": [
            {"event": "first_relevant_action", "actionType": "event_external_click", "timestamp": "2026-11-03T01:00:00Z"},
            {"event": "profile_completed", "timestamp": "2026-11-03T02:00:00Z"},
        ],
        "community_posts": [], "user_connections": [{"status": "accepted"}, {"status": "pending"}],
        "articles": [{"status": "published", "views": 10}, {"status": "pending_approval"}],
        "courses": [{"enrolledUsersCount": "5"}, {"enrolledUsersCount": "0"}],
        "opportunities": [{"status": "ABERTO"}, {"status": "breve"}], "events": [{}], "direct_messages": [],
    }
    base.update(extra)
    return base


def test_equipe_fica_fora_de_membros_e_logins():
    m = sc.calcular(_dados(), AGORA)
    assert (m["membros"], m["equipe"]) == (2, 3)
    assert m["login_7d"] == 1 and m["login_30d"] == 2


def test_taxas_e_contagens():
    m = sc.calcular(_dados(), AGORA)
    assert m["onboarding"] == 0.5
    assert m["voltaram_7d"] == 0.5  # 2 elegiveis, 1 voltou 18 dias depois
    assert m["cadastros_mes"] == 1
    assert (m["editais_abertos"], m["editais_breve"]) == (1, 1)
    assert (m["inscritos_cursos"], m["cursos_com_inscritos"]) == (5, 1)
    assert m["acao_evento"] == 1


def test_base_vazia_nao_divide_por_zero():
    vazio = {k: [] for k in _dados()}
    m = sc.calcular(vazio, AGORA)
    assert m["onboarding"] == 0 and m["voltaram_7d"] == 0


def test_secoes_e_diario_sem_equipe():
    d = _dados()
    assert [len(s.metricas) for s in sc.montar_secoes(sc.calcular(d, AGORA))] == [4] * 6
    linhas = sc.diario(d)
    assert ["2026-11-03", 0, 1, 1] in linhas
    assert not any(l[0] == "2026-11-01" for l in linhas)
