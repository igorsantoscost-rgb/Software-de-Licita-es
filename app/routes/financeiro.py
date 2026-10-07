"""Rotas do modulo Financeiro — faturas mensais por cliente."""

import os
import uuid
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from flask import Blueprint, render_template, redirect, url_for, request, flash, abort, send_file
from flask_login import login_required, current_user
from werkzeug.utils import secure_filename
from app.models import Fatura, ItemFatura, Cliente, STATUS_FATURA
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
    from app.email_service import emails_financeiro
    destinatarios = emails_financeiro(fatura.cliente) if current_user.is_master() else []
    return render_template("detalhe_fatura.html", fatura=fatura, destinatarios_email=destinatarios)


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


# ─── Arquivos da fatura (boleto e relatorio) ─────────────────────────────────

def _salvar_arquivo_fatura(fatura, arquivo, prefixo):
    pasta = os.path.join("/app/uploads", "boletos", str(fatura.cliente_id))
    os.makedirs(pasta, exist_ok=True)
    nome = secure_filename(arquivo.filename) or "arquivo"
    caminho = os.path.join(pasta, f"{prefixo}_{uuid.uuid4().hex[:8]}_{nome}")
    arquivo.save(caminho)
    return caminho


def _apagar_arquivo(caminho):
    if caminho:
        try:
            os.remove(caminho)
        except OSError:
            pass


@fin_bp.route("/fatura/<int:id>/upload-relatorio", methods=["POST"])
@login_required
def upload_relatorio(id):
    if not current_user.is_master():
        abort(403)
    fatura = Fatura.query.get_or_404(id)
    f = request.files.get("relatorio")
    if not f or not f.filename:
        flash("Selecione o arquivo do relatório.", "erro")
        return redirect(url_for("fin.detalhe_fatura", id=id))
    caminho = _salvar_arquivo_fatura(fatura, f, "relatorio")
    _apagar_arquivo(fatura.relatorio_caminho)
    fatura.relatorio_caminho = caminho
    fatura.relatorio_nome = f.filename[:300]
    db.session.commit()
    flash("Relatório juntado à fatura.", "ok")
    return redirect(url_for("fin.detalhe_fatura", id=id))


@fin_bp.route("/fatura/<int:id>/download-relatorio")
@login_required
def download_relatorio(id):
    fatura = Fatura.query.get_or_404(id)
    if not current_user.pode_ver_cliente(fatura.cliente_id):
        abort(403)
    if not fatura.relatorio_caminho or not os.path.exists(fatura.relatorio_caminho):
        flash("Relatório não disponível.", "erro")
        return redirect(url_for("fin.detalhe_fatura", id=id))
    return send_file(fatura.relatorio_caminho,
                     download_name=fatura.relatorio_nome or "relatorio.pdf",
                     as_attachment=True)


@fin_bp.route("/fatura/<int:id>/remover-relatorio", methods=["POST"])
@login_required
def remover_relatorio(id):
    if not current_user.is_master():
        abort(403)
    fatura = Fatura.query.get_or_404(id)
    _apagar_arquivo(fatura.relatorio_caminho)
    fatura.relatorio_caminho = None
    fatura.relatorio_nome = None
    db.session.commit()
    flash("Relatório removido.", "ok")
    return redirect(url_for("fin.detalhe_fatura", id=id))


# ─── Enviar tudo por e-mail ──────────────────────────────────────────────────

@fin_bp.route("/fatura/<int:id>/enviar-email", methods=["POST"])
@login_required
def enviar_email(id):
    if not current_user.is_master():
        abort(403)
    fatura = Fatura.query.get_or_404(id)
    from app.email_service import enviar_fatura_completa
    ok, destinatarios, anexos = enviar_fatura_completa(fatura)
    if not destinatarios:
        flash("O cliente não tem e-mail financeiro nem e-mail de avisos cadastrado. "
              "Preencha no cadastro do cliente.", "erro")
    elif ok:
        fatura.enviada_em = datetime.now()
        fatura.enviada_para = ", ".join(destinatarios)[:500]
        db.session.commit()
        extras = f" com {', '.join(anexos)}" if anexos else " (sem anexos)"
        flash(f"Fatura enviada para {', '.join(destinatarios)}{extras}.", "ok")
    else:
        flash("Não consegui enviar o e-mail agora. Tente de novo em alguns minutos.", "erro")
    return redirect(url_for("fin.detalhe_fatura", id=id))


# ─── Editar e excluir ────────────────────────────────────────────────────────

def _valor(txt):
    """'1.621,00' / '1621.00' / 'R$ 1.621' -> Decimal; vazio -> None."""
    txt = (txt or "").replace("R$", "").strip()
    if not txt:
        return None
    if "," in txt:
        txt = txt.replace(".", "").replace(",", ".")
    try:
        return Decimal(txt).quantize(Decimal("0.01"))
    except InvalidOperation:
        raise ValueError(txt)


@fin_bp.route("/fatura/<int:id>/editar", methods=["GET", "POST"])
@login_required
def editar_fatura(id):
    if not current_user.is_master():
        abort(403)
    fatura = Fatura.query.get_or_404(id)

    if request.method == "POST":
        try:
            mes = datetime.strptime(request.form.get("mes_referencia", ""), "%Y-%m").date().replace(day=1)
            vencimento = datetime.strptime(request.form.get("vencimento", ""), "%Y-%m-%d").date()
            taxa = _valor(request.form.get("taxa_consultoria")) or Decimal("0")
            implantacao = _valor(request.form.get("taxa_implantacao")) or Decimal("0")
        except ValueError:
            flash("Confira o mês, o vencimento e os valores: algum está em formato inválido.", "erro")
            return redirect(url_for("fin.editar_fatura", id=id))

        outra = Fatura.query.filter(Fatura.cliente_id == fatura.cliente_id,
                                    Fatura.mes_referencia == mes, Fatura.id != fatura.id).first()
        if outra:
            flash(f"Este cliente já tem a fatura de {outra.nome_mes}. Escolha outro mês.", "erro")
            return redirect(url_for("fin.editar_fatura", id=id))

        status = request.form.get("status")
        fatura.mes_referencia = mes
        fatura.vencimento = vencimento
        fatura.taxa_consultoria = taxa
        fatura.taxa_implantacao = implantacao
        if status in STATUS_FATURA:
            fatura.status = status

        # Itens existentes: editar ou remover
        remover = set(request.form.getlist("remover_item"))
        for item in list(fatura.itens):
            if str(item.id) in remover:
                db.session.delete(item)
                continue
            desc = (request.form.get(f"item_desc_{item.id}") or "").strip()
            try:
                valor = _valor(request.form.get(f"item_valor_{item.id}"))
            except ValueError:
                db.session.rollback()
                flash(f"Valor inválido no item \"{item.descricao[:60]}\".", "erro")
                return redirect(url_for("fin.editar_fatura", id=id))
            if desc:
                item.descricao = desc[:300]
            if valor is not None:
                item.valor = valor

        # Novo item avulso (opcional)
        nova_desc = (request.form.get("novo_desc") or "").strip()
        if nova_desc:
            try:
                novo_valor = _valor(request.form.get("novo_valor"))
            except ValueError:
                novo_valor = None
            if novo_valor is None:
                db.session.rollback()
                flash("Informe o valor do item novo.", "erro")
                return redirect(url_for("fin.editar_fatura", id=id))
            db.session.add(ItemFatura(fatura_id=fatura.id, descricao=nova_desc[:300], valor=novo_valor))

        db.session.commit()
        flash("Fatura atualizada.", "ok")
        return redirect(url_for("fin.detalhe_fatura", id=id))

    return render_template("form_fatura.html", fatura=fatura, status_choices=STATUS_FATURA)


@fin_bp.route("/fatura/<int:id>/excluir", methods=["POST"])
@login_required
def excluir_fatura(id):
    if not current_user.is_master():
        abort(403)
    fatura = Fatura.query.get_or_404(id)
    nome = f"{fatura.nome_mes} de {fatura.cliente.nome}"
    _apagar_arquivo(fatura.boleto_caminho)
    _apagar_arquivo(fatura.relatorio_caminho)
    db.session.delete(fatura)
    db.session.commit()
    flash(f"Fatura {nome} excluída.", "ok")
    return redirect(url_for("fin.painel"))
