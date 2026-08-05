# -*- coding: utf-8 -*-
"""
Regua dos proponentes sem projeto cadastrado (segmento S4).

A spec descrevia isto como "649 sem projeto, nunca acessou". A medicao de
05/08 derrubou a premissa e reduziu o alvo de ~700 para 42:

  659 dos 686 tem `legacyId` e nasceram em maio. Foram MIGRADOS em lote, nao
  se cadastraram. Nao e gente que abandonou um cadastro.

  So 7 tinham projeto na plataforma antiga, e cinco desses tem projeto que ja
  encerrou (2024/2025). Sobram 2 pessoas com projeto ainda em execucao fora da
  plataforma nova.

  Nao existe o que oferecer aos frios: a plataforma tem 1 edital, chamado
  "Edital Teste", e 9 incentivadores. Convidar 637 pessoas que nunca entraram
  seria convite para sala vazia. Ficam de fora por decisao (05/08), e voltam
  quando houver demanda real.

Duas variantes:
  A (2)  — tem projeto VIGENTE na base antiga que nao esta na plataforma nova.
           Cita o projeto pelo nome e a data de fim de execucao.
  B (40) — acessou a plataforma e nunca cadastrou projeto.

O QUE A COPY NAO FAZ, e este e o ponto mais importante desta regua: nao cita
quantidade de incentivador, nao diz que "empresas estao procurando projetos
como o seu" e nao menciona edital. Com 9 incentivadores e 1 edital de teste,
qualquer uma dessas frases seria promessa que a plataforma nao cumpre. O que
e verdade e sustenta a mensagem e que o time comercial da Brada leva projeto
aprovado a patrocinador — isso e o negocio, nao projecao.

Uso:
    python regua_sem_projeto.py                      # dry-run
    python regua_sem_projeto.py --checar-supressao   # dry-run + LeadLovers
    python regua_sem_projeto.py --apply --limite 1   # canario
    python regua_sem_projeto.py --apply --checar-supressao
"""
import argparse, csv, datetime as dt, os, pathlib, sys, time

from google.cloud import firestore

import fonte_antiga
import sync
from regua_expiracao import (
    conectar, mascarar, _primeiro_nome, PAUSA_ENTRE_ENVIOS_S, PLATAFORMA_URL,
)
from regua_rascunho import carregar_token_leadlovers, esta_suprimido
from filtros import (
    motivo_exclusao, indice_login, dias_desde_login, uids_tocados,
    exigir_checagem_supressao, registrar_supressao, motivo_ja_resolvido,
)

COL_CONTROLE = "regua_sem_projeto_envios"
MARCA_ORIGEM = "regua_sem_projeto"

# Quem acessou nos ultimos dias pode estar cadastrando o projeto agora. Dizer
# "voce nao cadastrou nenhum projeto" pra quem esta com a tela aberta e um
# e-mail que chega errado. Vale so pra variante B: a variante A fala de um
# projeto que ficou fora da plataforma, e isso independe de quando a pessoa
# acessou pela ultima vez.
DIAS_MIN_SEM_ACESSO = 3


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


def montar_copy_nao_migrado(nome_pessoa, projetos):
    """projetos: [(titulo, date_fim_execucao)] — so os vigentes."""
    saudacao = f"Olá, {_primeiro_nome(nome_pessoa)}" if nome_pessoa else "Olá"
    n = len(projetos)

    if n == 1:
        titulo, fim = projetos[0]
        assunto = f"O projeto {titulo} não está na plataforma nova da Brada"
        abertura = (f"Em maio a plataforma da Brada mudou. Sua conta veio para a plataforma nova, "
                    f"mas o projeto <strong>{titulo}</strong> não está nela.")
    else:
        assunto = f"Seus {n} projetos não estão na plataforma nova da Brada"
        abertura = (f"Em maio a plataforma da Brada mudou. Sua conta veio para a plataforma nova, "
                    f"mas <strong>{n} projetos</strong> seus não estão nela.")

    itens = "".join(
        f'<li style="margin-bottom:4px">{t} — execução até '
        f'<strong>{f.strftime("%d/%m/%Y")}</strong></li>' for t, f in projetos
    )
    bloco = (f'<div style="background-color:#fff7ed;border:1px solid #fed7aa;border-radius:10px;'
             f'padding:16px;margin-bottom:12px">'
             f'<ul style="margin:0;padding-left:20px;color:#7c2d12">{itens}</ul></div>')

    fecho = ("Como a execução ainda está em curso, dá tempo de "
             + ("ele voltar" if n == 1 else "eles voltarem") +
             " a aparecer para as empresas que procuram projetos para patrocinar. "
             "Cadastrar de novo leva alguns minutos.")

    html = (_HEADER
            + f'<p style="margin-top: 0; font-size: 16px; font-weight: 700; color: #0f172a;">{saudacao},</p>'
            + f'<p style="margin: 16px 0;">{abertura}</p>'
            + bloco
            + f'<p style="margin: 16px 0;">{fecho}</p>'
            + _botao("Cadastrar meu projeto")
            + _RODAPE).strip()
    return assunto, html


def montar_copy_sem_projeto(nome_pessoa):
    saudacao = f"Olá, {_primeiro_nome(nome_pessoa)}" if nome_pessoa else "Olá"
    assunto = "Sua conta está ativa, mas nenhum projeto seu aparece para os patrocinadores"

    corpo = [
        "Você tem conta na plataforma da Brada, mas ainda não cadastrou nenhum projeto.",
        # A unica promessa que a plataforma cumpre hoje. Nada de "empresas estao
        # procurando projetos como o seu": sao 9 incentivadores e 1 edital de teste.
        "Se você tem projeto aprovado em lei de incentivo, é o cadastro que coloca ele "
        "na frente das empresas com quem o nosso time comercial trabalha.",
        "O cadastro pede o básico do projeto: descrição, orçamento, prazo de captação e "
        "o arquivo do Diário Oficial.",
    ]

    html = (_HEADER
            + f'<p style="margin-top: 0; font-size: 16px; font-weight: 700; color: #0f172a;">{saudacao},</p>'
            + "".join(f'<p style="margin: 16px 0;">{p}</p>' for p in corpo)
            + _botao("Cadastrar meu projeto")
            + _RODAPE).strip()
    return assunto, html


# --------------------------------------------------------------------------- #
# Dados
# --------------------------------------------------------------------------- #
def coletar(db, hoje, idx_login):
    """Devolve (fila, dono_de_projeto). Cada item traz a variante ja decidida."""
    antiga = fonte_antiga.projetos_por_pessoa(sync.get_sheets_client())

    users = {d.id: (d.to_dict() or {}) for d in db.collection("users").stream()}
    dono_de_projeto, com_projeto = {}, set()
    for d in db.collection("projects").stream():
        dono = str((d.to_dict() or {}).get("ownerId") or "")
        dono_de_projeto[d.id] = dono
        com_projeto.add(dono)

    candidatos = []
    for uid, u in users.items():
        role = str(u.get("role") or "").strip()
        if role and role.upper() != "ONG":
            continue
        if uid in com_projeto:
            continue

        lid = str(u.get("legacyId") or "").strip()
        # `_parse_br` devolve date, None ou a string 'INVALIDA' (existe um typo
        # de ano 7202 na fonte) — por isso o isinstance, e nao `if fim`.
        vigentes = [(nome, fim) for nome, fim in antiga.get(lid, [])
                    if isinstance(fim, dt.date) and fim >= hoje]

        dias = dias_desde_login(uid, idx_login, hoje)
        if vigentes:
            variante = "nao_migrado"
        elif dias is not None and dias >= DIAS_MIN_SEM_ACESSO:
            variante = "sem_projeto"
        elif dias is not None:
            continue  # acessou nos ultimos dias: pode estar cadastrando agora
        else:
            # Frio: nunca acessou e nao tem projeto vigente na base antiga.
            # Fora do escopo por decisao de 05/08 — nao ha o que oferecer.
            continue

        candidatos.append({
            "id": uid,
            "email": str(u.get("email") or "").strip(),
            "nome": str(u.get("name") or u.get("displayName") or "").strip(),
            "verificado": bool(u.get("emailVerified", True)),
            "variante": variante,
            "projetos": vigentes,
            "dias_sem_acesso": dias,
        })
    return candidatos, dono_de_projeto


# --------------------------------------------------------------------------- #
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--limite", type=int, default=0, help="canario: envia no maximo N")
    ap.add_argument("--so-email", default="")
    ap.add_argument("--pausa", type=float, default=None)
    ap.add_argument("--checar-supressao", action="store_true",
                    help="consulta o LeadLovers e pula quem pediu descadastro (412)")
    ap.add_argument("--sem-checagem", action="store_true",
                    help="dispara sem consultar o descadastro (saida de emergencia)")
    ap.add_argument("--hoje", default="", help="simula outra data, AAAA-MM-DD")
    args = ap.parse_args()
    exigir_checagem_supressao(args)

    hoje = dt.date.fromisoformat(args.hoje) if args.hoje else dt.date.today()
    pausa = PAUSA_ENTRE_ENVIOS_S if args.pausa is None else args.pausa
    modo = "APPLY (ENVIA)" if args.apply else "DRY-RUN (nao envia)"
    print(f"=== REGUA DOS SEM PROJETO — {modo} ===")
    print(f"  data de referencia: {hoje}")

    db = conectar()
    idx_login = indice_login()
    candidatos, dono_de_projeto = coletar(db, hoje, idx_login)
    print(f"  candidatos: {len(candidatos)}")
    for v in ("nao_migrado", "sem_projeto"):
        print(f"    {v}: {sum(1 for c in candidatos if c['variante'] == v)}")

    controle = {d.id: (d.to_dict() or {}) for d in db.collection(COL_CONTROLE).stream()}
    n_env = sum(1 for r in controle.values() if not r.get("suprimido"))
    tocados = uids_tocados(db, hoje=hoje, dono_de_projeto=dono_de_projeto)
    print(f"  ja resolvidos: {len(controle)} ({n_env} enviados, "
          f"{len(controle) - n_env} descadastrados)   |   "
          f"tocados por outra regua (14d): {len(tocados)}")

    token = carregar_token_leadlovers() if args.checar_supressao else ""
    if args.checar_supressao and not token:
        sys.exit("ERRO: --checar-supressao pedido mas LEADLOVERS_TOKEN nao encontrado.")

    fila, pulados = [], {}
    def pular(motivo):
        pulados[motivo] = pulados.get(motivo, 0) + 1

    for c in candidatos:
        if c["id"] in controle:
            pular(motivo_ja_resolvido(controle[c["id"]])); continue
        if c["id"] in tocados:
            pular(f"tocado por {tocados[c['id']]}"); continue
        motivo = motivo_exclusao(c["email"], c["nome"])
        if motivo:
            pular(motivo); continue
        if not c["verificado"]:
            pular("e-mail nao verificado"); continue
        if args.so_email and c["email"].lower() != args.so_email.lower():
            pular("fora do --so-email"); continue
        fila.append(c)

    # Quem tem projeto vigente parado fora da plataforma vai primeiro: e o toque
    # de maior valor e o unico que perde validade com o tempo.
    fila.sort(key=lambda c: (c["variante"] != "nao_migrado", c["email"]))
    if args.limite:
        fila = fila[:args.limite]

    if args.checar_supressao:
        total = len(fila)
        print(f"\n  checando descadastro no LeadLovers ({total} e-mails, "
              f"~{max(1, round(total * 0.6 / 60))} min)...")
        print("  nada foi enviado ainda; cancelar aqui e seguro.")
        limpa, indefinidos = [], 0
        for i, c in enumerate(fila, start=1):
            s = esta_suprimido(c["email"], token)
            if s is True:
                pular("descadastrado (LGPD)")
                # So grava no modo real: dry-run nao escreve nada no Firestore.
                if args.apply:
                    registrar_supressao(db, COL_CONTROLE, c["id"])
            elif s is None:
                indefinidos += 1
                pular("supressao indefinida (API nao respondeu)")
            else:
                limpa.append(c)
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

    win = pathlib.Path(r"C:\Users\bruno\Documents\Brada\scripts\logs\regua_sem_projeto")
    logdir = win if os.name == "nt" else pathlib.Path("logs/regua_sem_projeto")
    logdir.mkdir(parents=True, exist_ok=True)
    logfile = logdir / f"{'envio' if args.apply else 'dryrun'}_{hoje.strftime('%Y%m%d')}.csv"

    print("\n  --- FILA ---")
    with open(logfile, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["owner_id", "email_mascarado", "variante", "dias_sem_acesso",
                    "projetos_nao_migrados", "assunto", "enviado"])
        for c in fila:
            if c["variante"] == "nao_migrado":
                assunto, html = montar_copy_nao_migrado(c["nome"], c["projetos"])
            else:
                assunto, html = montar_copy_sem_projeto(c["nome"])

            enviado = False
            if args.apply:
                db.collection("mail").add({
                    "to": c["email"],
                    "createdAt": firestore.SERVER_TIMESTAMP,
                    "message": {"subject": assunto, "html": html},
                    "origem": MARCA_ORIGEM,
                })
                db.collection(COL_CONTROLE).document(c["id"]).set({
                    "variante": c["variante"],
                    "nProjetos": len(c["projetos"]),
                    "diasSemAcesso": c["dias_sem_acesso"],
                    "enviadoEm": firestore.SERVER_TIMESTAMP,
                })
                enviado = True
                time.sleep(pausa)

            d = c["dias_sem_acesso"]
            print(f"    [{c['variante']:>12}] {mascarar(c['email']):<30} "
                  f"{'nunca' if d is None else str(d) + 'd':>6}  {assunto[:48]}")
            w.writerow([c["id"], mascarar(c["email"]), c["variante"],
                        "nunca" if d is None else d,
                        " | ".join(t for t, _ in c["projetos"]), assunto, enviado])

    print(f"\n  log: {logfile}")
    print("\n  DRY-RUN: nada enviado. Use --apply." if not args.apply
          else f"\n  ENVIADOS: {len(fila)}")


if __name__ == "__main__":
    sys.exit(main())
