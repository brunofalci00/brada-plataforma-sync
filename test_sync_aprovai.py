import datetime
"""Testes da logica pura de sync_aprovai.py: classificacao de cobranca do
AprovAI dentro da conta Asaas compartilhada, e de teste/staff. Nenhuma chamada
de rede; so os dois filtros que decidem o que entra no dashboard."""

from sync_aprovai import is_aprovai_transaction, is_teste_ou_staff, resumo_asaas, resumo_pre_approvals


def test_aprovai_por_descricao():
    assert is_aprovai_transaction({"description": "Assinatura AprovAi Mensal - R$ 89,00"})


def test_aprovai_por_valor_89_sem_outro_produto():
    assert is_aprovai_transaction({"description": "", "value": 89})
    assert is_aprovai_transaction({"description": "", "value": "89"})


def test_nao_aprovai_assessoria_mesmo_com_valor_89():
    # Nunca visto R$89 exato na Assessoria no historico, mas a regra de
    # exclusao tem que vencer mesmo que coincida.
    assert not is_aprovai_transaction({"description": "Assessoria Brada - Esporte & Cidadania", "value": 89})


def test_nao_aprovai_nf_avulsa():
    assert not is_aprovai_transaction({"description": "NF 140", "value": 89})


def test_nao_aprovai_valor_diferente_sem_palavra_chave():
    assert not is_aprovai_transaction({"description": "Parcela 1 de 2.", "value": 102})


def test_aprovai_por_external_reference():
    assert is_aprovai_transaction({"description": "", "externalReference": "sub_aprovai_123"})


def test_item_vazio_nao_quebra():
    assert not is_aprovai_transaction(None)
    assert not is_aprovai_transaction({})


def test_teste_ou_staff_por_dominio():
    assert is_teste_ou_staff({"email": "alguem@brada.social"})
    assert is_teste_ou_staff({"email": "alguem@criape.com.br"})


def test_teste_ou_staff_por_marcador():
    assert is_teste_ou_staff({"email": "usuario.teste.novo@gmail.com"})
    assert is_teste_ou_staff({"email": "test@exemplo.com"})


def test_nao_teste_email_externo_comum():
    assert not is_teste_ou_staff({"email": "contato@marciomelom2.com"})


def test_sem_email_trata_como_nao_oficial():
    assert is_teste_ou_staff({})
    assert is_teste_ou_staff(None)


def test_resumo_asaas_exclui_refunded_e_teste():
    rows = [
        # sync_em, payment_id_hash, customer_id_hash, status, value, billing_type, date_created, due_date, e_teste_ou_staff
        ["t", "p1", "c1", "REFUNDED", 89, "CREDIT_CARD", "2026-10-02", "2026-10-02", False],
        ["t", "p2", "c2", "CONFIRMED", 89, "CREDIT_CARD", "2026-11-01", "2026-11-01", False],
        ["t", "p3", "c3", "RECEIVED", 89, "PIX", "2026-11-02", "2026-11-02", True],
    ]
    resumo = resumo_asaas(rows, datetime.date(2026, 11, 18))
    assert resumo["total_cobrancas_aprovai"] == 3
    assert resumo["assinantes_reais_ativos"] == 1  # so a confirmada e nao-teste
    assert resumo["mrr_real"] == 89
    assert resumo["cobrancas_teste_ou_staff_sucesso"] == 1


def test_assinante_e_cliente_ativo_nao_cobranca_paga():
    rows = [
        ["t", "p1", "c1", "CONFIRMED", 89, "CREDIT_CARD", "2026-11-17", "2026-11-17", False],
        ["t", "p2", "c1", "RECEIVED", 89, "CREDIT_CARD", "2026-11-18", "2026-12-17", False],
        ["t", "p3", "c2", "RECEIVED", 89, "PIX", "2026-08-01", "2026-08-01", False],  # parou de pagar
    ]
    resumo = resumo_asaas(rows, datetime.date(2026, 12, 18))
    assert resumo["assinantes_reais_ativos"] == 1 and resumo["mrr_real"] == 89


def test_resumo_pre_approvals_separa_migrado_sem_billing_de_billing_real():
    docs = [
        {"subscriptionStatus": "active", "migratedFromKiwify": True, "subscriptionExpiresAt": None},
        {"subscriptionStatus": "active", "migratedFromKiwify": True, "subscriptionExpiresAt": None},
        {"subscriptionStatus": "active", "migratedFromKiwify": None, "subscriptionExpiresAt": "2026-11-20"},
        {"subscriptionStatus": "inactive", "migratedFromKiwify": True, "subscriptionExpiresAt": None},
    ]
    resumo = resumo_pre_approvals(docs)
    assert resumo["total_ativos_no_app"] == 3
    assert resumo["migrados_kiwify_sem_billing_asaas"] == 2
    assert resumo["com_billing_asaas_real"] == 1


def test_resumo_pre_approvals_vazio_nao_quebra():
    resumo = resumo_pre_approvals([])
    assert resumo == {"total_ativos_no_app": 0, "migrados_kiwify_sem_billing_asaas": 0, "com_billing_asaas_real": 0}


def _envio_elab(lead, **marcas):
    import datetime
    import sdr_automatize as sdra
    e = {"lead_id": lead, "dia": datetime.date(2026, 10, 1), "canal": "email"}
    e.update({m: marcas.get(m, False) for m in sdra.MARCAS})
    return e


def test_funil_de_elaboracao_e_cumulativo():
    import sdr_automatize as sdra
    from sync_aprovai import completar_elaboracao, funil_elaboracao
    leads = completar_elaboracao(sdra.por_lead([
        _envio_elab("a"), _envio_elab("b", abriu=True), _envio_elab("c", respondeu=True),
        _envio_elab("d", cadastro=True)]), frozenset({"d"}))
    assert funil_elaboracao(leads) == [("Disparados", 4), ("Abriram", 3), ("Clicaram ou responderam", 2),
                                       ("Assinaram o AprovAI", 1)]


def test_lead_da_brenda_assina_pelo_utm_term_da_assinatura_ativa():
    from sync_aprovai import leads_que_assinaram, assinantes_por_origem
    docs = [{"subscriptionStatus": "active", "utm_term": "lead-1", "utm_source": "email"},
            {"subscriptionStatus": "active", "attribution": {"utm_term": "lead-2", "utm_source": "whatsapp"}},
            {"subscriptionStatus": "active", "migratedFromKiwify": True, "utm_term": "lead-3"},
            {"subscriptionStatus": "canceled", "utm_term": "lead-4"},
            {"subscriptionStatus": "active"}]
    assert leads_que_assinaram(docs) == frozenset({"lead-1", "lead-2"})
    assert dict(assinantes_por_origem(docs)) == {"email": 1, "whatsapp": 1, "Antes do rastreio (ate 23/11)": 1}


def test_cobrancas_por_situacao_ignora_teste_e_agrupa_status():
    from sync_aprovai import cobrancas_por_situacao
    linha = lambda status, teste=False: ["t", "p", "c", status, 89, "PIX", "", "", teste]  # noqa: E731
    rows = [linha("RECEIVED"), linha("CONFIRMED"), linha("REFUNDED"), linha("RECEIVED", teste=True)]
    assert cobrancas_por_situacao(rows) == [("Pagas", 2), ("Estornadas", 1)]


def test_visitas_por_dia_completa_dias_vazios():
    import datetime
    from sync_aprovai import visitas_por_dia
    funil = [["2026-11-11", 7, 1, 0, "fonte"]]
    assert visitas_por_dia(funil, datetime.date(2026, 11, 12), dias=2) == [["11/11", 7, 1], ["12/11", 0, 0]]


def test_painel_sem_banco_do_sdr_fica_so_com_o_app():
    import datetime
    from sync_aprovai import montar_painel
    resumo = {"assinantes_reais_ativos": 0, "mrr_real": 0}
    pre = {"total_ativos_no_app": 22, "migrados_kiwify_sem_billing_asaas": 17, "com_billing_asaas_real": 5}
    painel, kpis, bases = montar_painel([["2026-11-11", 7, 1, 1, "f"]], resumo, pre, [], None, None,
                                        datetime.datetime(2026, 11, 12, 9))
    titulos = [getattr(c, "titulo", "") for linha in painel.linhas for c in linha]
    assert "Site do AprovAI (periodo A)" in titulos
    assert "elaboracao_disparados" not in kpis and kpis["aprovai_visitas"] == 7
    (_, site), (_, elab) = bases
    assert site[0][1:] == [7, 1, 1, 0, 0] and elab == []  # checkout e compras zerados sem o evento


def test_cpf_valido_aceita_cpf_real_e_recusa_sequencia():
    from sync_aprovai import cpf_valido
    assert cpf_valido("529.982.247-25")
    assert not cpf_valido("1234567890")
    assert not cpf_valido("111.111.111-11")
    assert not cpf_valido("529.982.247-24")
    assert not cpf_valido(None)


def test_lead_de_teste_so_quando_cpf_preenchido_e_invalido():
    from sync_aprovai import lead_de_teste
    assert lead_de_teste({"lead": {"cpf": "1234567890"}})
    assert not lead_de_teste({"lead": {"cpf": "52998224725"}})
    assert not lead_de_teste({"lead": {}})
    assert not lead_de_teste({"lead": "texto"})


def test_funil_do_app_ignora_lead_de_teste():
    from sync_aprovai import coletar_funil_rows

    class Doc:
        def __init__(self, d):
            self.d = d

        def to_dict(self):
            return self.d

    eventos = [Doc({"type": "page_view", "timestamp": "2026-10-26T10:00:00Z"}),
               Doc({"type": "lead_captured", "timestamp": "2026-10-26T17:17:35Z", "lead": {"cpf": "1234567890"}}),
               Doc({"type": "lead_captured", "timestamp": "2026-10-27T09:00:00Z", "lead": {"cpf": "52998224725"}})]

    class Colecao:
        def stream(self):
            return iter(eventos)

    class Db:
        def collection(self, nome):
            return Colecao()

    rows = coletar_funil_rows(Db())
    assert [(r[0], r[1], r[3]) for r in rows] == [("2026-10-26", 1, 0), ("2026-10-27", 0, 1)]


def test_aba_abre_pelo_asaas_e_nao_mostra_a_brenda():
    import datetime
    from sync_aprovai import montar_painel
    resumo = {"assinantes_reais_ativos": 1, "mrr_real": 89.0}
    pre = {"total_ativos_no_app": 22, "migrados_kiwify_sem_billing_asaas": 17, "com_billing_asaas_real": 5}
    painel, kpis, _ = montar_painel([["2026-11-11", 7, 1, 0, "f"]], resumo, pre, [], None, None,
                                    datetime.datetime(2026, 11, 12, 9))
    cartoes = [c.titulo for c in painel.linhas[1]]
    assert cartoes[:2] == ["Assinantes pagando (Asaas, hoje)", "Receita recorrente (MRR, Asaas, hoje)"]
    assert painel.linhas[1][1].valor == 89.0
    titulos = [getattr(c, "titulo", "") for linha in painel.linhas for c in linha]
    assert titulos.index("Assinaturas ativas (Asaas, hoje)") < titulos.index("Site do AprovAI (periodo A)")
    assert "Assinantes: quantos pagam de fato (hoje, sem equipe e teste)" not in titulos  # funil confuso saiu (25/11)
    assert not [t for t in titulos if "Brenda" in t]
    assert kpis["aprovai_mrr"] == 89.0


def test_equipe_somosbrada_e_teste_nao_contam():
    from sync_aprovai import is_teste_ou_staff
    assert is_teste_ou_staff({"email": "fulano@somosbrada.com.br"}) and is_teste_ou_staff({"email": "x@teste.com"})
    assert not is_teste_ou_staff({"email": "cliente@gmail.com"})


def test_clique_de_login_nao_conta_como_ida_ao_checkout():
    from sync_aprovai import coletar_funil_rows, funil_site, rotulo_checkout

    class Doc:
        def __init__(self, d):
            self.d = d

        def to_dict(self):
            return self.d

    eventos = [Doc({"type": "cta_click", "ctaId": "hero_main_cta", "timestamp": "2026-11-20T10:00:00Z"}),
               Doc({"type": "cta_click", "ctaId": "login_email_submit", "timestamp": "2026-11-20T10:01:00Z"}),
               Doc({"type": "cta_click", "ctaId": "header_member_login", "timestamp": "2026-11-20T10:02:00Z"}),
               Doc({"type": "purchase_completed", "timestamp": "2026-11-20T10:05:00Z"})]

    class Db:
        def collection(self, nome):
            return type("C", (), {"stream": lambda self: iter(eventos)})()

    rows = coletar_funil_rows(Db())
    assert rows[0][2] == 1 and rows[0][6] == 1 and rows[0][5] == 0
    assert [n for n, _ in funil_site(rows)] == ["Visitas a pagina", "Clicaram em comprar", "Compraram"]
    assert rotulo_checkout(rows) == "Clique em comprar ate a compra"
    rows[0][5] = 3  # o evento do checkout chegou: ele vira o topo da taxa
    assert "Abriram o checkout" in [n for n, _ in funil_site(rows)] and rotulo_checkout(rows) == "Checkout ate a compra"


def test_resumo_assinaturas_separa_ativas_canceladas_e_estornos():
    """Caso real de 25/11: 2 ativas (17 e 22/11), 2 canceladas e estornadas em outubro; equipe fora."""
    from sync_aprovai import resumo_assinaturas
    subs = [{"status": "ACTIVE", "apagada": False, "equipe": False}, {"status": "ACTIVE", "apagada": False, "equipe": False},
            {"status": "INACTIVE", "apagada": True, "equipe": False}, {"status": "INACTIVE", "apagada": True, "equipe": False},
            {"status": "ACTIVE", "apagada": False, "equipe": True}]
    linha = lambda st, eq=False: ["t", "p", "c", st, 89.0, "CREDIT_CARD", "", "", eq]  # noqa: E731
    cobr = [linha("REFUNDED"), linha("REFUNDED"), linha("CONFIRMED"), linha("REFUNDED", True)]
    assert resumo_assinaturas(subs, cobr) == {"assinaturas_ativas": 2, "assinaturas_canceladas": 2, "estornos": 2}


def test_painel_mostra_ativas_canceladas_estornos_e_acesso_sem_pagar():
    import datetime
    from sync_aprovai import montar_painel
    resumo = {"assinantes_reais_ativos": 2, "mrr_real": 178.0}
    pre = {"total_ativos_no_app": 11, "migrados_kiwify_sem_billing_asaas": 7, "com_billing_asaas_real": 4}
    subs = [{"status": "ACTIVE", "apagada": False, "equipe": False}] * 2 + [{"status": "INACTIVE", "apagada": True, "equipe": False}]
    painel, kpis, _ = montar_painel([["2026-11-11", 7, 1, 0, "f"]], resumo, pre, [], None, None,
                                    datetime.datetime(2026, 11, 25, 9), assinaturas=subs)
    valores = {c.titulo: c.valor for linha in painel.linhas for c in linha if hasattr(c, "valor")}
    assert valores["Assinaturas ativas (Asaas, hoje)"] == 2
    assert valores["Cancelaram a recorrencia (desde o inicio)"] == 1
    assert valores["Acesso no app sem assinatura (hoje)"] == 9
    assert kpis["aprovai_canceladas"] == 1 and kpis["aprovai_estornos"] == 0


def test_checkout_conta_visitante_unico_por_dia_nao_abertura():
    """Thiago (25/11): checkout_view a cada abertura do modal, com o visitorId do page_view. A meta e visitante."""
    from sync_aprovai import coletar_funil_rows

    class Doc:
        def __init__(self, d):
            self.d = d

        def to_dict(self):
            return self.d

    eventos = [Doc({"type": "checkout_view", "visitorId": "v1", "timestamp": "2026-11-26T10:00:00Z"}),
               Doc({"type": "checkout_view", "visitorId": "v1", "timestamp": "2026-11-26T10:03:00Z"}),
               Doc({"type": "checkout_view", "visitorId": "v2", "timestamp": "2026-11-26T11:00:00Z"}),
               Doc({"type": "checkout_view", "timestamp": "2026-11-26T12:00:00Z"})]  # sem visitorId: conta 1

    class Db:
        def collection(self, nome):
            return type("C", (), {"stream": lambda self: iter(eventos)})()

    assert coletar_funil_rows(Db())[0][5] == 3


def _db_de(eventos):
    class Doc:
        def __init__(self, d):
            self.d = d

        def to_dict(self):
            return self.d

    docs = [Doc(e) for e in eventos]
    return type("Db", (), {"collection": lambda self, nome: type("C", (), {"stream": lambda s: iter(docs)})()})()


def test_planos_contratados_conta_pagante_unico_e_tira_migrado_do_kiwify():
    """Caso real de 26/11: 4 'planos' com 2 pagantes. Mesmo cliente Asaas em dois documentos (so um com a origem)
    e um migrado do Kiwify com data de expiracao, que passava no corte antigo."""
    from sync_aprovai import assinantes_por_origem, resumo_pre_approvals, leads_que_assinaram
    docs = [{"subscriptionStatus": "active", "asaasCustomerId": "cus_1", "email": "a@x.com"},
            {"subscriptionStatus": "active", "asaasCustomerId": "cus_1", "asaasSubscriptionId": "sub_1",
             "email": "a2@x.com", "utm_source": "ref:aprovai.brada.social", "utm_term": "lead-9"},
            {"subscriptionStatus": "active", "asaasCustomerId": "cus_2", "email": "b@x.com"},
            {"subscriptionStatus": "active", "migratedFromKiwify": True, "subscriptionExpiresAt": "2026-12-20",
             "email": "c@x.com"},
            {"subscriptionStatus": "active", "migratedFromKiwify": True, "email": "d@x.com"}]
    origens = dict(assinantes_por_origem(docs))
    assert origens == {"link vindo de aprovai.brada.social": 1, "Antes do rastreio (ate 23/11)": 1}
    assert sum(origens.values()) == 2
    assert resumo_pre_approvals(docs) == {"total_ativos_no_app": 5, "migrados_kiwify_sem_billing_asaas": 2,
                                          "com_billing_asaas_real": 2}
    assert leads_que_assinaram(docs) == frozenset({"lead-9"})
    assert "utm_source" not in docs[0]  # juntar duplicados nao mexe no documento original


def test_ja_tenho_uma_conta_nao_e_clique_em_comprar():
    """Auditoria de 26/11: no periodo A, 3 cliques em Garantir + 1 em 'Ja tenho uma conta' e 2 compras: 50% -> 67%."""
    from sync_aprovai import clique_de_compra, coletar_funil_rows
    assert not clique_de_compra({"ctaId": "hero_existing_account"})
    assert clique_de_compra({"ctaId": "hero_main_cta"})
    ts = "2026-11-24T15:00:00Z"
    eventos = [{"type": "cta_click", "ctaId": "hero_main_cta", "timestamp": ts}] * 3 + [
        {"type": "cta_click", "ctaId": "hero_existing_account", "timestamp": ts},
        {"type": "purchase_completed", "timestamp": ts}, {"type": "purchase_completed", "timestamp": ts}]
    (r,) = coletar_funil_rows(_db_de(eventos))
    assert r[2] == 3 and round(r[6] / r[2], 2) == 0.67


def test_visitas_contam_pessoas_no_dia_da_primeira_visita():
    from sync_aprovai import coletar_funil_rows
    eventos = [{"type": "page_view", "visitorId": "v1", "timestamp": "2026-11-20T12:00:00Z"},
               {"type": "page_view", "visitorId": "v1", "timestamp": "2026-11-20T12:05:00Z"},
               {"type": "page_view", "visitorId": "v1", "timestamp": "2026-11-21T12:00:00Z"},  # voltou: nao repete
               {"type": "page_view", "visitorId": "v2", "timestamp": "2026-11-21T13:00:00Z"},
               {"type": "page_view", "visitorId": "v2", "timestamp": "2026-11-20T13:00:00Z"}]  # ordem nao importa
    rows = coletar_funil_rows(_db_de(eventos))
    assert [(r[0], r[1]) for r in rows] == [("2026-11-20", 2), ("2026-11-21", 0)]
    assert sum(r[1] for r in rows) == 2  # 5 eventos, 2 pessoas


def test_dia_do_evento_e_pelo_horario_de_brasilia():
    import datetime
    from sync_aprovai import coletar_funil_rows, dia_brasilia
    assert dia_brasilia("2026-11-27T02:58:32.023Z") == "2026-11-26"  # 23h58 de Brasilia
    assert dia_brasilia("2026-11-27T03:00:00Z") == "2026-11-27"
    assert dia_brasilia(datetime.datetime(2026, 11, 27, 1, 0, tzinfo=datetime.timezone.utc)) == "2026-11-26"
    assert dia_brasilia("") == "" and dia_brasilia(None) == ""
    rows = coletar_funil_rows(_db_de([{"type": "purchase_completed", "timestamp": "2026-11-23T01:30:00Z"}]))
    assert rows[0][0] == "2026-11-22" and rows[0][6] == 1
