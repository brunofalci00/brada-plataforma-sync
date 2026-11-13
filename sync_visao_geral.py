"""Aba "Visao Geral": o ecossistema Brada em uma tela.

Roda por ultimo no CI. Nao consulta fonte: le a aba escondida `dados_resumo`, que
cada sync de canal acabou de gravar (chave -> valor). Chave ausente vira 0 e sai
no log; a aba nao quebra.

O centro da tela sao os tres SDRs da Automatize lado a lado (do disparo ao
resultado de cada canal) e uma tabela com o funil de cada produto. Logo abaixo,
como a mesma IA (Brenda) divide os disparos entre as tres frentes e o que esta
esperando na fila do alocador hoje (le o banco do SDR; se falhar, o bloco sai).
"""

import datetime

import sync
import dashboard_painel as dp
import explorar_disparos
import sdr_automatize as sdr

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


def montar_painel(r, agora, disparos=()):
    faltando = []
    v = lambda k: valor(r, k, faltando)  # noqa: E731
    p = dp.Painel(ABA, "Ecossistema Brada · visao geral",
                  f"Atualizado {agora.strftime('%d/%m/%Y %H:%M')} · numeros das abas de cada produto")
    p.linha(dp.Cartao("Projetos aptos a captar", v("plataforma_aptos")),
            dp.Cartao("Membros da Comunidade", v("comunidade_membros")),
            dp.Cartao("Incentivadores em reuniao ou alem", v("incentivador_pipeline_reuniao")),
            dp.Cartao("Receita digital", v("aprovai_mrr") + v("pf_valor_pago"), dp.FMT_BRL))
    p.linha(dp.Serie("Leads disparados pela Automatize", ["SDR", "Leads"],
                     [[nome, v(topo)] for nome, topo, _ in SDRS], tipo="BAR",
                     subtitulo="Desde o inicio de cada canal"),
            dp.Serie("Disparo ate o primeiro resultado", ["SDR", "Conversao"],
                     [[nome, dp.taxa(v(meio), v(topo))] for nome, topo, meio in SDRS], formato=dp.FMT_PCT,
                     subtitulo="Incentivador: card no HubSpot · demais: cadastro na plataforma"))
    for componentes in disparos:
        p.linha(*componentes)
    linhas = []
    for nome, etapas, comeco, meio, fim in PRODUTOS:
        c, m, f = v(comeco), v(meio), v(fim)
        linhas.append([nome, etapas, c, m, f, dp.taxa(f, c)])
    p.linha(dp.Tabela("Funil de cada produto", ["Produto", "Etapas", "Comeco", "Meio", "Fim", "Fim / comeco"],
                      linhas, formatos=[None, None, dp.FMT_INT, dp.FMT_INT, dp.FMT_INT, dp.FMT_PCT],
                      gradiente=[5]))
    return p, sorted(set(faltando))


def disparos_da_brenda(hoje):
    try:
        conn = sdr.conectar()
        try:
            envios, fila = sdr.envios_frentes(conn), sdr.fila_do_dia(conn)
        finally:
            conn.close()
    except (Exception, SystemExit) as e:  # sem banco do SDR a aba sai sem o bloco, nao quebra
        print(f"[AVISO] banco do SDR indisponivel, Visao Geral sem divisao dos disparos: {str(e)[:160]}")
        return ()
    return explorar_disparos.componentes_visao_geral(envios, fila, hoje)


def main():
    agora = datetime.datetime.now(sync.BRT)
    sh = sync.get_sheets_client().open_by_key(sync.SPREADSHEET_ID)
    svc = sync.build_sheets_service()
    resumo = dp.ler_resumo(svc, sync.SPREADSHEET_ID)
    painel, faltando = montar_painel(resumo, agora, disparos_da_brenda(agora.date()))
    if faltando:
        print(f"[AVISO] chaves ausentes no resumo (sairam como 0): {faltando}")
    painel.publicar(sh, svc, sync.SPREADSHEET_ID, indice=0)
    print("Visao Geral: aba publicada.")


if __name__ == "__main__":
    main()
