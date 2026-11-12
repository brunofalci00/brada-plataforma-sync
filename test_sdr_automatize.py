import datetime

import sdr_automatize as sdra

D = datetime.date


def _envio(lead, dia, canal="email", **marcas):
    e = {"lead_id": lead, "dia": dia, "canal": canal}
    e.update({m: marcas.get(m, False) for m in sdra.MARCAS})
    return e


def test_por_lead_junta_envios_e_guarda_primeiro_disparo():
    leads = sdra.por_lead([_envio("a", D(2026, 11, 5), clicou=True),
                           _envio("a", D(2026, 11, 3), canal="whatsapp", respondeu=True),
                           _envio("b", D(2026, 11, 4))])
    a = leads["a"]
    assert a["dia"] == D(2026, 11, 3) and a["canais"] == {"email", "whatsapp"}
    assert a["clicou"] and a["respondeu"] and not leads["b"]["clicou"]


def test_por_semana_separa_email_e_whatsapp():
    envios = [_envio("a", D(2026, 11, 10)), _envio("b", D(2026, 11, 11), "whatsapp"), _envio("c", D(2026, 11, 2))]
    assert sdra.por_semana(envios, D(2026, 11, 12), n=2) == [["02/11", 1, 0], ["09/11", 1, 1]]


def test_por_dia_preenche_dias_sem_envio_com_zero():
    linhas = sdra.por_dia([_envio("a", D(2026, 11, 12))], D(2026, 11, 12), n=3)
    assert linhas == [["10/11", 0, 0], ["11/11", 0, 0], ["12/11", 1, 0]]


def test_ultimo_disparo():
    assert sdra.ultimo_disparo([_envio("a", D(2026, 10, 28)), _envio("b", None)]) == D(2026, 10, 28)
    assert sdra.ultimo_disparo([]) is None


def test_coorte_conta_por_semana_do_primeiro_disparo():
    leads = {"a": {"dia": D(2026, 11, 10), "x": True}, "b": {"dia": D(2026, 11, 11), "x": False},
             "c": {"dia": D(2026, 11, 3), "x": True}}
    linhas = sdra.coorte(leads, D(2026, 11, 12), ["x"], n=2)
    assert linhas == [["02/11", 1, 1, 1.0], ["09/11", 2, 1, 0.5]]


class _Cursor:
    def __init__(self, linhas):
        self.linhas = linhas

    def execute(self, *a):
        pass

    def fetchall(self):
        return self.linhas


class _Conn:
    def __init__(self, linhas):
        self.linhas = linhas

    def cursor(self):
        return _Cursor(self.linhas)


def test_fila_do_dia_separa_enviados_e_espera_com_canal_mais_comum():
    fila = sdra.fila_do_dia(_Conn([("elaboracao", "fila", "whatsapp", 230), ("elaboracao", "adiado", "email", 9),
                                   ("incentivador", "enviado", "email", 8)]))
    assert fila["elaboracao"] == {"enviados": 0, "na_fila": 239, "canal_fila": "whatsapp"}
    assert fila["incentivador"] == {"enviados": 8, "na_fila": 0, "canal_fila": ""}
