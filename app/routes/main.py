from flask import Blueprint, render_template, redirect, url_for, request, flash
from flask_login import login_required, current_user
from app.models import Licitacao, Cliente, User, STATUS_CHOICES
from app import db, bcrypt
from datetime import datetime, date, timedelta
import calendar

main_bp = Blueprint("main", __name__)


def _licitacoes_do_usuario(status_filtro=None):
    q = Licitacao.query
    if not current_user.is_assessor():
        q = q.filter_by(cliente_id=current_user.cliente_id)
    if status_filtro and status_filtro != "todos":
        q = q.filter_by(status=status_filtro)
    return q.order_by(Licitacao.data_disputa.asc()).all()


@main_bp.route("/painel")
@login_required
def painel():
    status_filtro = request.args.get("status", "todos")
    licitacoes = _licitacoes_do_usuario(status_filtro)
    clientes = Cliente.query.all() if current_user.is_assessor() else []
    return render_template(
        "painel.html",
        licitacoes=licitacoes,
        status_choices=STATUS_CHOICES,
        status_filtro=status_filtro,
        clientes=clientes,
    )


@main_bp.route("/calendario")
@login_required
def calendario():
    mes = request.args.get("mes", type=int, default=date.today().month)
    ano = request.args.get("ano", type=int, default=date.today().year)
    if mes < 1: mes, ano = 12, ano - 1
    if mes > 12: mes, ano = 1, ano + 1

    primeiro_dia = date(ano, mes, 1)
    ultimo_dia = date(ano, mes, calendar.monthrange(ano, mes)[1])

    q = Licitacao.query.filter(
        Licitacao.data_disputa >= datetime(ano, mes, 1),
        Licitacao.data_disputa <= datetime(ano, mes, ultimo_dia.day, 23, 59, 59),
    )
    if not current_user.is_assessor():
        q = q.filter(Licitacao.cliente_id == current_user.cliente_id)
    licitacoes_mes = q.all()

    eventos = {}
    for l in licitacoes_mes:
        d = l.data_disputa.date()
        eventos.setdefault(d, []).append(l)

    calendar.setfirstweekday(6)  # 6 = domingo (calendar usa 0=segunda por padrao)
    semanas = calendar.monthcalendar(ano, mes)
    nomes_meses = [
        "", "Janeiro", "Fevereiro", "Marco", "Abril", "Maio", "Junho",
        "Julho", "Agosto", "Setembro", "Outubro", "Novembro", "Dezembro"
    ]

    return render_template(
        "calendario.html",
        semanas=semanas,
        eventos=eventos,
        mes=mes,
        ano=ano,
        nome_mes=nomes_meses[mes],
        primeiro_dia=primeiro_dia,
    )


@main_bp.route("/calendario/semana")
@login_required
def calendario_semana():
    inicio_str = request.args.get("inicio")
    if inicio_str:
        try:
            inicio = datetime.strptime(inicio_str, "%Y-%m-%d").date()
        except ValueError:
            inicio = date.today()
    else:
        inicio = date.today()

    # Volta para o domingo da semana de 'inicio' (igual ao calendario mensal: domingo primeiro)
    inicio = inicio - timedelta(days=(inicio.weekday() + 1) % 7)
    fim = inicio + timedelta(days=6)

    dias_semana = [inicio + timedelta(days=i) for i in range(7)]

    q = Licitacao.query.filter(
        Licitacao.data_disputa >= datetime(inicio.year, inicio.month, inicio.day),
        Licitacao.data_disputa <= datetime(fim.year, fim.month, fim.day, 23, 59, 59),
    )
    if not current_user.is_assessor():
        q = q.filter(Licitacao.cliente_id == current_user.cliente_id)
    licitacoes_semana = q.order_by(Licitacao.data_disputa.asc()).all()

    eventos = {}
    for l in licitacoes_semana:
        d = l.data_disputa.date()
        eventos.setdefault(d, []).append(l)

    nomes_meses_curto = [
        "", "Jan", "Fev", "Mar", "Abr", "Mai", "Jun",
        "Jul", "Ago", "Set", "Out", "Nov", "Dez"
    ]

    return render_template(
        "calendario_semana.html",
        dias_semana=dias_semana,
        eventos=eventos,
        inicio=inicio,
        fim=fim,
        semana_anterior=inicio - timedelta(days=7),
        semana_seguinte=inicio + timedelta(days=7),
        nomes_meses_curto=nomes_meses_curto,
        hoje=date.today(),
    )


# ─── Gerenciar clientes (assessor) ───────────────────────────────────────────

@main_bp.route("/clientes")
@login_required
def clientes():
    if not current_user.is_assessor():
        return redirect(url_for("main.painel"))
    todos = Cliente.query.order_by(Cliente.nome).all()
    return render_template("clientes.html", clientes=todos)


@main_bp.route("/clientes/novo", methods=["GET", "POST"])
@login_required
def novo_cliente():
    if not current_user.is_assessor():
        return redirect(url_for("main.painel"))
    if request.method == "POST":
        nome = request.form.get("nome", "").strip()
        cnpj = request.form.get("cnpj", "").strip()
        email_user = request.form.get("email_usuario", "").strip().lower()
        senha_user = request.form.get("senha_usuario", "")
        nome_user = request.form.get("nome_usuario", "").strip()
        if not nome or not email_user or not senha_user:
            flash("Preencha todos os campos obrigatorios.", "erro")
            return render_template("form_cliente.html")
        cliente = Cliente(nome=nome, cnpj=cnpj)
        db.session.add(cliente)
        db.session.flush()
        user = User(
            nome=nome_user,
            email=email_user,
            senha=bcrypt.generate_password_hash(senha_user).decode("utf-8"),
            perfil="cliente",
            cliente_id=cliente.id,
        )
        db.session.add(user)
        db.session.commit()
        flash("Cliente criado com sucesso.", "ok")
        return redirect(url_for("main.clientes"))
    return render_template("form_cliente.html")
