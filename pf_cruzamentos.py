"""Cruzamentos da aba Primeira Fila: busca no Google x oferta por local, Hunter por canal e custo por anfitriao.

Busca por local so enxerga as buscas VISIVEIS do Search Console (as raras o Google esconde)."""
import unicodedata

import dashboard_painel as dp
import painel_filtros as fl

# Locais com busca no autocompletar do Google (estudo de 17/11) mesmo sem espaco no guia.
LOCAIS_COM_DEMANDA = ["Tijuca", "Barra da Tijuca", "Zona Sul", "Zona Norte", "Pinheiros", "Vila Madalena", "Niteroi"]
# Apelidos de cidade que aparecem na busca.
APELIDOS = {"rio de janeiro": ["rj", "rio"], "sao paulo": ["sp"], "belo horizonte": ["bh"]}
MIN_LINHAS = 1

CONSULTA_OFERTA = """
    select coalesce(cidade, ''), coalesce(bairro, ''), count(*) from espacos_reais
    where revisao_status = 'publicado' and not coalesce(pausado, false) and not coalesce(revisao_oculto, false)
    group by 1, 2"""


def normal(t):
    t = unicodedata.normalize("NFKD", (t or "").lower())
    return " ".join("".join(c for c in t if not unicodedata.combining(c)).split())


def _bate(consulta, termo):
    return f" {termo} " in f" {consulta} "


def cruzar_local(oferta, consultas):
    """oferta: (cidade, bairro, n); consultas: (texto, aparicoes, cliques).
    Devolve [local, tipo, aparicoes, cliques, espacos, leitura] ordenado por aparicoes."""
    locais = {}
    for cidade, bairro, n in oferta:
        for nome, tipo in ((cidade, "Cidade"), (bairro, "Bairro")):
            if nome and fl.seguro(nome):
                item = locais.setdefault(normal(nome), {"nome": nome, "tipo": tipo, "espacos": 0})
                item["espacos"] += n
    for nome in LOCAIS_COM_DEMANDA:
        locais.setdefault(normal(nome), {"nome": nome, "tipo": "Bairro ou regiao", "espacos": 0})
    linhas = []
    for chave, item in locais.items():
        termos = [chave] + APELIDOS.get(chave, [])
        bate = [c for c in consultas if any(_bate(normal(c[0]), t) for t in termos)]
        ap, cl = sum(c[1] for c in bate), sum(c[2] for c in bate)
        if not ap and not item["espacos"]:
            continue
        leitura = ("Busca e oferta" if ap and item["espacos"] else
                   "Busca sem espaco no guia" if ap else "Espaco sem busca visivel")
        linhas.append([item["nome"], item["tipo"], ap, cl, item["espacos"], leitura])
    return sorted(linhas, key=lambda l: (-l[2], -l[4], l[0]))


def blocos(painel, fh, canais, oferta, consultas):
    linhas = cruzar_local(oferta, consultas)
    if linhas:
        painel.linha(dp.Tabela("Busca no Google x espacos no guia, por local (ultimos 28 dias, so buscas visiveis)",
                               ["Local", "Tipo", "Aparicoes", "Cliques", "Espacos no guia", "Leitura"], linhas,
                               formatos=[None, None, dp.FMT_INT, dp.FMT_INT, dp.FMT_INT, None], gradiente=[2]))
    painel.linha(fl.por_dimensao("Hunter por canal: quem chega ao cadastro (periodo A)", fh, "canal", canais, [
        ("Captados", "soma", "captados"), ("Responderam", "taxa", "responderam"),
        ("Clicaram no link", "taxa", "clicou"), ("Cadastraram", "soma", "cadastrou"),
        ("Cadastro / captado", "taxa", "cadastrou")], gradiente=[4], cabecalho_dim="Canal"))
    painel.linha(dp.Tabela("Custo por anfitriao", ["Canal pago", "Situacao"], [
            ["Meta (anuncios)", "Entra com a campanha"],
            ["Google Ads", "Sem campanha"]]))
