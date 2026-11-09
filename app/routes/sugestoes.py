"""Sugestoes de melhoria do sistema.

Assessores (e o master) escrevem a sugestao, com texto e anexos (arquivos ou
prints colados com Ctrl+V). O master providencia e registra se foi acatada ou
recusada, com uma resposta opcional.
"""
import os
import uuid

from flask import Blueprint, render_template, redirect, url_for, request, flash, abort, send_from_directory
from flask_login import login_required, current_user

from app import db
from app.models import SugestaoMelhoria, AnexoSugestao, STATUS_SUGESTAO, agora_brasilia

sug_bp = Blueprint("sug", __name__, url_prefix="/sugestoes")

PASTA = "/app/uploads/sugestoes"
LIMITE_ANEXOS = 20


def _so_equipe():
    if not current_user.is_assessor():  # assessor ou master
        abort(403)


def _pode_ver(sug):
    return current_user.is_master() or sug.autor_id == current_user.id


def _salvar_anexos(sug):
    os.makedirs(os.path.join(PASTA, str(sug.id)), exist_ok=True)
    qtd = 0
    for f in request.files.getlist("anexos"):
        if not f or not f.filename or qtd >= LIMITE_ANEXOS:
            continue
        ext = os.path.splitext(f.filename)[1].lower()[:10]
        caminho = os.path.join(PASTA, str(sug.id), f"{uuid.uuid4().hex}{ext}")
        f.save(caminho)
        db.session.add(AnexoSugestao(sugestao_id=sug.id, nome_original=f.filename[:300],
                                     caminho=caminho, tamanho=os.path.getsize(caminho)))
        qtd += 1
    return qtd


@sug_bp.route("/")
@login_required
def lista():
    _so_equipe()
    filtro = request.args.get("status", "todas")
    q = SugestaoMelhoria.query
    if not current_user.is_master():
        q = q.filter_by(autor_id=current_user.id)
    if filtro in STATUS_SUGESTAO:
        q = q.filter_by(status=filtro)
    sugestoes = q.order_by(SugestaoMelhoria.criado_em.desc()).all()

    base = SugestaoMelhoria.query
    if not current_user.is_master():
        base = base.filter_by(autor_id=current_user.id)
    contagem = {s: base.filter_by(status=s).count() for s in STATUS_SUGESTAO}
    contagem["todas"] = sum(contagem.values())
    return render_template("sugestoes.html", sugestoes=sugestoes, filtro=filtro, contagem=contagem)


@sug_bp.route("/nova", methods=["POST"])
@login_required
def nova():
    _so_equipe()
    titulo = request.form.get("titulo", "").strip()
    texto = request.form.get("texto", "").strip()
    if not titulo:
        flash("Dê um título para a sugestão.", "erro")
        return redirect(url_for("sug.lista"))
    sug = SugestaoMelhoria(autor_id=current_user.id, titulo=titulo[:200], texto=texto)
    db.session.add(sug)
    db.session.flush()
    _salvar_anexos(sug)
    db.session.commit()
    try:
        from app.email_service import notificar_nova_sugestao
        notificar_nova_sugestao(sug)
    except Exception:
        pass
    flash("Sugestão enviada. Obrigado!", "ok")
    return redirect(url_for("sug.detalhe", id=sug.id))


@sug_bp.route("/<int:id>")
@login_required
def detalhe(id):
    _so_equipe()
    sug = SugestaoMelhoria.query.get_or_404(id)
    if not _pode_ver(sug):
        abort(403)
    return render_template("sugestao_detalhe.html", sug=sug)


@sug_bp.route("/<int:id>/anexar", methods=["POST"])
@login_required
def anexar(id):
    """Autor (ou master) acrescenta mais anexos depois de enviada."""
    _so_equipe()
    sug = SugestaoMelhoria.query.get_or_404(id)
    if not _pode_ver(sug):
        abort(403)
    qtd = _salvar_anexos(sug)
    db.session.commit()
    flash(f"{qtd} anexo(s) adicionado(s)." if qtd else "Nenhum arquivo escolhido.", "ok" if qtd else "erro")
    return redirect(url_for("sug.detalhe", id=id))


@sug_bp.route("/anexo/<int:anexo_id>")
@login_required
def anexo(anexo_id):
    _so_equipe()
    a = AnexoSugestao.query.get_or_404(anexo_id)
    if not _pode_ver(a.sugestao):
        abort(403)
    # imagens abrem no navegador (miniatura/visualizacao); o resto baixa
    return send_from_directory(os.path.dirname(a.caminho), os.path.basename(a.caminho),
                               as_attachment=not a.e_imagem, download_name=a.nome_original)


@sug_bp.route("/<int:id>/decidir", methods=["POST"])
@login_required
def decidir(id):
    if not current_user.is_master():
        abort(403)
    sug = SugestaoMelhoria.query.get_or_404(id)
    status = request.form.get("status")
    if status not in STATUS_SUGESTAO:
        abort(400)
    sug.resposta = request.form.get("resposta", "").strip() or None
    mudou = status != sug.status
    sug.status = status
    if status == "pendente":
        sug.decidido_por = None
        sug.decidido_em = None
    elif mudou or not sug.decidido_em:
        sug.decidido_por = current_user.id
        sug.decidido_em = agora_brasilia()
    db.session.commit()
    if mudou and status != "pendente" and sug.autor_id != current_user.id:
        try:
            from app.email_service import notificar_decisao_sugestao
            notificar_decisao_sugestao(sug)
        except Exception:
            pass
    rotulo = {"acatada": "acatada", "recusada": "recusada", "pendente": "pendente de novo"}[status]
    flash(f"Sugestão marcada como {rotulo}.", "ok")
    return redirect(url_for("sug.detalhe", id=id))


@sug_bp.route("/<int:id>/excluir", methods=["POST"])
@login_required
def excluir(id):
    """Autor apaga enquanto pendente; master apaga qualquer uma."""
    _so_equipe()
    sug = SugestaoMelhoria.query.get_or_404(id)
    if not (current_user.is_master() or (sug.autor_id == current_user.id and sug.status == "pendente")):
        abort(403)
    for a in sug.anexos:
        try:
            os.remove(a.caminho)
        except OSError:
            pass
    db.session.delete(sug)
    db.session.commit()
    flash("Sugestão excluída.", "ok")
    return redirect(url_for("sug.lista"))
