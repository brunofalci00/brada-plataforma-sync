# -*- coding: utf-8 -*-
"""
Regua da vitrine — quem tem projeto publicado e vigente, mas sumiu.

Segmento S6 do plano de retomada: 125 donos com pelo menos um projeto
publicado e dentro do prazo, sem acessar a plataforma ha 30 dias ou mais
(89 nunca acessaram). E o segmento de maior valor por e-mail: o ativo ja
existe e ja esta exposto aos patrocinadores.

O que a medicao de 05/08 mostrou e que definiu a copy:

  108 dos 125 tem projeto vigente POReM INCOMPLETO. Ele aparece na vitrine
  sem descricao e sem orcamento, entao o patrocinador encontra o projeto e
  nao consegue avaliar. Isso e verdade, e verificavel e e acionavel — que e
  tudo que uma copy precisa.

  Os outros 17 estao completos. Pra esses nao ha o que corrigir: o pedido e
  confirmar que os dados seguem valendo.

> AVISO EMBUTIDO NA COPY (variante A)
> Projeto publicado e incompleto existe porque a migracao escreveu direto no
> Firestore, sem passar pelo formulario. O front recalcula o status a cada
> save: se faltar um campo obrigatorio, o projeto VOLTA PRA RASCUNHO e sai da
> vitrine (Projects.tsx:153). Ou seja, quem preencher so metade do que a
> gente pediu fica pior do que estava. A copy avisa disso em uma frase.
> A correcao que explica isso na interface (T2) esta mergeada e nao deployada.

Uso:
    python regua_vitrine.py                          # dry-run
    python regua_vitrine.py --checar-supressao       # dry-run + LeadLovers
    python regua_vitrine.py --apply --limite 1       # canario
    python regua_vitrine.py --apply --checar-supressao
"""
import argparse, csv, datetime as dt, os, pathlib, sys, time

from google.cloud import firestore

from regua_expiracao import (
    conectar, mascarar, parse_data, _primeiro_nome,
    PAUSA_ENTRE_ENVIOS_S, PLATAFORMA_URL,
)
from regua_rascunho import campos_faltando, carregar_token_leadlovers, esta_suprimido
from filtros import (
    e_texto_de_teste, motivo_exclusao, indice_login, dias_desde_login, uids_tocados,
)

COL_CONTROLE = "regua_vitrine_envios"
MARCA_ORIGEM = "regua_vitrine"

# Quem acessou nos ultimos 30 dias nao esta sumido: nao recebe.
DIAS_INATIVO = 30


# --------------------------------------------------------------------------- #
# Copy
# --------------------------------------------------------------------------- #
_HEADER = """
<div style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif; font-size: 14px; color: #1e293b; max-width: 600px; margin: 0 auto; line-height: 1.6;">
  <div style="background-color: #ea580c; padding: 30px; border-radius: 16px 16px 0 0; text-align: center;">
    <h1 style="color: #ffffff; margin: 0; font-size: 28px; font-weight: 800; letter-spacing: -0.025em;">Brada</h1>
    <p style="color: #ffedd5; margin: 6px 0 0 0; font-size: 14px; font-weight: 500;">Conectando impacto e sustentabilidade</p>
  </div>
  <div style="padding: 32px; border: 1px solid #e2e8f0; border-top: none; border-radius: 0 0 16px 16px; background-color: #ffffff; box-shadow: 0 4px 12px rgba(0,0,0,0.03);">
"""

_RODAPE = """
    <p style="margin: 16px 0; font-size: 13px; color: #64748b;">
      Se não lembrar a senha, use a opção <strong>Esqueci minha senha</strong> na tela de acesso.
      Seu cadastro continua ativo e o e-mail é o mesmo de sempre.
    </p>
    <p style="margin: 24px 0 0 0; padding-top: 16px; border-top: 1px solid #e2e8f0; font-size: 13px; color: #64748b;">
      Qualquer dúvida, é só responder este e-mail.<br>Time Brada
    </p>
  </div>
</div>
"""


def _botao(texto):
    return (f'<div style="text-align: center; margin: 32px 0;">'
            f'<a href="{PLATAFORMA_URL}/login" style="background-color: #ea580c; color: #ffffff; '
            f'padding: 14px 28px; border-radius: 10px; text-decoration: none; font-weight: 700; '
            f'display: inline-block;">{texto}</a></div>')


def montar_copy_incompleto(nome_pessoa, projetos):
    """projetos: [(titulo, [campos que faltam])] — so os incompletos."""
    saudacao = f"Olá, {_primeiro_nome(nome_pessoa)}" if nome_pessoa else "Olá"
    n = len(projetos)

    if n == 1:
        titulo, falta = projetos[0]
        assunto = f"O projeto {titulo} está visível, mas incompleto"
        abertura = (f"O projeto <strong>{titulo}</strong> está publicado na plataforma da Brada e "
                    f"as empresas que procuram projetos para patrocinar conseguem encontrá-lo.")
    else:
        assunto = f"Seus {n} projetos estão visíveis, mas incompletos"
        abertura = (f"Você tem <strong>{n} projetos</strong> publicados na plataforma da Brada e "
                    f"as empresas que procuram projetos para patrocinar conseguem encontrá-los.")

    consequencia = ("Só que falta parte das informações que uma empresa olha antes de decidir. "
                    "Do jeito que está, quem abre o projeto não consegue avaliar.")

    blocos = []
    for titulo, falta in projetos:
        itens = "".join(f'<li style="margin-bottom:4px">{c}</li>' for c in falta)
        cabecalho = (f'<p style="margin:0 0 6px 0;font-weight:700;color:#0f172a">{titulo}</p>'
                     if n > 1 else "")
        blocos.append(
            f'<div style="background-color:#fff7ed;border:1px solid #fed7aa;border-radius:10px;'
            f'padding:16px;margin-bottom:12px">{cabecalho}'
            f'<ul style="margin:0;padding-left:20px;color:#7c2d12">{itens}</ul></div>'
        )

    # A frase mais importante do e-mail. Sem ela, quem salvar pela metade tira o
    # proprio projeto da vitrine sem entender o motivo.
    aviso = ("Vale preencher tudo de uma vez: a plataforma só mantém o projeto na vitrine "
             "quando o cadastro está completo.")

    html = (_HEADER
            + f'<p style="margin-top: 0; font-size: 16px; font-weight: 700; color: #0f172a;">{saudacao},</p>'
            + f'<p style="margin: 16px 0;">{abertura}</p>'
            + f'<p style="margin: 16px 0;">{consequencia}</p>'
            + "".join(blocos)
            + f'<p style="margin: 16px 0;">{aviso}</p>'
            + _botao("Completar meu projeto")
            + _RODAPE).strip()
    return assunto, html


def montar_copy_completo(nome_pessoa, projetos):
    """projetos: [(titulo, data_expiracao|None)] — todos completos."""
    saudacao = f"Olá, {_primeiro_nome(nome_pessoa)}" if nome_pessoa else "Olá"
    n = len(projetos)

    if n == 1:
        titulo, exp = projetos[0]
        assunto = f"Confirme se os dados do projeto {titulo} seguem valendo"
        abertura = (f"O projeto <strong>{titulo}</strong> está publicado e completo na plataforma "
                    f"da Brada. As empresas conseguem encontrá-lo e avaliar.")
    else:
        assunto = f"Confirme se os dados dos seus {n} projetos seguem valendo"
        abertura = (f"Seus <strong>{n} projetos</strong> estão publicados e completos na plataforma "
                    f"da Brada. As empresas conseguem encontrá-los e avaliar.")

    linhas = []
    for titulo, exp in projetos:
        prazo = (f'prazo final de captação em <strong>{exp.strftime("%d/%m/%Y")}</strong>'
                 if exp else "sem prazo final de captação preenchido")
        linhas.append(f'<li style="margin-bottom:4px">{titulo} — {prazo}</li>')

    bloco = (f'<div style="background-color:#fff7ed;border:1px solid #fed7aa;border-radius:10px;'
             f'padding:16px;margin-bottom:12px">'
             f'<ul style="margin:0;padding-left:20px;color:#7c2d12">{"".join(linhas)}</ul></div>')

    pedido = ("Como faz um tempo que você não entra, vale conferir se continua tudo certo. "
              "Se o prazo foi prorrogado, se o valor mudou ou se o projeto já foi captado, "
              "atualizar leva um minuto.")

    html = (_HEADER
            + f'<p style="margin-top: 0; font-size: 16px; font-weight: 700; color: #0f172a;">{saudacao},</p>'
            + f'<p style="margin: 16px 0;">{abertura}</p>'
            + bloco
            + f'<p style="margin: 16px 0;">{pedido}</p>'
            + _botao("Conferir meu projeto")
            + _RODAPE).strip()
    return assunto, html


# --------------------------------------------------------------------------- #
# Dados
# --------------------------------------------------------------------------- #
def coletar(db, hoje, idx_login):
    """Donos com projeto publicado e vigente, sem acesso ha DIAS_INATIVO ou mais."""
    users = {d.id: (d.to_dict() or {}) for d in db.collection("users").stream()}

    por_dono, dono_de_projeto = {}, {}
    for d in db.collection("projects").stream():
        p = d.to_dict() or {}
        dono_de_projeto[d.id] = str(p.get("ownerId") or "")

        if str(p.get("status") or "").strip() == "Rascunho":
            continue
        titulo = str(p.get("title") or "seu projeto").strip()
        if e_texto_de_teste(titulo):
            continue
        # Vigente: prazo no futuro, ou sem prazo (mesma regra do filtro da
        # vitrine no matchmaking — projeto sem data preenchida segue visivel).
        exp = parse_data(p.get("cacExpirationDate"))
        if exp and (exp - hoje).days < 0:
            continue

        dono_id = dono_de_projeto[d.id]
        dono = users.get(dono_id)
        if not dono:
            continue
        reg = por_dono.setdefault(dono_id, {
            "id": dono_id,
            "email": str(dono.get("email") or "").strip(),
            "nome": str(dono.get("name") or dono.get("displayName") or "").strip(),
            "verificado": bool(dono.get("emailVerified", True)),
            "incompletos": [],
            "completos": [],
        })
        falta = campos_faltando(p)
        if falta:
            reg["incompletos"].append((titulo, falta))
        else:
            reg["completos"].append((titulo, exp))

    # So quem sumiu. dias None = nunca acessou, que tambem conta.
    sumidos = {}
    for uid, reg in por_dono.items():
        d = dias_desde_login(uid, idx_login, hoje)
        if d is None or d >= DIAS_INATIVO:
            reg["dias_sem_acesso"] = d
            sumidos[uid] = reg
    return sumidos, dono_de_projeto


# --------------------------------------------------------------------------- #
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--limite", type=int, default=0, help="canario: envia no maximo N")
    ap.add_argument("--so-email", default="")
    ap.add_argument("--pausa", type=float, default=None)
    ap.add_argument("--checar-supressao", action="store_true",
                    help="consulta o LeadLovers e pula quem pediu descadastro (412)")
    ap.add_argument("--hoje", default="", help="simula outra data, AAAA-MM-DD")
    args = ap.parse_args()

    hoje = dt.date.fromisoformat(args.hoje) if args.hoje else dt.date.today()
    pausa = PAUSA_ENTRE_ENVIOS_S if args.pausa is None else args.pausa
    modo = "APPLY (ENVIA)" if args.apply else "DRY-RUN (nao envia)"
    print(f"=== REGUA DA VITRINE — {modo} ===")
    print(f"  data de referencia: {hoje}  |  inativo = {DIAS_INATIVO}+ dias sem acessar")

    db = conectar()
    idx_login = indice_login()
    print(f"  usuarios com login no Auth: {sum(1 for v in idx_login.values() if v)}")

    por_dono, dono_de_projeto = coletar(db, hoje, idx_login)
    print(f"  donos com projeto vigente e sem acesso: {len(por_dono)}")
    print(f"    com projeto incompleto: {sum(1 for r in por_dono.values() if r['incompletos'])}")
    print(f"    so com projeto completo: {sum(1 for r in por_dono.values() if not r['incompletos'])}")

    ja = {d.id for d in db.collection(COL_CONTROLE).stream()}
    tocados = uids_tocados(db, hoje=hoje, dono_de_projeto=dono_de_projeto)
    print(f"  envios ja registrados: {len(ja)}   |   tocados por outra regua (14d): {len(tocados)}")

    token = carregar_token_leadlovers() if args.checar_supressao else ""
    if args.checar_supressao and not token:
        sys.exit("ERRO: --checar-supressao pedido mas LEADLOVERS_TOKEN nao encontrado.")

    fila, pulados = [], {}
    def pular(motivo):
        pulados[motivo] = pulados.get(motivo, 0) + 1

    for dono_id, reg in por_dono.items():
        if dono_id in ja:
            pular("ja enviado"); continue
        if dono_id in tocados:
            pular(f"tocado por {tocados[dono_id]}"); continue
        motivo = motivo_exclusao(reg["email"], reg["nome"])
        if motivo:
            pular(motivo); continue
        if not reg["verificado"]:
            pular("e-mail nao verificado"); continue
        if args.so_email and reg["email"].lower() != args.so_email.lower():
            pular("fora do --so-email"); continue
        fila.append(reg)

    # Quem esta a menos campos de resolver vai primeiro: se algo der errado no
    # meio do lote, o que sobra e o mais dificil, nao o mais facil.
    fila.sort(key=lambda r: sum(len(f) for _, f in r["incompletos"]))
    if args.limite:
        fila = fila[:args.limite]

    if args.checar_supressao:
        total = len(fila)
        print(f"\n  checando descadastro no LeadLovers ({total} e-mails, "
              f"~{max(1, round(total * 0.6 / 60))} min)...")
        print("  nada foi enviado ainda; cancelar aqui e seguro.")
        limpa, indefinidos = [], 0
        for i, reg in enumerate(fila, start=1):
            s = esta_suprimido(reg["email"], token)
            if s is True:
                pular("descadastrado (LGPD)")
            elif s is None:
                indefinidos += 1
                pular("supressao indefinida (API nao respondeu)")
            else:
                limpa.append(reg)
            if i % 25 == 0 or i == total:
                print(f"    {i}/{total} checados · {len(limpa)} liberados · "
                      f"{i - len(limpa) - indefinidos} descadastrados", flush=True)
            time.sleep(0.3)
        fila = limpa
        if indefinidos:
            print(f"    {indefinidos} sem resposta da API — pulados por precaucao")

    print(f"\n  A ENVIAR: {len(fila)}   |   pulados: {sum(pulados.values())}")
    for m, n in sorted(pulados.items(), key=lambda x: -x[1]):
        print(f"      {m}: {n}")

    win = pathlib.Path(r"C:\Users\bruno\Documents\Brada\scripts\logs\regua_vitrine")
    logdir = win if os.name == "nt" else pathlib.Path("logs/regua_vitrine")
    logdir.mkdir(parents=True, exist_ok=True)
    logfile = logdir / f"{'envio' if args.apply else 'dryrun'}_{hoje.strftime('%Y%m%d')}.csv"

    print("\n  --- FILA ---")
    with open(logfile, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["owner_id", "email_mascarado", "variante", "dias_sem_acesso",
                    "n_projetos", "campos_faltando", "assunto", "enviado"])
        for reg in fila:
            if reg["incompletos"]:
                variante = "incompleto"
                assunto, html = montar_copy_incompleto(reg["nome"], reg["incompletos"])
                projs = reg["incompletos"]
                faltas = sorted({c for _, f in projs for c in f})
            else:
                variante = "completo"
                assunto, html = montar_copy_completo(reg["nome"], reg["completos"])
                projs = reg["completos"]
                faltas = []

            enviado = False
            if args.apply:
                db.collection("mail").add({
                    "to": reg["email"],
                    "createdAt": firestore.SERVER_TIMESTAMP,
                    "message": {"subject": assunto, "html": html},
                    "origem": MARCA_ORIGEM,
                })
                db.collection(COL_CONTROLE).document(reg["id"]).set({
                    "variante": variante,
                    "nProjetos": len(projs),
                    "diasSemAcesso": reg["dias_sem_acesso"],
                    "enviadoEm": firestore.SERVER_TIMESTAMP,
                })
                enviado = True
                time.sleep(pausa)

            dias = reg["dias_sem_acesso"]
            print(f"    [{variante:>10}] {mascarar(reg['email']):<28} "
                  f"{'nunca' if dias is None else str(dias) + 'd':>6}  {assunto[:52]}")
            w.writerow([reg["id"], mascarar(reg["email"]), variante,
                        "nunca" if dias is None else dias, len(projs),
                        " | ".join(faltas), assunto, enviado])

    print(f"\n  log: {logfile}")
    print("\n  DRY-RUN: nada enviado. Use --apply." if not args.apply
          else f"\n  ENVIADOS: {len(fila)}")


if __name__ == "__main__":
    sys.exit(main())
