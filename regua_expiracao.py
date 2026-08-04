# -*- coding: utf-8 -*-
"""
Regua automatica de expiracao de projeto — plataforma Brada.

Le o Firestore ao vivo, segmenta os projetos por proximidade da data de
expiracao e enfileira e-mails na colecao `mail` (extensao firestore-send-email,
ACTIVE). Idempotente: cada (projeto, toque) so e enviado uma vez, registrado em
`regua_expiracao_envios`.

NOMENCLATURA: o campo tecnico ainda se chama `cacExpirationDate` (legado). O
termo de negocio e "data de expiracao do projeto". Nenhuma copy fala "CAC".

Uso:
    python regua_expiracao.py                      # dry-run (nao envia nada)
    python regua_expiracao.py --toques d7,expirado # so alguns toques
    python regua_expiracao.py --apply --limite 1   # canario: envia 1 e-mail
    python regua_expiracao.py --apply              # dispara de verdade
    python regua_expiracao.py --apply --so-email x@y.com   # teste dirigido

Guarda-corpos:
  - dry-run e o padrao; enviar exige --apply explicito
  - nunca envia para dominios internos da Brada
  - nunca envia para e-mail nao verificado
  - nunca reenvia o mesmo toque para o mesmo projeto
  - cada execucao grava um CSV de log em scripts/logs/regua_expiracao/
"""
import argparse, csv, datetime as dt, json, os, pathlib, re, sys

from google.cloud import firestore
from google.oauth2 import service_account

PROJECT_ID = "gen-lang-client-0225656939"
DATABASE = "ai-studio-93e1b1b8-c1c0-446c-87ba-d8fb8e3b0dd6"
SA_FILE = r"C:\Users\bruno\.brada-secrets\firebase-sa.json"


def conectar():
    """Credencial da SA: no CI vem por env JSON; local, do arquivo."""
    bruto = os.environ.get("FIREBASE_SERVICE_ACCOUNT_JSON", "")
    if bruto:
        # decode utf-8-sig tolera BOM
        info = json.loads(bruto.encode("utf-8").decode("utf-8-sig"))
    else:
        with open(SA_FILE, encoding="utf-8-sig") as fh:
            info = json.load(fh)
    creds = service_account.Credentials.from_service_account_info(info)
    return firestore.Client(project=PROJECT_ID, database=DATABASE, credentials=creds)
PLATAFORMA_URL = "https://match.brada.social"
COL_CONTROLE = "regua_expiracao_envios"

# Dominios e enderecos internos: nunca recebem a regua.
DOMINIOS_INTERNOS = ("@brada.social", "@somosbrada.com.br")
EMAILS_INTERNOS = {
    "marketing@brada.social", "suporte@brada.social", "inovacao@brada.social",
    "evaristo.ramalho@somosbrada.com.br", "carolina.barbosa@somosbrada.com.br",
    "diego.baptista@somosbrada.com.br",
}

# Toque -> (dias_min, dias_max) em relacao a hoje. Negativo = ja expirou.
TOQUES = {
    "d30": (16, 30),
    "d15": (8, 15),
    "d7": (0, 7),
    "expirado": (-45, -1),
}


# --------------------------------------------------------------------------- #
# Copy
# --------------------------------------------------------------------------- #
def _primeiro_nome(nome: str) -> str:
    nome = (nome or "").strip()
    if not nome:
        return ""
    p = nome.split()[0]
    return p.capitalize() if p.isupper() or p.islower() else p


def montar_copy(toque: str, nome_pessoa: str, titulo_projeto: str, dias: int, data_exp: dt.date):
    """Retorna (assunto, corpo_html). Sem jargao interno, sem a sigla antiga."""
    saudacao = f"Olá, {_primeiro_nome(nome_pessoa)}" if nome_pessoa else "Olá"
    data_br = data_exp.strftime("%d/%m/%Y")

    if toque == "expirado":
        dias_atras = abs(dias)
        assunto = f"O prazo do projeto {titulo_projeto} venceu"
        abertura = (
            f"O prazo de captação do projeto <strong>{titulo_projeto}</strong> venceu em "
            f"<strong>{data_br}</strong>, há {dias_atras} dia(s)."
        )
        explicacao = (
            "Enquanto a data estiver vencida, o projeto continua no seu painel, mas deixa de fazer "
            "sentido para um patrocinador que consulte a plataforma, porque o prazo de captação já passou."
        )
        acao = "Atualizar a data do projeto"
    else:
        assunto = (
            f"O projeto {titulo_projeto} vence em {dias} dias"
            if dias > 1 else f"O projeto {titulo_projeto} vence amanhã"
            if dias == 1 else f"O projeto {titulo_projeto} vence hoje"
        )
        quando = (
            f"em <strong>{dias} dias</strong>, no dia <strong>{data_br}</strong>" if dias > 1
            else f"<strong>amanhã, {data_br}</strong>" if dias == 1
            else f"<strong>hoje, {data_br}</strong>"
        )
        abertura = (
            f"O prazo de captação do projeto <strong>{titulo_projeto}</strong> termina {quando}."
        )
        explicacao = (
            "Se o prazo já foi prorrogado, vale atualizar a data na plataforma para que o projeto "
            "continue disponível para os patrocinadores que consultam a base."
        )
        acao = "Atualizar a data do projeto"

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
    <div style="text-align: center; margin: 32px 0;">
      <a href="{PLATAFORMA_URL}/login" style="background-color: #ea580c; color: #ffffff; padding: 14px 28px; border-radius: 10px; text-decoration: none; font-weight: 700; display: inline-block;">{acao}</a>
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
# Dados
# --------------------------------------------------------------------------- #
def parse_data(v):
    if v is None:
        return None
    if hasattr(v, "date"):
        try:
            return v.date()
        except Exception:
            pass
    m = re.match(r"(\d{4})-(\d{2})-(\d{2})", str(v))
    return dt.date(int(m.group(1)), int(m.group(2)), int(m.group(3))) if m else None


def e_interno(email: str) -> bool:
    e = (email or "").strip().lower()
    return (not e) or e in EMAILS_INTERNOS or e.endswith(DOMINIOS_INTERNOS)


def mascarar(email: str) -> str:
    a, _, b = (email or "").partition("@")
    return (a[:2] + "***@" + b) if b else "(sem e-mail)"


def coletar(db, hoje, toques):
    """Devolve a lista de envios candidatos, sem filtrar por ja-enviado."""
    users = {}
    for d in db.collection("users").stream():
        users[d.id] = d.to_dict() or {}

    candidatos = []
    for d in db.collection("projects").stream():
        p = d.to_dict() or {}
        status = str(p.get("status") or "").strip()
        # Rascunho e outra conversa (trilha de campanha, nao transacional).
        if status not in ("Disponível", "Em Execução"):
            continue
        exp = parse_data(p.get("cacExpirationDate"))
        if not exp:
            continue
        dias = (exp - hoje).days
        toque = next((t for t in toques
                      if TOQUES[t][0] <= dias <= TOQUES[t][1]), None)
        if not toque:
            continue

        dono = users.get(str(p.get("ownerId") or ""), {})
        email = str(dono.get("email") or "").strip()
        candidatos.append({
            "project_id": d.id,
            "titulo": str(p.get("title") or "seu projeto").strip(),
            "status": status,
            "email": email,
            "nome": str(dono.get("name") or dono.get("displayName") or "").strip(),
            "verificado": bool(dono.get("emailVerified", dono.get("email_verificado", True))),
            "toque": toque,
            "dias": dias,
            "data_exp": exp,
        })
    return candidatos


# --------------------------------------------------------------------------- #
# Execucao
# --------------------------------------------------------------------------- #
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="envia de verdade (padrao e dry-run)")
    ap.add_argument("--toques", default="d30,d15,d7,expirado")
    ap.add_argument("--limite", type=int, default=0, help="canario: envia no maximo N")
    ap.add_argument("--so-email", default="", help="restringe a um destinatario (teste)")
    ap.add_argument("--hoje", default="", help="simula outra data, formato AAAA-MM-DD")
    args = ap.parse_args()

    hoje = dt.date.fromisoformat(args.hoje) if args.hoje else dt.date.today()
    toques = [t.strip() for t in args.toques.split(",") if t.strip() in TOQUES]
    modo = "APPLY (ENVIA)" if args.apply else "DRY-RUN (nao envia)"

    print(f"=== REGUA DE EXPIRACAO — {modo} ===")
    print(f"  data de referencia: {hoje}  |  toques: {', '.join(toques)}")

    db = conectar()
    candidatos = coletar(db, hoje, toques)
    print(f"  projetos na janela: {len(candidatos)}")

    ja_enviados = {d.id for d in db.collection(COL_CONTROLE).stream()}
    print(f"  envios ja registrados: {len(ja_enviados)}")

    fila, pulados = [], []
    for c in candidatos:
        chave = f"{c['project_id']}__{c['toque']}"
        if chave in ja_enviados:
            pulados.append((c, "ja enviado")); continue
        if not c["email"]:
            pulados.append((c, "sem e-mail")); continue
        if e_interno(c["email"]):
            pulados.append((c, "interno")); continue
        if not c["verificado"]:
            pulados.append((c, "e-mail nao verificado")); continue
        if args.so_email and c["email"].lower() != args.so_email.lower():
            pulados.append((c, "fora do --so-email")); continue
        c["chave"] = chave
        fila.append(c)

    fila.sort(key=lambda c: c["dias"])
    if args.limite:
        fila = fila[:args.limite]

    print(f"\n  A ENVIAR: {len(fila)}   |   pulados: {len(pulados)}")
    motivos = {}
    for _, m in pulados:
        motivos[m] = motivos.get(m, 0) + 1
    for m, n in sorted(motivos.items(), key=lambda x: -x[1]):
        print(f"      {m}: {n}")

    win = pathlib.Path(r"C:\Users\bruno\Documents\Brada\scripts\logs\regua_expiracao")
    logdir = win if os.name == "nt" else pathlib.Path("logs/regua_expiracao")
    logdir.mkdir(parents=True, exist_ok=True)
    stamp = hoje.strftime("%Y%m%d")
    logfile = logdir / f"{'envio' if args.apply else 'dryrun'}_{stamp}.csv"

    print("\n  --- FILA ---")
    with open(logfile, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["project_id", "toque", "dias", "data_expiracao", "status",
                    "email_mascarado", "titulo", "assunto", "enviado"])
        for c in fila:
            assunto, html = montar_copy(c["toque"], c["nome"], c["titulo"], c["dias"], c["data_exp"])
            enviado = False
            if args.apply:
                db.collection("mail").add({
                    "to": c["email"],
                    "createdAt": firestore.SERVER_TIMESTAMP,
                    "message": {"subject": assunto, "html": html},
                })
                db.collection(COL_CONTROLE).document(c["chave"]).set({
                    "projectId": c["project_id"], "toque": c["toque"],
                    "diasNoEnvio": c["dias"], "enviadoEm": firestore.SERVER_TIMESTAMP,
                })
                enviado = True
            print(f"    [{c['toque']:>8}] {c['dias']:>4}d  {mascarar(c['email']):<28} {c['titulo'][:44]}")
            print(f"               assunto: {assunto}")
            w.writerow([c["project_id"], c["toque"], c["dias"], c["data_exp"], c["status"],
                        mascarar(c["email"]), c["titulo"], assunto, enviado])

    print(f"\n  log: {logfile}")
    if not args.apply:
        print("\n  DRY-RUN: nada foi enviado. Para disparar, rode com --apply.")
    else:
        print(f"\n  ENVIADOS: {len(fila)} e-mail(s) enfileirados na colecao mail.")


if __name__ == "__main__":
    sys.exit(main())
