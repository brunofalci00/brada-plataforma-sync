"""Aba "Visao Geral": o ecossistema Brada em uma tela.

Roda por ultimo no CI. Nao consulta fonte: le a aba escondida `dados_resumo`, que
cada sync de canal acabou de gravar (chave -> valor). Chave ausente vira 0 e sai
no log; a aba nao quebra.

Tres blocos: 5 cartoes, o alerta "a Brenda esta disparando?" e uma linha por produto,
com o nome de cada etapa escrito na celula (sem "comeco/meio/fim") e o periodo que a linha cobre.
O detalhe dos disparos mora na aba "Brenda".
"""

import datetime

import sync
import dashboard_painel as dp

ABA = "Visão Geral"

# (publico, produto, periodo, [(nome da etapa, chave do resumo)]). Os 3 publicos nao se misturam; cada
# funil tem a propria profundidade e o proprio periodo, entao as linhas nao se comparam entre si.
PRODUTOS = [
    # O card do Incentivador nasce sozinho ao abrir a agenda (robo): nao e etapa; o resultado e reuniao.
    ("Incentivador", "SDR Incentivador", "desde 01/09",
     [("disparados", "incentivador_disparados"), ("clicaram/responderam", "incentivador_engajaram"),
      ("reunioes", "incentivador_reuniao")]),
    ("Proponente plataforma", "SDR Aptos", "desde 14/08",
     [("disparados", "aptos_sdr_disparados"), ("cadastraram", "aptos_sdr_cadastros"),
      ("projetos aptos", "aptos_sdr_aptos")]),
    ("Proponente plataforma", "Plataforma (sem migrados)", "total",
     [("contas novas", "plataforma_novos_cadastros"), ("com projeto apto", "plataforma_novos_aptos")]),
    # Decisao de 23/11: o fim da elaboracao e a assinatura do AprovAI (lead_id da Brenda no utm_term).
    # Na linha do site, o assinante nao vem necessariamente do lead: sem % na ultima etapa.
    ("Proponente elaboracao", "SDR Elaboracao", "desde 27/08",
     [("disparados", "elaboracao_disparados"), ("clicaram/responderam", "elaboracao_engajaram"),
      ("assinaram o AprovAI", "elaboracao_assinantes")]),
    ("Proponente elaboracao", "AprovAI (site e app)", "desde 26/10",
     [("visitas", "aprovai_visitas"), ("leads", "aprovai_leads"), ("pagando hoje", "aprovai_assinantes", False)]),
    ("Outros", "Comunidade", "total",
     [("membros", "comunidade_membros"), ("acessaram em 30 dias", "comunidade_login_30d")]),
    ("Outros", "Primeira Fila (site)", "desde 17/11",
     [("visitas", "pf_jornada_visitas"), ("viram um espaco", "pf_jornada_pag_espaco"),
      ("reservas pagas", "pf_reservas_pagas")]),
    ("Outros", "SDR Primeira Fila (produtor)", "desde 20/11",
     [("abordados", "pf_produtor_abordados"), ("clicaram", "pf_produtor_clicaram"),
      ("cadastraram", "pf_produtor_cadastros")]),
]
FRENTES_BRENDA = [("Incentivador", "incentivador"), ("Elaboracao", "elaboracao"), ("Plataforma", "plataforma")]
LIMITE_PARADA = 2  # mesmo corte da aba Brenda

# Precisa de acao (decisao de 23/11): (o que olhar, chave, limite, quando dispara, dono). Dispara se valor > limite.
ALERTAS = [
    ("Cards da Brenda parados 7+ dias", "incentivador_cards_parados", 0, "mais de 0", "Vanessa (comercial)"),
    ("Cards da Brenda sem executivo dono", "incentivador_cards_sem_dono", 0, "mais de 0", "Vanessa (comercial)"),
    ("Projetos com prazo vencido, a reativar", "plataforma_a_reativar", 0, "mais de 0", "Bruno (regua de e-mail)"),
    ("Rascunhos a ate 3 campos de publicar", "plataforma_rascunhos_perto", 0, "mais de 0", "Bruno (regua de e-mail)"),
]


def valor(r, chave, faltando):
    if chave not in r:
        faltando.append(chave)
        return 0
    return dp._numero(r[chave])


def celula_etapa(qtd, nome, anterior=None):
    """'222 clicaram ou responderam (35%)': o % e sobre a etapa anterior."""
    texto = f"{dp.num_br(qtd)} {nome}"
    if anterior is None:
        return texto
    return f"{texto} ({dp.pct_br(dp.taxa(qtd, anterior))})"


def linha_produto(publico, produto, periodo, etapas, v):
    """etapa = (nome, chave) ou (nome, chave, False): False = sem % sobre a anterior (nao vem dela)."""
    qtds = [v(e[1]) for e in etapas]
    cels = [celula_etapa(q, e[0], qtds[i - 1] if i and (len(e) < 3 or e[2]) else None)
            for i, (e, q) in enumerate(zip(etapas, qtds))]
    meio = cels[1] if len(cels) == 3 else ""
    return [publico, produto, periodo, cels[0], meio, cels[-1], dp.taxa(qtds[-1], qtds[0])]


def linhas_alerta(v):
    """[o que olhar, agora, dispara quando, dono, situacao]: Brenda por frente, comercial, regua, Primeira Fila."""
    out = []
    for nome, chave in FRENTES_BRENDA:
        dias, sete = int(v(f"brenda_{chave}_dias_sem_envio")), int(v(f"brenda_{chave}_envios_7d"))
        out.append([f"Brenda disparando para {nome}", f"{dias} dias uteis sem envio ({sete} em 7 dias)",
                    f"mais de {LIMITE_PARADA} dias uteis", "Jean (Automatize)",
                    "AGIR" if dias > LIMITE_PARADA else "ok"])
    for nome, chave, limite, quando, dono in ALERTAS:
        n = int(v(chave))
        out.append([nome, dp.num_br(n), quando, dono, "AGIR" if n > limite else "ok"])
    cliques, conversas = int(v("pf_cliques_30d")), int(v("pf_conversas_30d"))
    out.append(["Primeira Fila: cliques nos espacos que viram conversa (30 dias)",
                f"{dp.num_br(conversas)} conversas em {dp.num_br(cliques)} cliques", "menos de 1 a cada 100",
                "Afonso (produto)", "AGIR" if cliques >= 100 and conversas * 100 < cliques else "ok"])
    return sorted(out, key=lambda l: l[-1] != "AGIR")


def montar_painel(r, agora):
    faltando = []
    v = lambda k: valor(r, k, faltando)  # noqa: E731
    p = dp.Painel(ABA, "Ecossistema Brada · visao geral",
                  f"Atualizado {agora.strftime('%d/%m/%Y %H:%M')} · numeros das abas de cada produto"
                  " · reunioes de incentivador no HubSpot · receita = MRR do AprovAI + comissao da Primeira Fila"
                  " · disparos da Automatize em detalhe na aba Brenda")
    p.linha(dp.Cartao("Projetos aptos a captar", v("plataforma_aptos")),
            dp.Cartao("Membros da Comunidade", v("comunidade_membros")),
            dp.Cartao("Reunioes: todas as origens", v("incentivador_pipeline_reuniao")),
            dp.Cartao("Reunioes: vindas da Brenda", v("incentivador_cards_reuniao")),
            dp.Cartao("Receita do mes", v("aprovai_mrr") + v("pf_comissao_mes"), dp.FMT_BRL))
    p.linha(dp.Tabela("Precisa de acao (AGIR primeiro; cada linha tem um dono)",
                      ["O que olhar", "Agora", "Dispara quando", "Dono", "Situacao"], linhas_alerta(v),
                      spans=[4, 3, 2, 2, 1]))
    linhas = [linha_produto(*prod, v) for prod in PRODUTOS]
    p.linha(dp.Tabela("Funil de cada produto (o % de cada etapa e sobre a anterior; cada linha tem o seu periodo)",
                      ["Publico", "Produto", "Periodo", "Primeira etapa", "Etapa do meio", "Ultima etapa",
                       "Ultima / primeira"], linhas,
                      formatos=[None, None, None, None, None, None, dp.FMT_PCT], spans=[2, 2, 1, 2, 2, 2, 1]))
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
