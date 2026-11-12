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
    resumo = resumo_asaas(rows)
    assert resumo["total_cobrancas_aprovai"] == 3
    assert resumo["assinantes_reais_ativos"] == 1  # so a confirmada e nao-teste
    assert resumo["mrr_real"] == 89
    assert resumo["cobrancas_teste_ou_staff_sucesso"] == 1


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
