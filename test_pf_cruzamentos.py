import pf_cruzamentos as pc


def test_cruzar_local_marca_busca_sem_espaco_e_apelido_de_cidade():
    oferta = [("Rio de Janeiro", "Laranjeiras", 11), ("Rio de Janeiro", "Centro", 8)]
    consultas = [("espaco para festa rj", 40, 2), ("espaço para evento tijuca", 12, 0), ("dinastia hub", 30, 5)]
    linhas = {l[0]: l for l in pc.cruzar_local(oferta, consultas)}
    assert linhas["Rio de Janeiro"][2:5] == [40, 2, 19]
    assert linhas["Rio de Janeiro"][5] == "Busca e oferta"
    assert linhas["Tijuca"][5] == "Busca sem espaco no guia"
    assert linhas["Laranjeiras"][5] == "Espaco sem busca visivel"
    assert "Pinheiros" not in linhas  # sem busca e sem espaco nao entra


def test_normal_tira_acento_e_caixa():
    assert pc.normal("  Niterói ") == "niteroi"
