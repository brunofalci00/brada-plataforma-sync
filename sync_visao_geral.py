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

# (publico, produto, etapas, chave do comeco, do meio, do fim). Agrupado pelos 3 publicos, que nao se
# misturam; cada funil tem a propria profundidade, entao nao ha gradiente comparando um com o outro.
PRODUTOS = [
    # O card do Incentivador nasce sozinho ao abrir a agenda (robo): o meio e quem clicou ou respondeu.
    ("Incentivador", "SDR Incentivador", "disparo > clicou ou respondeu > reuniao",
     "incentivador_disparados", "incentivador_engajaram", "incentivador_reuniao"),
    ("Proponente da plataforma", "SDR Aptos", "disparo > cadastro > apto",
     "aptos_sdr_disparados", "aptos_sdr_cadastros", "aptos_sdr_aptos"),
    ("Proponente da plataforma", "Plataforma (novos, todas as origens)", "cadastro > apto",
     "plataforma_novos_cadastros", None, "plataforma_novos_aptos"),
    # O assinante do AprovAI nao vem do disparo: o SDR de elaboracao termina no cadastro.
    ("Proponente de elaboracao", "SDR Elaboracao", "disparo > cadastro", "elaboracao_disparados", None,
     "elaboracao_cadastros"),
    ("Proponente de elaboracao", "AprovAI (app)", "visita > lead > assinante", "aprovai_visitas", "aprovai_leads",
     "aprovai_assinantes"),
    ("Outros", "Comunidade", "visitante 30d > membro > acao",
     "comunidade_visitantes_30d", "comunidade_membros", "comunidade_primeira_acao"),
    ("Outros", "Primeira Fila", "visita > pagina de espaco > paga (desde 17/11)",
     "pf_jornada_visitas", "pf_jornada_pag_espaco", "pf_reservas_pagas"),
    ("Outros", "SDR Primeira Fila (produtor SALIC)", "abordado > clicou > cadastro",
     "pf_produtor_abordados", "pf_produtor_clicaram", "pf_produtor_cadastros"),
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
            dp.Cartao(f"Reunioes de incentivador (Brenda: {int(v('incentivador_reuniao'))})",
                      v("incentivador_pipeline_reuniao")),
            dp.Cartao("Receita do mes (MRR + comissao PF)",
                      v("aprovai_mrr") + v("pf_comissao_mes"), dp.FMT_BRL))
    linhas = []
    for publico, nome, etapas, comeco, meio, fim in PRODUTOS:
        c, m, f = v(comeco), v(meio), v(fim)
        cm, mf = (dp.taxa(m, c), dp.taxa(f, m)) if meio else ("", "")
        gargalo = "" if not meio or not c else ("Comeco > meio" if cm <= mf else "Meio > fim")
        linhas.append([publico, nome, etapas, c, m, f, cm, mf, dp.taxa(f, c), gargalo])
    p.linha(dp.Tabela("Funil de cada produto, por publico",
                      ["Publico", "Produto", "Etapas", "Comeco", "Meio", "Fim", "Comeco > meio", "Meio > fim",
                       "Fim / comeco", "Onde mais perde"],
                      linhas, formatos=[None, None, None, dp.FMT_INT, dp.FMT_INT, dp.FMT_INT, dp.FMT_PCT,
                                        dp.FMT_PCT, dp.FMT_PCT, None]))
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
