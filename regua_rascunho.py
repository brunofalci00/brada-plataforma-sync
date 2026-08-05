# -*- coding: utf-8 -*-
"""
Regua de retomada dos projetos em Rascunho — plataforma Brada.

Diagnostico de 04/08: 354 projetos estao em Rascunho e 352 deles travam nos
MESMOS 3 campos (Diario Oficial 100%, Descricao 99%, Orcamento 99%), porque a
migracao nao trouxe esses dados. Nao sao 354 problemas: e um problema repetido.
235 projetos estao a 1-3 campos de aparecer para os patrocinadores.

Por isso a copy nao diz "complete seu cadastro": diz exatamente o que falta em
cada projeto, por nome. Um e-mail por DONO, listando os projetos dele.

Canal: colecao `mail` (extensao firestore-send-email), igual a regua de
expiracao. A mensagem e sobre o ativo da propria pessoa, nao e campanha
publicitaria — mas ainda assim respeitamos a lista de descadastro do LeadLovers
antes de enviar (--checar-supressao).

Uso:
    python regua_rascunho.py                          # dry-run
    python regua_rascunho.py --checar-supressao       # dry-run + checa LeadLovers
    python regua_rascunho.py --apply --limite 1       # canario
    python regua_rascunho.py --apply --checar-supressao
"""
import argparse, csv, datetime as dt, os, pathlib, sys, time

from google.cloud import firestore

from regua_expiracao import (
    conectar, mascarar, e_interno, _primeiro_nome,
    MARCA_ORIGEM, PAUSA_ENTRE_ENVIOS_S, PLATAFORMA_URL,
)

COL_CONTROLE = "regua_rascunho_envios"

# Espelha isProjectComplete (src/pages/ong/Projects.tsx:474-492).
# A ordem importa: e a ordem em que a pessoa ve no formulario.
CAMPOS_OBRIGATORIOS = [
    ("title", "Nome do projeto"),
    ("description", "Descrição"),
    ("budget", "Orçamento estimado"),
    ("startDate", "Data de início"),
    ("category", "Categoria"),
    ("targetAudience", "Público-alvo"),
    ("location", "Localização"),
    ("ods", "ODS"),
    ("fundingSource", "Fonte de recurso"),
    ("cacExpirationDate", "Prazo final de captação"),
    ("diarioOficialUrl", "Arquivo do Diário Oficial"),
]


def vazio(v):
    if v is None:
        return True
    if isinstance(v, str):
        return not v.strip()
    if isinstance(v, (list, dict)):
        return len(v) == 0
    return False


def campos_faltando(p: dict):
    falta = [rotulo for chave, rotulo in CAMPOS_OBRIGATORIOS if vazio(p.get(chave))]
    alvo = p.get("targetAudience") or []
    if isinstance(alvo, list) and "Outros" in alvo and vazio(p.get("targetAudienceOther")):
        falta.append('Especificação do público-alvo "Outros"')
    return falta


# --------------------------------------------------------------------------- #
# Supressao (LGPD): quem pediu descadastro no LeadLovers nao recebe
# --------------------------------------------------------------------------- #
def carregar_token_leadlovers():
    caminho = os.path.expanduser(r"~/.brada-secrets/leadlovers.env")
    if os.environ.get("LEADLOVERS_TOKEN"):
        return os.environ["LEADLOVERS_TOKEN"]
    try:
        with open(caminho, encoding="utf-8-sig") as fh:
            for linha in fh:
                if linha.strip().startswith("LEADLOVERS_TOKEN"):
                    return linha.split("=", 1)[1].strip()
    except FileNotFoundError:
        pass
    return ""


def esta_suprimido(email: str, token: str):
    """True se o LeadLovers devolve 412 (descadastro/bounce). None se indefinido."""
    import requests
    try:
        r = requests.get("https://llapi.leadlovers.com/webapi/Lead",
                         params={"token": token, "email": email}, timeout=20)
        if r.status_code == 412:
            return True
        if r.status_code in (200, 404):
            return False
        return None
    except Exception:
        return None


# --------------------------------------------------------------------------- #
# Copy
# --------------------------------------------------------------------------- #
def montar_copy(nome_pessoa: str, projetos: list):
    """projetos: lista de (titulo, [campos que faltam])."""
    saudacao = f"Olá, {_primeiro_nome(nome_pessoa)}" if nome_pessoa else "Olá"
    n = len(projetos)

    if n == 1:
        titulo, falta = projetos[0]
        assunto = f"O projeto {titulo} ainda não aparece para os patrocinadores"
        abertura = (f"O projeto <strong>{titulo}</strong> está cadastrado na plataforma da Brada, "
                    f"mas ainda não aparece para as empresas que procuram projetos para patrocinar.")
        explicacao = ("Falta preencher o que está abaixo. São informações que não vieram na "
                      "migração da plataforma antiga."
                      if len(falta) <= 4 else
                      "Falta preencher o que está abaixo para ele ficar visível.")
    else:
        assunto = f"Seus {n} projetos ainda não aparecem para os patrocinadores"
        abertura = (f"Você tem <strong>{n} projetos</strong> cadastrados na plataforma da Brada que "
                    f"ainda não aparecem para as empresas que procuram projetos para patrocinar.")
        explicacao = ("Falta preencher o que está abaixo em cada um. São informações que não "
                      "vieram na migração da plataforma antiga.")

    blocos = []
    for titulo, falta in projetos:
        itens = "".join(
            f'<li style="margin-bottom:4px">{campo}</li>' for campo in falta
        )
        cabecalho = (f'<p style="margin:0 0 6px 0;font-weight:700;color:#0f172a">{titulo}</p>'
                     if n > 1 else "")
        blocos.append(
            f'<div style="background-color:#fff7ed;border:1px solid #fed7aa;border-radius:10px;'
            f'padding:16px;margin-bottom:12px">{cabecalho}'
            f'<ul style="margin:0;padding-left:20px;color:#7c2d12">{itens}</ul></div>'
        )

    html = f"""
<div style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif; font-size: 14px; color: #1e293b; max-width: 600px; margin: 0 auto; line-height: 1.6;">
  <div style="background-color: #ea580c; padding: 30px; border-radius: 16px 16px 0 0; text-align: center;">
    <h1 style="color: #ffffff; margin: 0; font-size: 28px; font-weight: 800; letter-spacing: -0.025em;">Brada</h1>
    <p style="color: #ffedd5; margin: 6px 0 0 0; font-size: 14px; font-weight: 500;">Conectando impacto e sustentabilidade</p>
  </div>
  <div style="padding: 32px; border: 1px solid #e2e8f0; border-top: none; border-radius: 0 0 16px 16px; background-color: #ffffff; box-shadow: 0 4px 12px rgba(0,0,0,0.03);">
    <p style="margin-top: 0; font-size: 16px; font-weight: 700; color: #0f172a;">{saudacao},</p>
    <p style="margin: 16px 0;">{abertura}</p>
    <p style="margin: 16px 0;">{explicacao}</p>
    {"".join(blocos)}
    <div style="text-align: center; margin: 32px 0;">
      <a href="{PLATAFORMA_URL}/login" style="background-color: #ea580c; color: #ffffff; padding: 14px 28px; border-radius: 10px; text-decoration: none; font-weight: 700; display: inline-block;">Completar meu projeto</a>
    </div>
    <p style="margin: 16px 0; font-size: 13px; color: #64748b;">
      Se não lembrar a senha, use a opção <strong>Esqueci minha senha</strong> na tela de acesso.
      Seu cadastro continua ativo e o e-mail é o mesmo de sempre.
    </p>
    <p style="margin: 24px 0 0 0; padding-top: 16px; border-top: 1px solid #e2e8f0; font-size: 13px; color: #64748b;">
      Qualquer dúvida, é só responder este e-mail.<br>Time Brada
    </p>
  </div>
</div>
""".strip()
    return assunto, html


# --------------------------------------------------------------------------- #
def coletar(db):
    """Agrupa os projetos em Rascunho por dono."""
    users = {d.id: (d.to_dict() or {}) for d in db.collection("users").stream()}
    por_dono = {}
    for d in db.collection("projects").stream():
        p = d.to_dict() or {}
        if str(p.get("status") or "").strip() != "Rascunho":
            continue
        dono_id = str(p.get("ownerId") or "")
        dono = users.get(dono_id)
        if not dono:
            continue
        reg = por_dono.setdefault(dono_id, {
            "email": str(dono.get("email") or "").strip(),
            "nome": str(dono.get("name") or dono.get("displayName") or "").strip(),
            "verificado": bool(dono.get("emailVerified", True)),
            "logou": bool(dono.get("lastLoginAt")),
            "projetos": [],
        })
        reg["projetos"].append((str(p.get("title") or "seu projeto").strip(), campos_faltando(p)))
    return por_dono


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--limite", type=int, default=0)
    ap.add_argument("--so-email", default="")
    ap.add_argument("--checar-supressao", action="store_true",
                    help="consulta o LeadLovers e pula quem pediu descadastro (412)")
    args = ap.parse_args()

    modo = "APPLY (ENVIA)" if args.apply else "DRY-RUN (nao envia)"
    print(f"=== REGUA DE RASCUNHO — {modo} ===")

    db = conectar()
    por_dono = coletar(db)
    print(f"  donos com projeto em Rascunho: {len(por_dono)}")
    print(f"  projetos em Rascunho: {sum(len(v['projetos']) for v in por_dono.values())}")

    ja = {d.id for d in db.collection(COL_CONTROLE).stream()}
    print(f"  envios ja registrados: {len(ja)}")

    token = carregar_token_leadlovers() if args.checar_supressao else ""
    if args.checar_supressao and not token:
        sys.exit("ERRO: --checar-supressao pedido mas LEADLOVERS_TOKEN nao encontrado.")

    fila, pulados = [], {}
    def pular(motivo):
        pulados[motivo] = pulados.get(motivo, 0) + 1

    for dono_id, reg in por_dono.items():
        if dono_id in ja:
            pular("ja enviado"); continue
        if not reg["email"]:
            pular("sem e-mail"); continue
        if e_interno(reg["email"]):
            pular("interno"); continue
        if not reg["verificado"]:
            pular("e-mail nao verificado"); continue
        if args.so_email and reg["email"].lower() != args.so_email.lower():
            pular("fora do --so-email"); continue
        reg["id"] = dono_id
        fila.append(reg)

    fila.sort(key=lambda r: sum(len(f) for _, f in r["projetos"]))
    if args.limite:
        fila = fila[:args.limite]

    if args.checar_supressao:
        print(f"\n  checando descadastro no LeadLovers ({len(fila)} e-mails)...")
        limpa = []
        indefinidos = 0
        for reg in fila:
            s = esta_suprimido(reg["email"], token)
            if s is True:
                pular("descadastrado (LGPD)")
            elif s is None:
                indefinidos += 1
                pular("supressao indefinida (API nao respondeu)")
            else:
                limpa.append(reg)
            time.sleep(0.3)
        fila = limpa
        if indefinidos:
            print(f"    {indefinidos} sem resposta da API — pulados por precaucao")

    print(f"\n  A ENVIAR: {len(fila)}   |   pulados: {sum(pulados.values())}")
    for m, n in sorted(pulados.items(), key=lambda x: -x[1]):
        print(f"      {m}: {n}")

    logdir = (pathlib.Path(r"C:\Users\bruno\Documents\Brada\scripts\logs\regua_rascunho")
              if os.name == "nt" else pathlib.Path("logs/regua_rascunho"))
    logdir.mkdir(parents=True, exist_ok=True)
    stamp = dt.date.today().strftime("%Y%m%d")
    logfile = logdir / f"{'envio' if args.apply else 'dryrun'}_{stamp}.csv"

    print("\n  --- FILA ---")
    with open(logfile, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["owner_id", "email_mascarado", "n_projetos", "campos_faltando",
                    "assunto", "enviado"])
        for reg in fila:
            assunto, html = montar_copy(reg["nome"], reg["projetos"])
            enviado = False
            if args.apply:
                db.collection("mail").add({
                    "to": reg["email"],
                    "createdAt": firestore.SERVER_TIMESTAMP,
                    "message": {"subject": assunto, "html": html},
                    "origem": MARCA_ORIGEM,
                })
                db.collection(COL_CONTROLE).document(reg["id"]).set({
                    "nProjetos": len(reg["projetos"]),
                    "enviadoEm": firestore.SERVER_TIMESTAMP,
                })
                enviado = True
                time.sleep(PAUSA_ENTRE_ENVIOS_S)
            faltas = sorted({c for _, f in reg["projetos"] for c in f})
            print(f"    {mascarar(reg['email']):<28} {len(reg['projetos'])} proj  {assunto[:56]}")
            w.writerow([reg["id"], mascarar(reg["email"]), len(reg["projetos"]),
                        " | ".join(faltas), assunto, enviado])

    print(f"\n  log: {logfile}")
    print("\n  DRY-RUN: nada enviado. Use --apply." if not args.apply
          else f"\n  ENVIADOS: {len(fila)}")


if __name__ == "__main__":
    sys.exit(main())
