"""Rotas do modulo Financeiro — faturas mensais por cliente."""

import os
import uuid
from flask import Blueprint, render_template, redirect, url_for, request, flash, abort, send_file
from flask_login import login_required, current_user
from app.models import Fatura, Cliente, STATUS_FATURA
from app import db

fin_bp = Blueprint("fin", __name__, url_prefix="/financeiro")


@fin_bp.route("/")
@login_required
def painel():
    cliente_filtro = request.args.get("cliente_id", "todos")
    status_filtro = request.args.get("status", "todos")

    if not current_user.is_assessor():
        # Cliente ve so suas faturas
        q = Fatura.query.filter_by(cliente_id=current_user.cliente_id)
    elif current_user.is_assessor_puro():
        ids = [c.id for c in current_user.clientes_atendidos]
        q = Fatura.query.filter(Fatura.cliente_id.in_(ids))
    else:
        q = Fatura.query

    if cliente_filtro and cliente_filtro != "todos":
        q = q.filter_by(cliente_id=int(cliente_filtro))
    if status_filtro and status_filtro != "todos":
        q = q.filter_by(status=status_filtro)

    faturas = q.order_by(Fatura.mes_referencia.desc()).all()
    clientes = Cliente.query.order_by(Cliente.nome).all() if current_user.is_assessor() else []

    return render_template("financeiro_painel.html",
                           faturas=faturas,
                           clientes=clientes,
                           cliente_filtro=cliente_filtro,
                           status_filtro=status_filtro,
                           status_choices=STATUS_FATURA)


@fin_bp.route("/fatura/<int:id>")
@login_required
def detalhe_fatura(id):
    fatura = Fatura.query.get_or_404(id)
    if not current_user.pode_ver_cliente(fatura.cliente_id):
        abort(403)
    return render_template("detalhe_fatura.html", fatura=fatura)


@fin_bp.route("/fatura/<int:id>/upload-boleto", methods=["POST"])
@login_required
def upload_boleto(id):
    if not current_user.is_master():
        abort(403)
    fatura = Fatura.query.get_or_404(id)
    f = request.files.get("boleto")
    if not f or not f.filename:
        flash("Selecione um arquivo.", "erro")
        return redirect(url_for("fin.detalhe_fatura", id=id))

    pasta = os.path.join("/app/uploads", "boletos", str(fatura.cliente_id))
    os.makedirs(pasta, exist_ok=True)
    nome_seguro = f"{uuid.uuid4().hex[:8]}_{f.filename}"
    caminho = os.path.join(pasta, nome_seguro)
    f.save(caminho)

    # Remove boleto anterior se existir
    if fatura.boleto_caminho:
        try:
            os.remove(fatura.boleto_caminho)
        except OSError:
            pass

    fatura.boleto_caminho = caminho
    fatura.boleto_nome = f.filename
    db.session.commit()
    flash("Boleto anexado.", "ok")
    return redirect(url_for("fin.detalhe_fatura", id=id))


@fin_bp.route("/fatura/<int:id>/download-boleto")
@login_required
def download_boleto(id):
    fatura = Fatura.query.get_or_404(id)
    if not current_user.pode_ver_cliente(fatura.cliente_id):
        abort(403)
    if not fatura.boleto_caminho or not os.path.exists(fatura.boleto_caminho):
        flash("Boleto nao disponivel.", "erro")
        return redirect(url_for("fin.detalhe_fatura", id=id))
    return send_file(fatura.boleto_caminho,
                     download_name=fatura.boleto_nome or "boleto.pdf",
                     as_attachment=True)


@fin_bp.route("/fatura/<int:id>/marcar-paga", methods=["POST"])
@login_required
def marcar_paga(id):
    if not current_user.is_master():
        abort(403)
    fatura = Fatura.query.get_or_404(id)
    fatura.status = "paga"
    db.session.commit()
    flash("Fatura marcada como paga.", "ok")
    return redirect(url_for("fin.detalhe_fatura", id=id))


@fin_bp.route("/fatura/<int:id>/reabrir", methods=["POST"])
@login_required
def reabrir_fatura(id):
    if not current_user.is_master():
        abort(403)
    fatura = Fatura.query.get_or_404(id)
    fatura.status = "aberta"
    db.session.commit()
    flash("Fatura reaberta.", "ok")
    return redirect(url_for("fin.detalhe_fatura", id=id))


@fin_bp.route("/gerar-faturas-mes", methods=["POST"])
@login_required
def gerar_faturas_mes():
    """Gera faturas do mes atual para todos os clientes que ainda nao tem."""
    if not current_user.is_master():
        abort(403)
    from app.financeiro_service import obter_ou_criar_fatura
    clientes = Cliente.query.all()
    criadas = 0
    for c in clientes:
        fatura = obter_ou_criar_fatura(c)
        if fatura and fatura.id:
            criadas += 1
    db.session.commit()
    flash(f"Faturas do mes verificadas: {criadas} cliente(s).", "ok")
    return redirect(url_for("fin.painel"))
