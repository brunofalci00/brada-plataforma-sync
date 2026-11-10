import sync_visao_geral as vg


def _grade(titulo, secoes):
    g = [[titulo], ["Foto: ..."]]
    for nome, valores in secoes:
        g += [[nome], [v for x in valores for v in (x, "")], ["rotulo"] * len(valores), []]
    return g


GRADES = {
    "Plataforma Brada": _grade("Plataforma Brada - Aquisicao", [
        ("Plataforma - Estoque e prazo de captacao", [771, 389, 47, 18]),
        ("Novos proponentes - Funil de pessoas", [153, 60, 56, 56]),
        ("Cadastros e uso - Publicos", [6, 44, 165, 69]),
    ]),
    "Comunidade": _grade("Comunidade Brada", [
        ("Visitas nos ultimos 30 dias (Google Analytics)", [403, 73, 0.181, 1588]),
        ("Membros", [127, 56, 0.97, 32]),
        ("Uso", [50, 125, 0.05, 72]),
    ]),
    "SDR Incentivador": _grade("SDR", [
        ("Volume por canal (todo o historico)", [119, 656, 0.15, 775]),
        ("Quem chega em Reuniao Agendada ou alem", [150, 1, 146, 0.008]),
    ]),
    "AprovAI": _grade("AprovAI", [
        ("Funil da landing", [120, 2, 1, 0]),
        ("Conversao e assinantes (Asaas)", [0, 0, 0, 0]),
    ]),
    "Primeira Fila": _grade("Primeira Fila", [
        ("Espacos anunciados", [31, 28, 1]),
        ("Procura dos organizadores", [759, 718, 1, 0]),
        ("Reservas e dinheiro", [0, 0, 0, 0]),
    ]),
}


def test_extrai_por_secao_e_posicao():
    k = vg.extrair(GRADES)
    assert (k["aptos_hoje"], k["prazo_vencido"], k["acessos_plat_30d"]) == (389, 47, 69)
    assert (k["visitantes_com"], k["retorno_com"], k["membros_com"]) == (403, 0.181, 127)
    assert (k["reunioes_inc"], k["reunioes_automatize"], k["cliques_pf_30d"]) == (150, 1, 718)
    assert all(v is not None for v in k.values())


def test_aba_ausente_vira_traco_sem_quebrar():
    grades = {a: g for a, g in GRADES.items() if a != "AprovAI"}
    k = vg.extrair(grades)
    assert k["assinantes_aprovai"] is None
    secoes = vg.montar_secoes(k)
    assert [len(s.metricas) for s in secoes] == [4] * 6
    assert secoes[-1].metricas[0][0] == "-"


def test_frases_acendem_alertas_certos():
    f = vg.frases(vg.extrair(GRADES))
    assert f["retorno"][1] and "18%" in f["retorno"][0]
    assert f["conexao"][1] and "falta conversão" in f["conexao"][0]
    assert f["receita"][1] and f["receita"][0].startswith("Os produtos digitais ainda não")
    assert "389 projetos" in f["manchete"][0]


def test_numero_aceita_texto_formatado():
    assert vg._numero("2.624") == 2624
    assert abs(vg._numero("18,1%") - 0.181) < 1e-9
    assert vg._numero("R$1.234,50") == 1234.5
    assert vg._numero("abc") is None
