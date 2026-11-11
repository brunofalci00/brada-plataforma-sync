"""Aba "Visao Geral": KPIs do ecossistema Brada contados como uma historia.

Roda por ultimo no CI. Nao consulta fonte nenhuma: le os numeros que as outras abas
acabaram de escrever (Plataforma Brada, Comunidade, SDR Incentivador, AprovAI,
Primeira Fila) e monta 5 capitulos: chegada, ativacao, retorno, conexao e receita.
Cada capitulo tem uma frase que muda conforme os numeros.

Como acha cada numero: pelo TITULO da secao na aba de origem e pela POSICAO da metrica
dentro dela (0 = primeira). Nao depende do rotulo, que pode mudar (ex.: "10/2026 completo").
Se uma aba ou secao sumir, o KPI sai como "-" e a frase avisa; a aba nao quebra.

KPIs e frases: definidos pelo Bruno (dono de dados), documentados no vault em
01_Projetos/Dashboard_Funil_Plataforma/kpis_storytelling_dashboard.md.
"""

import datetime

import sync
import dashboard_kpi_blocks as kpi
from sync_aprovai import build_sheets_service

ABA = "Visão Geral"
PCT = {"type": "PERCENT", "pattern": "0.0%"}
BRL = {"type": "CURRENCY", "pattern": "R$#,##0.00"}

# Limiar das frases de alerta.
RETORNO_SAUDAVEL = 0.25      # abaixo disso, o retorno da Comunidade e "baixo"
CLIQUES_SEM_PEDIDO = 100     # procura relevante na Primeira Fila sem pedido = alerta

# chave: (aba, trecho do titulo da secao, posicao da metrica)
FONTES = {
    "aptos_hoje": ("Plataforma Brada", "Estoque e prazo", 1),
    "prazo_vencido": ("Plataforma Brada", "Estoque e prazo", 2),
    "cad_plat_mes": ("Plataforma Brada", "Cadastros e uso", 0),
    "cad_plat_mes_ant": ("Plataforma Brada", "Cadastros e uso", 1),
    "acessos_plat_30d": ("Plataforma Brada", "Cadastros e uso", 3),
    # Google Analytics da propriedade 537748462 (match.brada.social), no ar desde
    # 11/11. "visitantes_plat_30d" ainda nao tem capitulo (os 4 slots de Chegada ja
    # estao ocupados por publicos distintos) - fica disponivel pra quando o Bruno
    # quiser trocar algum.
    "visitantes_plat_30d": ("Plataforma Brada", "Visitas (Google Analytics)", 0),
    "retorno_plat": ("Plataforma Brada", "Visitas (Google Analytics)", 2),
    "novos_cadastros": ("Plataforma Brada", "Novos proponentes", 0),
    "novos_aptos": ("Plataforma Brada", "Novos proponentes", 3),
    "visitantes_com": ("Comunidade", "Visitas", 0),
    "retorno_com": ("Comunidade", "Visitas", 2),
    "membros_com": ("Comunidade", "Membros", 0),
    "cad_com_mes": ("Comunidade", "Membros", 1),
    "login_com_7d": ("Comunidade", "Uso", 0),
    # Saiu do capitulo Retorno em 11/11 pra abrir espaco pro "retorno_plat" (GA da
    # Plataforma); segue disponivel, so nao aparece em nenhum capitulo hoje.
    "acao_com": ("Comunidade", "Uso", 3),
    "pipeline_inc": ("SDR Incentivador", "Volume por canal", 3),
    "reunioes_inc": ("SDR Incentivador", "Quem chega em Reuniao", 0),
    "reunioes_automatize": ("SDR Incentivador", "Quem chega em Reuniao", 1),
    "views_aprovai": ("AprovAI", "Funil da landing", 0),
    "assinantes_aprovai": ("AprovAI", "Conversao e assinantes", 1),
    "mrr_aprovai": ("AprovAI", "Conversao e assinantes", 2),
    "espacos_pf": ("Primeira Fila", "Espacos anunciados", 1),
    "cliques_pf_30d": ("Primeira Fila", "Procura", 1),
    "pedidos_pf": ("Primeira Fila", "Procura", 3),
    "reservas_pf": ("Primeira Fila", "Reservas e dinheiro", 0),
    "valor_pf": ("Primeira Fila", "Reservas e dinheiro", 1),
}


# ---------------------------------------------------------------- leitura das abas

def _numero(v):
    if isinstance(v, (int, float)):
        return v
    if v in (None, "", "-"):
        return None
    t = str(v).replace("R$", "").replace("%", "").replace(".", "").replace(",", ".").strip()
    try:
        n = float(t)
    except ValueError:
        return None
    return n / 100 if "%" in str(v) else n


def metricas_da_secao(grade, trecho):
    """Valores da primeira linha de numeros depois do titulo que contem `trecho`."""
    for i, linha in enumerate(grade):
        celulas = [c for c in linha if c not in ("", None)]
        if i == 0 or len(celulas) != 1 or trecho.lower() not in str(celulas[0]).lower():
            continue
        for seguinte in grade[i + 1:i + 3]:
            valores = [c for c in seguinte if c not in ("", None)]
            numeros = [_numero(c) for c in valores]
            if valores and all(n is not None for n in numeros):
                return numeros
        return []
    return []


def extrair(grades):
    """grades = {aba: matriz de valores}. Devolve {chave: numero ou None}."""
    out = {}
    for chave, (aba, trecho, pos) in FONTES.items():
        valores = metricas_da_secao(grades.get(aba, []), trecho)
        out[chave] = valores[pos] if pos < len(valores) else None
    return out


# ---------------------------------------------------------------- historia

def _n(v):
    if v is None:
        return "?"
    return f"{v:,.0f}".replace(",", ".")


def _pct(v):
    return "?" if v is None else f"{v * 100:.0f}%".replace(".", ",")


def _brl(v):
    return "?" if v is None else ("R$ " + f"{v:,.2f}").replace(",", "X").replace(".", ",").replace("X", ".")


def _taxa(parte, todo):
    return parte / todo if parte is not None and todo else None


def frases(k):
    """Uma frase por capitulo + a manchete. Cada uma: (texto, e_alerta)."""
    f = {}
    f["manchete"] = (
        f"{_n(k['aptos_hoje'])} projetos podem receber investimento hoje e {_n(k['membros_com'])} "
        f"pessoas fazem parte da Comunidade. A oferta existe; o desafio agora é fazer quem chega voltar "
        f"e transformar procura em dinheiro.", False)

    f["chegada"] = (
        f"{_n(k['visitantes_com'])} pessoas visitaram a Comunidade em 30 dias e {_n(k['cad_com_mes'])} "
        f"se cadastraram neste mês. Na plataforma de projetos foram {_n(k['cad_plat_mes'])} cadastros "
        f"no mês até agora ({_n(k['cad_plat_mes_ant'])} no mês anterior inteiro).", False)

    taxa_novos = _taxa(k["novos_aptos"], k["novos_cadastros"])
    f["ativacao"] = (
        f"De cada 100 proponentes novos, {_n(None if taxa_novos is None else taxa_novos * 100)} chegam "
        f"a ter projeto apto a captar. {_n(k['prazo_vencido'])} projetos estão com prazo vencido e "
        f"saem da vitrine até serem reativados.", (k["prazo_vencido"] or 0) > 0)

    retorno = k["retorno_com"]
    baixo = retorno is not None and retorno < RETORNO_SAUDAVEL
    f["retorno"] = (
        (f"Só {_pct(retorno)} dos visitantes da Comunidade voltaram em 30 dias: a maioria entra uma vez "
         f"e não volta." if baixo else
         f"{_pct(retorno)} dos visitantes da Comunidade voltaram em 30 dias.")
        + f" Na plataforma, {_n(k['acessos_plat_30d'])} pessoas acessaram no mesmo período e "
          f"{_pct(k['retorno_plat'])} dos visitantes voltaram.", baixo)

    cliques, pedidos = k["cliques_pf_30d"], k["pedidos_pf"]
    procura_parada = (cliques or 0) >= CLIQUES_SEM_PEDIDO and not pedidos
    f["conexao"] = (
        f"{_n(k['reunioes_inc'])} incentivadores chegaram a reunião, {_n(k['reunioes_automatize'])} "
        f"deles pela Automatize. Na Primeira Fila, {_n(cliques)} cliques em espaços em 30 dias viraram "
        f"{_n(pedidos)} pedidos de reserva" + (": há procura, falta conversão." if procura_parada else "."),
        procura_parada or not k["reunioes_automatize"])

    sem_receita = not k["assinantes_aprovai"] and not k["reservas_pf"]
    f["receita"] = (
        ("Os produtos digitais ainda não geram receita: " if sem_receita else "Receita dos produtos digitais: ")
        + f"{_n(k['assinantes_aprovai'])} assinantes no AprovAI ({_brl(k['mrr_aprovai'])} por mês) e "
          f"{_n(k['reservas_pf'])} reservas pagas na Primeira Fila ({_brl(k['valor_pf'])}).", sem_receita)
    return f


def _v(k, chave):
    return "-" if k[chave] is None else k[chave]


def montar_secoes(k):
    f = frases(k)
    return [
        kpi.Secao("O ecossistema hoje", [
            (_v(k, "aptos_hoje"), "Projetos aptos a captar hoje (norte)", None),
            (_v(k, "membros_com"), "Membros da Comunidade", None),
            (_v(k, "reunioes_inc"), "Incentivadores em reunião ou além", None),
            (_v(k, "mrr_aprovai"), "Receita recorrente dos produtos digitais", BRL),
        ], nota=f["manchete"][0]),
        kpi.Secao("1. Chegada: quem descobre a Brada", [
            (_v(k, "visitantes_com"), "Visitantes da Comunidade (30 dias)", None),
            (_v(k, "cad_com_mes"), "Cadastros na Comunidade no mês", None),
            (_v(k, "cad_plat_mes"), "Cadastros na plataforma no mês", None),
            (_v(k, "views_aprovai"), "Visitas à página do AprovAI", None),
        ], nota=f["chegada"][0], nota_aviso=f["chegada"][1]),
        kpi.Secao("2. Ativação: quem vira oferta", [
            (_v(k, "aptos_hoje"), "Projetos aptos a captar hoje", None),
            (_v(k, "novos_aptos"), "Proponentes novos com projeto apto", None),
            (_v(k, "prazo_vencido"), "Projetos com prazo vencido", None),
            (_v(k, "espacos_pf"), "Espaços no ar na Primeira Fila", None),
        ], nota=f["ativacao"][0], nota_aviso=f["ativacao"][1]),
        kpi.Secao("3. Retorno: quem volta", [
            ("-" if k["retorno_com"] is None else k["retorno_com"], "Visitantes da Comunidade que voltaram", PCT),
            (_v(k, "login_com_7d"), "Membros que entraram em 7 dias", None),
            ("-" if k["retorno_plat"] is None else k["retorno_plat"], "Visitantes da Plataforma que voltaram", PCT),
            (_v(k, "acessos_plat_30d"), "Pessoas que acessaram a plataforma (30 dias)", None),
        ], nota=f["retorno"][0], nota_aviso=f["retorno"][1]),
        kpi.Secao("4. Conexão: oferta encontra demanda", [
            (_v(k, "pipeline_inc"), "Incentivadores no pipeline", None),
            (_v(k, "reunioes_inc"), "Chegaram a reunião ou além", None),
            (_v(k, "cliques_pf_30d"), "Cliques em espaços (30 dias)", None),
            (_v(k, "pedidos_pf"), "Pedidos de reserva", None),
        ], nota=f["conexao"][0], nota_aviso=f["conexao"][1]),
        kpi.Secao("5. Receita dos produtos digitais", [
            (_v(k, "assinantes_aprovai"), "Assinantes do AprovAI", None),
            (_v(k, "mrr_aprovai"), "Receita mensal do AprovAI", BRL),
            (_v(k, "reservas_pf"), "Reservas pagas na Primeira Fila", None),
            (_v(k, "valor_pf"), "Valor das reservas pagas", BRL),
        ], nota=f["receita"][0], nota_aviso=f["receita"][1]),
    ]


def _primeira_aba(svc, ws):
    svc.spreadsheets().batchUpdate(spreadsheetId=sync.SPREADSHEET_ID, body={"requests": [
        {"updateSheetProperties": {"properties": {"sheetId": ws.id, "index": 0}, "fields": "index"}}]}).execute()


def main():
    agora = datetime.datetime.now(sync.BRT)
    gc = sync.get_sheets_client()
    sh = gc.open_by_key(sync.SPREADSHEET_ID)
    grades = {}
    for aba in sorted({a for a, _, _ in FONTES.values()}):
        try:
            grades[aba] = sh.worksheet(aba).get_all_values(value_render_option="UNFORMATTED_VALUE")
        except Exception as erro:  # noqa: BLE001 - aba ausente vira "-" na visao geral
            print(f"::warning::aba {aba} indisponivel: {erro}")
    k = extrair(grades)
    faltando = [c for c, v in k.items() if v is None]
    if faltando:
        print(f"::warning::KPIs sem valor: {', '.join(faltando)}")
    subtitulo = (f"Foto: {agora.strftime('%d/%m/%Y %H:%M')} (Brasília) - resumo das abas do dashboard. "
                 "Pergunta central: a Brada atrai, ativa e conecta quem faz e quem financia impacto?")
    svc = build_sheets_service()
    ws = kpi.publish(sh, svc, sync.SPREADSHEET_ID, ABA, "Visão Geral - Ecossistema Brada",
                     subtitulo, montar_secoes(k))
    _primeira_aba(svc, ws)
    print(f"Visao Geral: {len(k) - len(faltando)}/{len(k)} KPIs preenchidos.")


if __name__ == "__main__":
    main()
