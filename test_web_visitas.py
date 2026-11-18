import datetime

import dashboard_painel as dp
import painel_filtros as fl
import web_visitas as w

D = datetime.date(2026, 11, 17)


def test_canal_agrupa_origens():
    assert w.canal("google", "organic") == "Google organico"
    assert w.canal("(direct)", "(none)") == "Direto"
    assert w.canal("meta", "cpc") == "Anuncio"
    assert w.canal("meta", "paid_social") == "Anuncio"
    assert w.canal("ig", "social") == "Redes sociais"
    assert w.canal("leadlovers", "email") == "E-mail"
    assert w.canal("chatgpt.com", "ai-assistant") == "IA"
    assert w.canal("(not set)", "(not set)") == "Outros"


def test_tipo_de_busca_marca_vence_generica():
    marca = w.PRODUTOS["pf"]["marca"]
    assert w.tipo_busca("espaco para evento rio de janeiro", marca) == "Generica"
    assert w.tipo_busca("primeira fila espaco", marca) == "Marca ou nome"
    assert w.tipo_busca("casarao floresta", marca) == "Marca ou nome"


def test_resto_oculto_fecha_o_total_do_dia():
    por_busca = [(D, "Generica", 10, 1, 5.0)]
    totais = [{"keys": [D.isoformat()], "impressions": 30, "clicks": 3, "position": 6.0}]
    (d, tipo, imp, cli, pos), = w.resto_oculto(por_busca, totais)
    assert (tipo, imp, cli) == (w.OCULTA, 20, 2)
    assert abs(pos - 6.5) < 1e-9


def test_blocos_viram_bases_e_linhas_vivas():
    dados = {"ga": [(D, "Direto", [3, 2, 1, 1])], "gsc": [(D, "Generica", 10, 1, 5.0)],
             "top": [["espaco para festa", 10, 1, 5.0, "Generica"]], "erros": []}
    (bv, lv), (bb, lb) = w.linhas_bases("pf", dados)
    assert lv[0][1:] == ["Direto", 3, 2, 1, 1] and lb[0][1:] == ["Generica", 10, 1, 500]
    ctl = fl.controles("Primeira Fila", D)
    p = dp.Painel("Primeira Fila", "t", "s")
    w.blocos(p, ctl, "pf", dados)
    titulos = [getattr(c, "titulo", "") for linha in p.linhas for c in linha]
    assert "Sessoes no site" in titulos and "Posicao media" in titulos
    assert any(t.startswith("Buscas que mais") for t in titulos)


def test_blocos_sem_dado_mostram_aviso():
    p = dp.Painel("AprovAI", "t", "s")
    w.blocos(p, fl.controles("AprovAI", D), "aprovai", {"ga": [], "gsc": [], "top": [], "erros": []})
    assert len(p.linhas) == 2
