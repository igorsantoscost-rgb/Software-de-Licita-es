"""Rotas dos acessos a portais (aba Portais do cliente) e da autorizacao de
compra de acesso pedida ao cliente a partir de uma licitacao."""
import json
from datetime import date, datetime, timedelta

from flask import Blueprint, abort, flash, redirect, request, url_for
from flask_login import current_user, login_required

from app import db
from app.models import (AcessoPortal, Cliente, FORMAS_PAGAMENTO_PORTAL, Licitacao,
                        SolicitacaoAcessoPortal)
from app.portais import lancar_cobrancas, registrar_ja_cobrado, valor_reais

portais_bp = Blueprint("portais", __name__)


def _assessor_do_cliente(cliente_id):
    if not current_user.is_assessor() or not current_user.pode_ver_cliente(cliente_id):
        abort(403)


def _voltar_portais(cliente_id):
    return redirect(url_for("main.editar_cliente", id=cliente_id, _anchor="portais"))


def _data(txt):
    try:
        return datetime.strptime((txt or "").strip(), "%Y-%m-%d").date()
    except ValueError:
        return None


def _ler_acesso(form, acesso):
    """Preenche o acesso com o formulario. Devolve mensagem de erro ou None."""
    portal = (form.get("portal") or "").strip()
    if portal == "__outro__":
        portal = (form.get("portal_outro") or "").strip()
    if not portal:
        return "Escolha o portal."
    pago = form.get("pago") == "1"
    forma = form.get("forma_pagamento") if pago else None
    if pago and forma not in FORMAS_PAGAMENTO_PORTAL:
        return "Escolha a forma de pagamento."
    try:
        valor = valor_reais(form.get("valor")) if pago else None
    except ValueError:
        return "Valor inválido."
    if pago and not valor:
        return "Informe o valor do acesso pago."
    valido_ate = _data(form.get("valido_ate"))
    if pago and forma == "anual" and not valido_ate:
        return "No acesso anual, informe até quando ele vale."
    acesso.portal = portal[:100]
    acesso.pago = pago
    acesso.forma_pagamento = forma
    acesso.valor = valor
    acesso.valido_ate = valido_ate
    acesso.observacao = (form.get("observacao") or "").strip()[:300] or None
    return None


@portais_bp.route("/clientes/<int:id>/portais/novo", methods=["POST"])
@login_required
def novo_acesso(id):
    _assessor_do_cliente(id)
    cliente = Cliente.query.get_or_404(id)
    acesso = AcessoPortal(cliente_id=cliente.id, inicio=date.today(), criado_por=current_user.id)
    erro = _ler_acesso(request.form, acesso)
    if erro:
        flash(erro, "erro")
        return _voltar_portais(id)
    db.session.add(acesso)
    db.session.flush()
    if request.form.get("ja_cobrado") == "1":
        registrar_ja_cobrado(acesso)
    novas = lancar_cobrancas(cliente)
    db.session.commit()
    msg = f"Acesso ao portal {acesso.portal} registrado."
    if novas:
        msg += " A cobrança entrou na fatura do cliente."
    flash(msg, "ok")
    return _voltar_portais(id)


@portais_bp.route("/clientes/portais/<int:acesso_id>/editar", methods=["POST"])
@login_required
def editar_acesso(acesso_id):
    acesso = AcessoPortal.query.get_or_404(acesso_id)
    _assessor_do_cliente(acesso.cliente_id)
    erro = _ler_acesso(request.form, acesso)
    if erro:
        db.session.rollback()
        flash(erro, "erro")
        return _voltar_portais(acesso.cliente_id)
    novas = lancar_cobrancas(acesso.cliente)
    db.session.commit()
    flash("Acesso atualizado." + (" A cobrança entrou na fatura do cliente." if novas else ""), "ok")
    return _voltar_portais(acesso.cliente_id)


@portais_bp.route("/clientes/portais/<int:acesso_id>/excluir", methods=["POST"])
@login_required
def excluir_acesso(acesso_id):
    acesso = AcessoPortal.query.get_or_404(acesso_id)
    _assessor_do_cliente(acesso.cliente_id)
    cliente_id, nome = acesso.cliente_id, acesso.portal
    db.session.delete(acesso)
    db.session.commit()
    flash(f"Acesso ao portal {nome} removido. O que já foi cobrado continua nas faturas.", "ok")
    return _voltar_portais(cliente_id)


# ─── Critica da licitacao: cliente sem acesso ao portal ─────────────────────

@portais_bp.route("/licitacoes/<int:id>/portal/solicitar", methods=["POST"])
@login_required
def solicitar_acesso(id):
    lic = Licitacao.query.get_or_404(id)
    _assessor_do_cliente(lic.cliente_id)
    planos = []
    formas = request.form.getlist("plano_forma")
    valores = request.form.getlist("plano_valor")
    for forma, valor_txt in zip(formas, valores):
        if forma not in FORMAS_PAGAMENTO_PORTAL or not (valor_txt or "").strip():
            continue
        try:
            valor = valor_reais(valor_txt)
        except ValueError:
            flash(f"Valor inválido no plano {FORMAS_PAGAMENTO_PORTAL[forma]}.", "erro")
            return redirect(url_for("lic.detalhe", id=id, _anchor="acesso-portal"))
        if valor:
            planos.append({"forma": forma, "valor": float(valor)})
    if not planos:
        flash("Informe o valor de pelo menos um plano.", "erro")
        return redirect(url_for("lic.detalhe", id=id, _anchor="acesso-portal"))
    # Uma pergunta aberta por vez: a anterior pendente e substituida
    for s in SolicitacaoAcessoPortal.query.filter_by(licitacao_id=lic.id, status="pendente").all():
        s.status = "cancelada"
        s.respondido_em = datetime.utcnow()
        s.respondido_por = current_user.id
        s.resposta_obs = "Substituída por nova consulta."
    db.session.add(SolicitacaoAcessoPortal(
        licitacao_id=lic.id, cliente_id=lic.cliente_id, portal=lic.portal,
        planos=json.dumps(planos), observacao=(request.form.get("observacao") or "").strip()[:500] or None,
        criado_por=current_user.id))
    db.session.commit()
    flash("Consulta enviada ao cliente. Ela aparece na licitação e no resumo diário por e-mail.", "ok")
    return redirect(url_for("lic.detalhe", id=id, _anchor="acesso-portal"))


@portais_bp.route("/licitacoes/portal/solicitacao/<int:sid>/cancelar", methods=["POST"])
@login_required
def cancelar_solicitacao(sid):
    s = SolicitacaoAcessoPortal.query.get_or_404(sid)
    _assessor_do_cliente(s.cliente_id)
    if s.status == "pendente":
        s.status = "cancelada"
        s.respondido_em = datetime.utcnow()
        s.respondido_por = current_user.id
        db.session.commit()
        flash("Consulta cancelada.", "ok")
    return redirect(url_for("lic.detalhe", id=s.licitacao_id, _anchor="acesso-portal"))


@portais_bp.route("/licitacoes/portal/solicitacao/<int:sid>/responder", methods=["POST"])
@login_required
def responder_solicitacao(sid):
    """Resposta do cliente: autoriza um dos planos ou recusa."""
    s = SolicitacaoAcessoPortal.query.get_or_404(sid)
    if current_user.is_assessor() or current_user.cliente_id != s.cliente_id:
        abort(403)
    if s.status != "pendente":
        flash("Esta consulta já foi respondida.", "erro")
        return redirect(url_for("lic.detalhe", id=s.licitacao_id))
    escolha = request.form.get("escolha", "")
    obs = (request.form.get("resposta_obs") or "").strip()[:500] or None
    s.respondido_em = datetime.utcnow()
    s.respondido_por = current_user.id
    s.resposta_obs = obs
    if escolha == "recusar":
        s.status = "recusada"
        db.session.commit()
        _avisar_assessores(s)
        flash("Resposta registrada: compra do acesso não autorizada.", "ok")
        return redirect(url_for("lic.detalhe", id=s.licitacao_id))
    plano = next((p for p in s.lista_planos if p["forma"] == escolha), None)
    if not plano:
        db.session.rollback()
        flash("Escolha um dos planos ou a opção de não autorizar.", "erro")
        return redirect(url_for("lic.detalhe", id=s.licitacao_id, _anchor="acesso-portal"))
    s.status = "autorizada"
    s.plano_escolhido = plano["forma"]
    s.valor_escolhido = plano["valor"]
    # Registra o acesso do cliente; a cobranca entra na fatura pelas regras do plano
    valido_ate = None
    if plano["forma"] == "anual":
        hoje = date.today()
        try:
            valido_ate = hoje.replace(year=hoje.year + 1) - timedelta(days=1)
        except ValueError:  # 29/02
            valido_ate = hoje + timedelta(days=364)
    acesso = AcessoPortal(cliente_id=s.cliente_id, portal=s.portal, pago=True,
                          forma_pagamento=plano["forma"], valor=plano["valor"], valido_ate=valido_ate,
                          inicio=date.today(), solicitacao_id=s.id, criado_por=current_user.id,
                          observacao=f"Autorizado pelo cliente na licitação {s.licitacao.numero_pregao}"[:300]
                          if s.licitacao else "Autorizado pelo cliente")
    db.session.add(acesso)
    db.session.flush()
    s.acesso_id = acesso.id
    lancar_cobrancas(s.cliente)
    db.session.commit()
    _avisar_assessores(s)
    flash(f"Obrigado! Compra do acesso ao portal {s.portal} autorizada ({s.rotulo_plano}).", "ok")
    return redirect(url_for("lic.detalhe", id=s.licitacao_id))


def _avisar_assessores(s):
    try:
        from app.email_service import notificar_resposta_acesso_portal
        notificar_resposta_acesso_portal(s)
    except Exception:
        pass
