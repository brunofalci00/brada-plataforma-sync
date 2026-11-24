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
    # Reuniao: mesma contagem do cartao "Reunioes: vindas da Brenda" (cards que chegaram a reuniao).
    ("Incentivador", "Brenda -> incentivadores", "desde 01/09",
     [("disparados", "incentivador_disparados"), ("clicaram/responderam", "incentivador_engajaram"),
      ("reunioes", "incentivador_cards_reuniao")]),
    ("Proponente plataforma", "Brenda -> ONGs", "desde 14/08",
     [("disparados", "aptos_sdr_disparados"), ("cadastraram", "aptos_sdr_cadastros"),
      ("projetos aptos", "aptos_sdr_aptos")]),
    ("Proponente plataforma", "Plataforma, todas as origens", "total",
     [("contas novas", "plataforma_novos_cadastros"), ("com projeto apto", "plataforma_novos_aptos")]),
    # Decisao de 23/11: o fim da elaboracao e a assinatura do AprovAI (lead_id da Brenda no utm_term),
    # medida so desde 23/11. Na linha do site, o assinante nao vem necessariamente do lead: sem %.
    ("Proponente elaboracao", "Brenda -> elaboracao", "desde 27/08",
     [("disparados", "elaboracao_disparados"), ("clicaram/responderam", "elaboracao_engajaram"),
      ("assinaram (desde 23/11)", "elaboracao_assinantes")]),
    ("Proponente elaboracao", "AprovAI (site e app)", "desde 26/10",
     [("visitas", "aprovai_visitas"), ("leads", "aprovai_leads"), ("pagando hoje", "aprovai_assinantes", False)]),
    ("Comunidade", "Comunidade", "total",
     [("membros", "comunidade_membros"), ("voltaram (30 dias)", "comunidade_voltaram_30d")]),
    # Procura por espaco: clique contado 1 por pessoa, espaco e dia so desde 17/11.
    ("Primeira Fila", "Procura por espacos", "desde 17/11",
     [("cliques em espacos", "pf_cliques_30d"), ("conversas", "pf_conversas_30d"),
      ("reservas pagas", "pf_reservas_pagas")]),
    ("Primeira Fila", "Prospeccao de produtores", "desde 20/11",
     [("abordados", "pf_produtor_abordados"), ("clicaram", "pf_produtor_clicaram"),
      ("cadastraram", "pf_produtor_cadastros")]),
]
FRENTES_BRENDA = [("Incentivador", "incentivador"), ("Elaboracao", "elaboracao"), ("Plataforma", "plataforma")]
LIMITE_PARADA = 2  # mesmo corte da aba Brenda

# Precisa de acao: (o que olhar, chave, limite, quando dispara). Dispara se valor > limite.
# Sem coluna de dono por enquanto (Bruno, 23/11): os donos ainda nao estao combinados.
ALERTAS = [
    ("Incentivador: cards da Brenda parados ha 7+ dias no HubSpot", "incentivador_cards_parados", 0, "mais de 0"),
    ("Incentivador: cards da Brenda sem executivo no HubSpot", "incentivador_cards_sem_dono", 0, "mais de 0"),
    ("Plataforma: projetos com prazo vencido (sairam da vitrine)", "plataforma_a_reativar", 0, "mais de 0"),
    ("Plataforma: projetos em rascunho que faltam ate 3 campos para publicar", "plataforma_rascunhos_perto", 0,
     "mais de 0"),
]


def valor(r, chave, faltando):
    if chave not in r:
        faltando.append(chave)
        return 0
    return dp._numero(r[chave])


PARADA_SINGULAR = {"com", "em", "desde", "hoje", "ou", "na", "no", "do", "da"}


def singular(nome):
    """'reunioes' -> 'reuniao', 'clicaram/responderam' -> 'clicou/respondeu': para '1 reuniao', nao '1 reunioes'.
    Muda so as palavras do comeco, ate a primeira preposicao ou parentese."""
    def palavra(w):
        for fim, troca in (("aram", "ou"), ("eram", "eu"), ("iram", "iu"), ("oes", "ao")):
            if w.endswith(fim):
                return w[:-len(fim)] + troca
        return w[:-1] if w.endswith("s") and len(w) > 3 else w
    saida, parou = [], False
    for w in nome.split(" "):
        parou = parou or w in PARADA_SINGULAR or w.startswith("(")
        saida.append(w if parou else "/".join(palavra(p) for p in w.split("/")))
    return " ".join(saida)


def celula_etapa(qtd, nome, anterior=None):
    """'222 clicaram ou responderam (35%)': o % e sobre a etapa anterior."""
    texto = f"{dp.num_br(qtd)} {singular(nome) if qtd == 1 else nome}"
    if anterior is None:
        return texto
    return f"{texto} ({dp.pct_br(dp.taxa(qtd, anterior))})"


def linha_produto(publico, produto, periodo, etapas, v):
    """etapa = (nome, chave) ou (nome, chave, False): False = sem % sobre a anterior (nao vem dela)."""
    qtds = [v(e[1]) for e in etapas]
    cels = [celula_etapa(q, e[0], qtds[i - 1] if i and (len(e) < 3 or e[2]) else None)
            for i, (e, q) in enumerate(zip(etapas, qtds))]
    meio = cels[1] if len(cels) == 3 else ""
    ligado = len(etapas[-1]) < 3 or etapas[-1][2]  # ultima etapa que nao vem da primeira: sem taxa de ponta a ponta
    return [publico, produto, periodo, cels[0], meio, cels[-1], dp.taxa(qtds[-1], qtds[0]) if ligado else ""]


def linhas_alerta(v, r):
    """[o que olhar, agora, dispara quando, situacao]: Brenda por frente, Incentivador, Plataforma, Primeira Fila."""
    out = []
    for nome, chave in FRENTES_BRENDA:
        chave_dias = f"brenda_{chave}_dias_sem_envio"
        dias = int(v(chave_dias)) if chave_dias in r else 99  # sem dado = alerta, nunca "ok"
        sete = int(v(f"brenda_{chave}_envios_7d"))
        out.append([f"Brenda: disparos para {nome}", f"{dias} {'dia util' if dias == 1 else 'dias uteis'} sem envio ({sete} em 7 dias)",
                    f"mais de {LIMITE_PARADA} dias uteis sem envio", "Agir" if dias > LIMITE_PARADA else "Em dia"])
    for nome, chave, limite, quando in ALERTAS:
        n = int(v(chave))
        out.append([nome, dp.num_br(n), quando, "Agir" if n > limite else "Em dia"])
    cliques, conversas = int(v("pf_cliques_30d")), int(v("pf_conversas_30d"))
    out.append(["Primeira Fila: quem clica num espaco do guia e abre conversa com o anfitriao (desde 17/11)",
                f"{dp.num_br(conversas)} {'conversa' if conversas == 1 else 'conversas'} em {dp.num_br(cliques)} cliques", "menos de 1% dos cliques",
                "Agir" if cliques >= 30 and conversas * 100 < cliques else "Em dia"])
    return sorted(out, key=lambda l: l[-1] != "Agir")


def chaves_usadas():
    """Toda chave do resumo que a Visao Geral le (para o aviso de numero velho)."""
    chaves = {e[1] for *_, etapas in PRODUTOS for e in etapas} | {c for _, c, *_ in ALERTAS}
    chaves |= {f"brenda_{c}_{s}" for _, c in FRENTES_BRENDA for s in ("dias_sem_envio", "envios_7d")}
    return chaves | {"plataforma_aptos", "comunidade_membros", "incentivador_pipeline_reuniao",
                     "aprovai_mrr", "pf_comissao_mes", "pf_cliques_30d", "pf_conversas_30d"}


def montar_painel(r, agora, velhas=()):
    faltando = []
    v = lambda k: valor(r, k, faltando)  # noqa: E731
    p = dp.Painel(ABA, "Ecossistema Brada · visao geral",
                  f"Atualizado {agora.strftime('%d/%m/%Y %H:%M')} · Brenda = a prospeccao automatica (Automatize)"
                  " · receita do mes = assinaturas do AprovAI + comissao da Primeira Fila"
                  + (f" · ATENCAO: {len(velhas)} numero(s) sao da ultima rodada que funcionou" if velhas else ""))
    p.linha(dp.Cartao("Projetos aptos a captar", v("plataforma_aptos")),
            dp.Cartao("Membros da Comunidade", v("comunidade_membros")),
            dp.Cartao("Reunioes: todas as origens", v("incentivador_pipeline_reuniao")),
            dp.Cartao("Reunioes: vindas da Brenda", v("incentivador_cards_reuniao")),
            dp.Cartao("Receita do mes", v("aprovai_mrr") + v("pf_comissao_mes"), dp.FMT_BRL))
    linhas = [linha_produto(*prod, v) for prod in PRODUTOS]
    p.linha(dp.Tabela("Funil de cada produto (o % de cada etapa e sobre a anterior; cada linha tem o seu periodo)",
                      ["Publico", "Produto", "Periodo", "Primeira etapa", "Etapa do meio", "Ultima etapa",
                       "Do inicio ao fim"], linhas,
                      formatos=[None, None, None, None, None, None, dp.FMT_PCT], spans=[2, 2, 1, 2, 2, 2, 1]))
    p.linha(dp.Tabela("O que fazer agora (Agir primeiro)",
                      ["O que olhar", "Agora", "Alerta se", "Situacao"], linhas_alerta(v, r),
                      spans=[6, 3, 2, 1]))
    return p, sorted(set(faltando))


def main():
    agora = datetime.datetime.now(sync.BRT)
    sh = sync.get_sheets_client().open_by_key(sync.SPREADSHEET_ID)
    svc = sync.build_sheets_service()
    resumo = dp.ler_resumo(svc, sync.SPREADSHEET_ID)
    usadas = chaves_usadas()
    velhas = dp.chaves_velhas(svc, sync.SPREADSHEET_ID, agora.date(), usadas)
    if velhas:
        print(f"[AVISO] {len(velhas)} chave(s) do resumo sao de outro dia (passo falhou ou fonte fora): {velhas}")
    painel, faltando = montar_painel(resumo, agora, velhas)
    if faltando:
        print(f"[AVISO] chaves ausentes no resumo (sairam como 0): {faltando}")
    painel.publicar(sh, svc, sync.SPREADSHEET_ID, indice=0)
    print("Visao Geral: aba publicada.")


if __name__ == "__main__":
    main()
