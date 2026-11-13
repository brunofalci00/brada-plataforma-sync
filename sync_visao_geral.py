"""Aba "Visao Geral": o ecossistema Brada em uma tela.

Roda por ultimo no CI. Nao consulta fonte: le a aba escondida `dados_resumo`, que
cada sync de canal acabou de gravar (chave -> valor). Chave ausente vira 0 e sai
no log; a aba nao quebra.

Enxuta desde a fase 3: 4 cartoes e uma linha por produto. O detalhe dos disparos
(capacidade, fila, taxas por canal e frente) mora na aba "Brenda".
"""

import datetime

import sync
import dashboard_painel as dp

ABA = "Visão Geral"

# (produto, etapas, chave do comeco, do meio, do fim)
PRODUTOS = [
    ("SDR Incentivador", "disparo > card > reuniao",
     "incentivador_disparados", "incentivador_cards", "incentivador_reuniao"),
    ("SDR Elaboracao", "disparo > cadastro > assinante",
     "elaboracao_disparados", "elaboracao_cadastros", "aprovai_assinantes"),
    ("SDR Aptos", "disparo > cadastro > apto",
     "aptos_sdr_disparados", "aptos_sdr_cadastros", "aptos_sdr_aptos"),
    ("Plataforma (novos)", "cadastro > apto",
     "plataforma_novos_cadastros", None, "plataforma_novos_aptos"),
    ("Comunidade", "visita > membro > acao",
     "comunidade_visitantes_30d", "comunidade_membros", "comunidade_primeira_acao"),
    ("AprovAI (app)", "visita > lead > assinante", "aprovai_visitas", "aprovai_leads", "aprovai_assinantes"),
    ("Primeira Fila", "clique > pedido > paga", "pf_cliques_total", "pf_pedidos", "pf_reservas_pagas"),
]
SDRS = [("Incentivador", "incentivador_disparados", "incentivador_cards"),
        ("Elaboracao", "elaboracao_disparados", "elaboracao_cadastros"),
        ("Aptos", "aptos_sdr_disparados", "aptos_sdr_cadastros")]


def valor(r, chave, faltando):
    if chave is None:
        return ""
    if chave not in r:
        faltando.append(chave)
        return 0
    return dp._numero(r[chave])


def montar_painel(r, agora):
    faltando = []
    v = lambda k: valor(r, k, faltando)  # noqa: E731
    p = dp.Painel(ABA, "Ecossistema Brada · visao geral",
                  f"Atualizado {agora.strftime('%d/%m/%Y %H:%M')} · numeros das abas de cada produto"
                  " · disparos da Automatize em detalhe na aba Brenda")
    p.linha(dp.Cartao("Projetos aptos a captar", v("plataforma_aptos")),
            dp.Cartao("Membros da Comunidade", v("comunidade_membros")),
            dp.Cartao("Incentivadores em reuniao+ (pipeline todo)", v("incentivador_pipeline_reuniao")),
            dp.Cartao("Receita digital", v("aprovai_mrr") + v("pf_valor_pago"), dp.FMT_BRL))
    linhas = []
    for nome, etapas, comeco, meio, fim in PRODUTOS:
        c, m, f = v(comeco), v(meio), v(fim)
        linhas.append([nome, etapas, c, m, f, dp.taxa(f, c)])
    p.linha(dp.Tabela("Funil de cada produto", ["Produto", "Etapas", "Comeco", "Meio", "Fim", "Fim / comeco"],
                      linhas, formatos=[None, None, dp.FMT_INT, dp.FMT_INT, dp.FMT_INT, dp.FMT_PCT],
                      gradiente=[5]))
    return p, sorted(set(faltando))


def main():
    agora = datetime.datetime.now(sync.BRT)
    sh = sync.get_sheets_client().open_by_key(sync.SPREADSHEET_ID)
    svc = sync.build_sheets_service()
    resumo = dp.ler_resumo(svc, sync.SPREADSHEET_ID)
    painel, faltando = montar_painel(resumo, agora)
    if faltando:
        print(f"[AVISO] chaves ausentes no resumo (sairam como 0): {faltando}")
    painel.publicar(sh, svc, sync.SPREADSHEET_ID, indice=0)
    print("Visao Geral: aba publicada.")


if __name__ == "__main__":
    main()
