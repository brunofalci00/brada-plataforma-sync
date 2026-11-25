"""Cupons da Primeira Fila: quantos anfitrioes estao com cada cupom e quem sao (revisao do Bruno, 25/11).

"Quem" sai pelo espaco (nome publico do anuncio e protocolo PF-...), nunca pelo nome, e-mail ou telefone
da pessoa: a planilha nao recebe dado pessoal. Anfitriao sem espaco aparece so pela data do cadastro.
"""

import datetime

import dashboard_painel as dp

CONSULTA = """
    select p.cupom_codigo, p.cupom_tipo, p.cupom_inicio::date, p.cupom_fim::date, coalesce(p.plano, ''),
           (p.created_at at time zone 'America/Sao_Paulo')::date,
           (select string_agg(e.name || ' (' || e.protocol || ')', ', ' order by e.created_at)
              from espacos_reais e where e.owner_id = p.id),
           (select count(*) from espacos_reais e where e.owner_id = p.id and e.revisao_status = 'publicado'
              and not coalesce(e.pausado, false) and not coalesce(e.revisao_oculto, false))
    from perfis_reais p where p.cupom_codigo is not null
    order by p.cupom_codigo, p.cupom_inicio"""

CAB_RESUMO = ["Cupom", "Anfitrioes", "Com espaco no guia", "Cupom valendo hoje"]
CAB_LISTA = ["Cupom", "Espacos do anfitriao", "Cadastro", "Cupom desde", "Cupom ate", "Plano", "No guia"]
SEM_ESPACO = "Sem espaco cadastrado"


def _data(d):
    return d.strftime("%d/%m/%y") if d else ""


def valendo(fim, hoje):
    return fim is None or fim >= hoje


def resumo(registros, hoje):
    """[[cupom, anfitrioes, com espaco no guia, cupom valendo hoje]] do cupom mais usado para o menos usado."""
    por = {}
    for codigo, _tipo, _ini, fim, _plano, _criado, _espacos, no_guia in registros:
        c = por.setdefault(codigo, [codigo, 0, 0, 0])
        c[1] += 1
        c[2] += int(no_guia > 0)
        c[3] += int(valendo(fim, hoje))
    return sorted(por.values(), key=lambda c: (-c[1], c[0]))


def lista(registros):
    return [[codigo, espacos or SEM_ESPACO, _data(criado), _data(ini), _data(fim) or "sem fim", plano or "-",
             "sim" if no_guia else "nao"]
            for codigo, _tipo, ini, fim, plano, criado, espacos, no_guia in registros]


def coletar(cur, base, params):
    cur.execute(base + CONSULTA, params)
    return cur.fetchall()


def blocos(painel, registros, hoje=None):
    hoje = hoje or datetime.date.today()
    if not registros:
        return
    painel.linha(dp.Tabela("Cupons: quantos anfitrioes estao com cada um (hoje)", CAB_RESUMO, resumo(registros, hoje),
                           formatos=[None, dp.FMT_INT, dp.FMT_INT, dp.FMT_INT], spans=[3, 3, 3, 3]))
    painel.linha(dp.Tabela("Cupons: quem esta com cada um (pelo espaco anunciado; sem nome nem contato da pessoa)",
                           CAB_LISTA, lista(registros), spans=[2, 4, 2, 1, 1, 1, 1]))
