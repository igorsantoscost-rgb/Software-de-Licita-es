"""Desenvolvimento (so master): sugestoes de melhoria da equipe e status das
atualizacoes do sistema (auto-deploy)."""
from flask import Blueprint, render_template, request, abort
from flask_login import login_required, current_user

from app.models import SugestaoMelhoria, STATUS_SUGESTAO
from app.versao import status_sistema, historico_deploys

dev_bp = Blueprint("dev", __name__, url_prefix="/desenvolvimento")


@dev_bp.route("/")
@login_required
def painel():
    if not current_user.is_master():
        abort(403)
    filtro = request.args.get("status", "pendente")
    q = SugestaoMelhoria.query
    if filtro in STATUS_SUGESTAO:
        q = q.filter_by(status=filtro)
    sugestoes = q.order_by(SugestaoMelhoria.criado_em.desc()).all()
    contagem = {s: SugestaoMelhoria.query.filter_by(status=s).count() for s in STATUS_SUGESTAO}
    contagem["todas"] = sum(contagem.values())
    return render_template("desenvolvimento.html", sugestoes=sugestoes, filtro=filtro, contagem=contagem,
                           deploys=historico_deploys())
