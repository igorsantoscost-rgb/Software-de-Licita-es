"""Caixa de entrada: avisos do sistema e recados entre a equipe."""
from flask import Blueprint, render_template, redirect, url_for, request, flash, abort
from flask_login import login_required, current_user

from app import db
from app.models import MensagemCaixa, User, agora_brasilia
from app.caixa import enviar_mensagem

caixa_bp = Blueprint("caixa", __name__, url_prefix="/caixa")

LIMITE_TEXTO = 4000


def _destinatarios_possiveis():
    """Equipe fala com a equipe. Master tambem pode escrever para clientes."""
    perfis = ("master", "assessor", "cliente") if current_user.is_master() else ("master", "assessor")
    return (User.query.filter(User.perfil.in_(perfis), User.id != current_user.id)
            .order_by(User.perfil.desc(), User.nome).all())


@caixa_bp.route("/")
@login_required
def lista():
    aba = request.args.get("aba", "entrada")
    if aba == "enviadas" and current_user.is_assessor():
        msgs = (MensagemCaixa.query.filter_by(remetente_id=current_user.id)
                .order_by(MensagemCaixa.criado_em.desc()).limit(200).all())
    else:
        aba = "entrada"
        msgs = (MensagemCaixa.query.filter_by(destinatario_id=current_user.id)
                .order_by(MensagemCaixa.criado_em.desc()).limit(200).all())
    return render_template("caixa.html", msgs=msgs, aba=aba,
                           destinatarios=_destinatarios_possiveis() if current_user.is_assessor() else [])


@caixa_bp.route("/<int:id>")
@login_required
def ver(id):
    m = MensagemCaixa.query.get_or_404(id)
    if current_user.id not in (m.destinatario_id, m.remetente_id):
        abort(403)
    if m.destinatario_id == current_user.id and not m.lida_em:
        m.lida_em = agora_brasilia()
        db.session.commit()
    return render_template("caixa_mensagem.html", m=m)


@caixa_bp.route("/nova", methods=["POST"])
@login_required
def nova():
    if not current_user.is_assessor():
        abort(403)
    permitidos = {u.id for u in _destinatarios_possiveis()}
    ids = [int(i) for i in request.form.getlist("para") if i.isdigit() and int(i) in permitidos]
    assunto = request.form.get("assunto", "").strip()
    texto = request.form.get("texto", "").strip()
    if not ids or not assunto:
        flash("Escolha quem vai receber e escreva o assunto.", "erro")
        return redirect(url_for("caixa.lista"))
    if len(texto) > LIMITE_TEXTO:
        flash(f"A mensagem passou de {LIMITE_TEXTO} caracteres.", "erro")
        return redirect(url_for("caixa.lista"))
    qtd = enviar_mensagem(ids, assunto, texto, remetente_id=current_user.id)
    db.session.commit()
    flash(f"Mensagem enviada para {qtd} pessoa(s).", "ok")
    return redirect(url_for("caixa.lista", aba="enviadas"))


@caixa_bp.route("/<int:id>/responder", methods=["POST"])
@login_required
def responder(id):
    m = MensagemCaixa.query.get_or_404(id)
    if m.destinatario_id != current_user.id or not m.remetente_id:
        abort(403)
    texto = request.form.get("texto", "").strip()[:LIMITE_TEXTO]
    if not texto:
        flash("Escreva a resposta.", "erro")
        return redirect(url_for("caixa.ver", id=id))
    assunto = m.assunto if m.assunto.lower().startswith("re:") else f"Re: {m.assunto}"
    enviar_mensagem([m.remetente_id], assunto, texto, link=m.link, remetente_id=current_user.id)
    db.session.commit()
    flash("Resposta enviada.", "ok")
    return redirect(url_for("caixa.lista"))


@caixa_bp.route("/marcar-lidas", methods=["POST"])
@login_required
def marcar_lidas():
    (MensagemCaixa.query.filter_by(destinatario_id=current_user.id, lida_em=None)
     .update({"lida_em": agora_brasilia()}))
    db.session.commit()
    return redirect(url_for("caixa.lista"))
