"""Prospeccao de produtores da Primeira Fila (SALIC).

Fonte: visoes `dados.prospeccao_produtores` (um produtor por linha) e `dados.prospeccao_emails`
(um e-mail por linha), criadas pela Automatize em 19/11. So agregados saem daqui: nome, e-mail,
telefone e lead_id ficam no banco, para a comercial consultar la.

`whatsapp_resultado` nao e lido: em 26/11 a consulta pela conta `leitura_dados` volta "relation does not
exist" (a visao nao esta no esquema `dados`). Pedido aberto com a Automatize.
"""

import painel_filtros as fl
import dashboard_painel as dp

TZ = "America/Sao_Paulo"

# Um registro por produtor ja abordado, na data do primeiro contato (e-mail ou WhatsApp).
CONSULTA_PRODUTORES = f"""
    select (least(enviado_em, whatsapp_enviado_em) at time zone '{TZ}')::date, lei, uf,
           enviado_em is not null, whatsapp_enviado_em is not null,
           clicou_em is not null or whatsapp_clicou_em is not null,
           cadastrado_em is not null, descadastrou_em is not null
    from dados.prospeccao_produtores
    where enviado_em is not null or whatsapp_enviado_em is not null"""

# Foto de hoje da fila (sem data): total, ja abordados, com telefone.
CONSULTA_FILA = """
    select count(*), count(*) filter (where enviado_em is not null or whatsapp_enviado_em is not null),
           count(*) filter (where nullif(telefone, '') is not null)
    from dados.prospeccao_produtores"""

BASE = fl.Base("base_pf_produtor", [("lei", "Lei", fl.TODAS), ("uf", "UF", fl.TODAS),
                                    ("canal_produtor", "Canal", fl.TODOS)], [
    ("abordados", "Abordados"), ("email", "Receberam e-mail"), ("whatsapp", "Receberam WhatsApp"),
    ("clicaram", "Clicaram no link"), ("cadastraram", "Cadastraram"), ("descadastraram", "Descadastraram")])
FUNIL = [("Produtores abordados", "abordados"), ("Clicaram no link", "clicaram"), ("Cadastraram", "cadastraram")]
PUBLICOS = {"produtor": "Produtor", "espaco": "Anfitriao (espaco)"}


def _canal(email, whatsapp):
    if email and whatsapp:
        return "E-mail e WhatsApp"
    return "WhatsApp" if whatsapp else "E-mail"


def linhas(registros):
    """registros: (dia, lei, uf, email, whatsapp, clicou, cadastrou, descadastrou)."""
    def um(dia, lei, uf, email, whatsapp, clicou, cadastrou, descadastrou):
        cadastrou = bool(cadastrou)
        etapas = {"abordados": 1, "email": email, "whatsapp": whatsapp, "clicaram": clicou or cadastrou,
                  "cadastraram": cadastrou, "descadastraram": descadastrou}
        return dia, {"lei": lei or "sem lei", "uf": uf.strip().upper() if uf else "sem UF",
                     "canal_produtor": _canal(email, whatsapp)}, etapas
    return BASE.agregar(um(*r) for r in registros)


def coletar(cur):
    cur.execute(CONSULTA_PRODUTORES)
    registros = cur.fetchall()
    cur.execute(CONSULTA_FILA)
    fila = dict(zip(("total", "abordados", "com_telefone"), cur.fetchone()))
    return {"produtor_linhas": registros, "produtor_fila": fila}


def blocos(painel, ctl, dados):
    """Produtor (SALIC): fila, funil do periodo A, A x B, por lei e canal; sem a tabela por versao do e-mail (saiu em 25/11: a versao 1 de anfitriao estava errada e foi trocada).
    Devolve (linhas da base, resumo)."""
    pl = linhas(dados.get("produtor_linhas", []))
    fila = dados.get("produtor_fila") or {"total": 0, "abordados": 0, "com_telefone": 0}
    fp = fl.Filtros(ctl, BASE)
    painel.linha(dp.Cartao("Produtores na fila (SALIC, hoje)", fila["total"]),
                 dp.Cartao("Ja abordados (hoje)", fila["abordados"]),
                 dp.Cartao("Ainda nao abordados (hoje)", fila["total"] - fila["abordados"]),
                 dp.Cartao("Com telefone (hoje)", fila["com_telefone"]))
    painel.linha(fl.FunilVivo("Produtor: prospeccao de produtores da Rouanet, do contato ao cadastro (periodo A)", fp, FUNIL, altura=320,
                              subtitulo="Produtor na data do primeiro contato · etapas = estado de hoje · "
                                        "filtros de cidade, categoria e canal do Hunter nao valem aqui"))
    painel.linha(fl.por_dimensao("Produtor por canal (periodo A)", fp, "canal_produtor",
                                 BASE.opcoes(pl, "canal_produtor") or ["E-mail"], [
                     ("Abordados", "soma", "abordados"), ("Clicaram", "taxa", "clicaram"),
                     ("Cadastraram", "soma", "cadastraram"), ("Descadastraram", "taxa", "descadastraram")],
                     cabecalho_dim="Canal"))
    leis = BASE.opcoes(pl, "lei") or ["sem lei"]
    if len(leis) > 1:  # uma lei so (hoje, Rouanet) repetiria o funil
        painel.linha(fl.por_dimensao("Produtor por lei (periodo A)", fp, "lei", leis, [
            ("Abordados", "soma", "abordados"), ("Clicaram", "taxa", "clicaram"), ("Cadastraram", "soma", "cadastraram"),
            ("Descadastraram", "taxa", "descadastraram")], gradiente=[1], cabecalho_dim="Lei"))
    total = {k: sum(l[BASE.cabecalho.index(c)] for l in pl) for k, c in BASE.etapas}
    resumo = {"pf_produtor_abordados": total["abordados"], "pf_produtor_clicaram": total["clicaram"],
              "pf_produtor_cadastros": total["cadastraram"]}
    return pl, resumo
