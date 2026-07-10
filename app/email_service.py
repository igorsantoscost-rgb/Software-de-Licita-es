"""
Servico de envio de e-mails via Resend.
Remetente fixo: contato@licitabidfy.com.br

Gatilhos:
  1. Nova licitacao cadastrada -> email pro cliente e assessores
  2. Lembrete 1 dia antes do pregao -> email pro cliente
  3. Assessor comenta -> email pro cliente
"""

import os
import logging
import requests
from datetime import datetime

logger = logging.getLogger(__name__)

RESEND_API_KEY = os.environ.get("RESEND_API_KEY", "")
REMETENTE = "Bidfy <contato@licitabidfy.com.br>"
BASE_URL = os.environ.get("BASE_URL", "https://licitabidfy.com.br")


def _enviar(destinatarios, assunto, html):
    """Envia um e-mail via Resend API. Retorna True se deu certo."""
    if not RESEND_API_KEY:
        logger.warning("RESEND_API_KEY nao configurada — e-mail nao enviado.")
        return False
    if not destinatarios:
        return False
    # Garante lista
    if isinstance(destinatarios, str):
        destinatarios = [destinatarios]
    try:
        resp = requests.post(
            "https://api.resend.com/emails",
            headers={"Authorization": f"Bearer {RESEND_API_KEY}"},
            json={
                "from": REMETENTE,
                "to": destinatarios,
                "subject": assunto,
                "html": html,
            },
            timeout=15,
        )
        if resp.status_code in (200, 201):
            logger.info(f"E-mail enviado para {destinatarios}: {assunto}")
            return True
        else:
            logger.error(f"Resend erro {resp.status_code}: {resp.text}")
            return False
    except Exception as e:
        logger.error(f"Erro ao enviar e-mail: {e}")
        return False


def _emails_do_cliente(cliente):
    """Retorna lista de e-mails dos usuarios vinculados ao cliente
    mais o email_contato cadastrado (se houver)."""
    if not cliente:
        return []
    emails = []
    # Email de contato do cadastro do cliente (campo de avisos)
    if cliente.email_contato:
        emails.append(cliente.email_contato)
    # Emails dos usuarios vinculados
    if cliente.usuarios:
        for u in cliente.usuarios:
            if u.email and u.email not in emails:
                emails.append(u.email)
    return emails


def _emails_assessores():
    """Retorna lista de e-mails de todos os assessores."""
    from app.models import User
    assessores = User.query.filter_by(perfil="assessor").all()
    return [u.email for u in assessores if u.email]


def _formatar_data(dt):
    """Formata datetime para exibicao amigavel."""
    if not dt:
        return "Nao definida"
    return dt.strftime("%d/%m/%Y as %H:%M")


def _template_base(conteudo):
    """Wrapper HTML com estilo Bidfy."""
    return f"""
    <div style="font-family: Arial, sans-serif; max-width: 600px; margin: 0 auto; padding: 20px;">
      <div style="background: #14532d; padding: 16px 24px; border-radius: 8px 8px 0 0; text-align: center;">
        <span style="color: #fff; font-size: 22px; font-weight: 700; letter-spacing: 1px;">
          <span style="color: #fff;">BID</span><span style="color: #4ade80;">FY</span>
        </span>
      </div>
      <div style="border: 1px solid #e5e7eb; border-top: none; border-radius: 0 0 8px 8px; padding: 24px;">
        {conteudo}
      </div>
      <p style="color: #9ca3af; font-size: 12px; text-align: center; margin-top: 16px;">
        Este e-mail foi enviado automaticamente pela plataforma Bidfy.<br>
        Nao responda a este e-mail.
      </p>
    </div>
    """


# ─── GATILHO 1: Nova licitacao cadastrada ────────────────────────────────────

def notificar_nova_licitacao(lic):
    """Envia e-mail para o cliente e assessores quando uma licitacao e criada."""
    cliente = lic.cliente
    destinatarios = _emails_do_cliente(cliente) + _emails_assessores()
    # Remove duplicatas mantendo ordem
    destinatarios = list(dict.fromkeys(destinatarios))
    if not destinatarios:
        return

    assunto = f"Nova licitacao cadastrada — {lic.orgao_licitante}"

    conteudo = f"""
    <h2 style="color: #14532d; margin-top: 0;">Nova Licitacao Cadastrada</h2>
    <p>Uma nova licitacao foi cadastrada para <strong>{cliente.nome}</strong>.</p>
    <table style="width: 100%; border-collapse: collapse; margin: 16px 0;">
      <tr><td style="padding: 8px; border-bottom: 1px solid #e5e7eb; color: #6b7280;">Orgao Licitante</td>
          <td style="padding: 8px; border-bottom: 1px solid #e5e7eb; font-weight: 600;">{lic.orgao_licitante}</td></tr>
      <tr><td style="padding: 8px; border-bottom: 1px solid #e5e7eb; color: #6b7280;">Pregao</td>
          <td style="padding: 8px; border-bottom: 1px solid #e5e7eb; font-weight: 600;">{lic.numero_pregao}</td></tr>
      <tr><td style="padding: 8px; border-bottom: 1px solid #e5e7eb; color: #6b7280;">UASG</td>
          <td style="padding: 8px; border-bottom: 1px solid #e5e7eb; font-weight: 600;">{lic.uasg or '—'}</td></tr>
      <tr><td style="padding: 8px; border-bottom: 1px solid #e5e7eb; color: #6b7280;">Portal</td>
          <td style="padding: 8px; border-bottom: 1px solid #e5e7eb; font-weight: 600;">{lic.portal or '—'}</td></tr>
      <tr><td style="padding: 8px; border-bottom: 1px solid #e5e7eb; color: #6b7280;">Data da Disputa</td>
          <td style="padding: 8px; border-bottom: 1px solid #e5e7eb; font-weight: 600;">{_formatar_data(lic.data_disputa)}</td></tr>
    </table>
    <p><a href="{BASE_URL}/licitacoes/{lic.id}" style="background: #14532d; color: #fff; padding: 10px 20px;
        border-radius: 6px; text-decoration: none; display: inline-block;">Ver Licitacao</a></p>
    """
    _enviar(destinatarios, assunto, _template_base(conteudo))


# ─── GATILHO 2: Lembrete 1 dia antes do pregao ──────────────────────────────

def enviar_lembretes_diarios():
    """Busca licitacoes com disputa amanha e envia lembrete para os clientes.
    Deve ser chamado uma vez por dia (ex: via cron as 8h)."""
    from app.models import Licitacao
    from datetime import timedelta

    amanha = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)
    fim_amanha = amanha.replace(hour=23, minute=59, second=59)

    lics = Licitacao.query.filter(
        Licitacao.data_disputa >= amanha,
        Licitacao.data_disputa <= fim_amanha,
        Licitacao.status == "agendada",
    ).all()

    enviados = 0
    for lic in lics:
        cliente = lic.cliente
        destinatarios = _emails_do_cliente(cliente) + _emails_assessores()
        destinatarios = list(dict.fromkeys(destinatarios))
        if not destinatarios:
            continue

        # Verifica se tem itens sem valor minimo preenchido
        itens_sem_valor = [i for i in lic.itens if i.valor_minimo is None]
        aviso_parametros = ""
        if itens_sem_valor:
            aviso_parametros = f"""
            <div style="background: #fef3c7; border-left: 4px solid #f59e0b; padding: 12px 16px; margin: 16px 0; border-radius: 4px;">
              <strong style="color: #92400e;">Atencao:</strong> {len(itens_sem_valor)} item(ns) ainda nao tem valor minimo preenchido.
              Preencha os parametros antes da disputa para que seu assessor participe com os valores corretos.
            </div>
            """

        assunto = f"Lembrete: pregao amanha — {lic.orgao_licitante}"

        conteudo = f"""
        <h2 style="color: #14532d; margin-top: 0;">Lembrete de Pregao</h2>
        <p>A disputa a seguir acontecera <strong>amanha</strong>:</p>
        <table style="width: 100%; border-collapse: collapse; margin: 16px 0;">
          <tr><td style="padding: 8px; border-bottom: 1px solid #e5e7eb; color: #6b7280;">Orgao Licitante</td>
              <td style="padding: 8px; border-bottom: 1px solid #e5e7eb; font-weight: 600;">{lic.orgao_licitante}</td></tr>
          <tr><td style="padding: 8px; border-bottom: 1px solid #e5e7eb; color: #6b7280;">Pregao</td>
              <td style="padding: 8px; border-bottom: 1px solid #e5e7eb; font-weight: 600;">{lic.numero_pregao}</td></tr>
          <tr><td style="padding: 8px; border-bottom: 1px solid #e5e7eb; color: #6b7280;">Horario da Disputa</td>
              <td style="padding: 8px; border-bottom: 1px solid #e5e7eb; font-weight: 600; color: #dc2626;">{_formatar_data(lic.data_disputa)}</td></tr>
          <tr><td style="padding: 8px; border-bottom: 1px solid #e5e7eb; color: #6b7280;">Portal</td>
              <td style="padding: 8px; border-bottom: 1px solid #e5e7eb; font-weight: 600;">{lic.portal or '—'}</td></tr>
          <tr><td style="padding: 8px; border-bottom: 1px solid #e5e7eb; color: #6b7280;">Cliente</td>
              <td style="padding: 8px; border-bottom: 1px solid #e5e7eb; font-weight: 600;">{cliente.nome}</td></tr>
        </table>
        {aviso_parametros}
        <p><a href="{BASE_URL}/licitacoes/{lic.id}" style="background: #14532d; color: #fff; padding: 10px 20px;
            border-radius: 6px; text-decoration: none; display: inline-block;">Ver Licitacao e Parametros</a></p>
        """
        if _enviar(destinatarios, assunto, _template_base(conteudo)):
            enviados += 1

    return {"total": len(lics), "enviados": enviados}


# ─── GATILHO 3: Assessor comenta ─────────────────────────────────────────────

def notificar_novo_comentario(comentario, licitacao):
    """Envia e-mail para o cliente quando o assessor escreve um comentario."""
    cliente = licitacao.cliente
    destinatarios = _emails_do_cliente(cliente)
    if not destinatarios:
        return

    assunto = f"Novo comentario na licitacao — {licitacao.orgao_licitante}"

    conteudo = f"""
    <h2 style="color: #14532d; margin-top: 0;">Novo Comentario</h2>
    <p>O assessor <strong>{comentario.autor.nome}</strong> adicionou um comentario na licitacao:</p>
    <table style="width: 100%; border-collapse: collapse; margin: 12px 0;">
      <tr><td style="padding: 8px; border-bottom: 1px solid #e5e7eb; color: #6b7280;">Orgao</td>
          <td style="padding: 8px; border-bottom: 1px solid #e5e7eb; font-weight: 600;">{licitacao.orgao_licitante}</td></tr>
      <tr><td style="padding: 8px; border-bottom: 1px solid #e5e7eb; color: #6b7280;">Pregao</td>
          <td style="padding: 8px; border-bottom: 1px solid #e5e7eb; font-weight: 600;">{licitacao.numero_pregao}</td></tr>
    </table>
    <div style="background: #f3f4f6; padding: 14px 18px; border-radius: 6px; margin: 16px 0; border-left: 4px solid #14532d;">
      <p style="margin: 0; white-space: pre-wrap;">{comentario.texto}</p>
    </div>
    <p><a href="{BASE_URL}/licitacoes/{licitacao.id}" style="background: #14532d; color: #fff; padding: 10px 20px;
        border-radius: 6px; text-decoration: none; display: inline-block;">Ver Licitacao</a></p>
    """
    _enviar(destinatarios, assunto, _template_base(conteudo))
