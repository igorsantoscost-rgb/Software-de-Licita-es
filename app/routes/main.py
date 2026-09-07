from flask import Blueprint, render_template, redirect, url_for, request, flash
from flask_login import login_required, current_user
from app.models import Licitacao, Cliente, User, STATUS_CHOICES, PalavraChaveCliente
from app import db, bcrypt
from app.capag import UFS
from datetime import datetime, date, timedelta
import calendar

main_bp = Blueprint("main", __name__)


# Status que aparecem no painel geral (aba "Todos"), na ordem desejada
STATUS_ATIVOS = ["agendada", "em disputa", "em julgamento", "em habilitacao"]
# Ordem de prioridade para exibição no painel geral
_ORDEM_STATUS = {s: i for i, s in enumerate(STATUS_ATIVOS)}


def _licitacoes_do_usuario(status_filtro=None, cliente_filtro=None):
    q = Licitacao.query
    if not current_user.is_assessor():
        # Cliente sempre vê apenas as próprias licitações
        q = q.filter_by(cliente_id=current_user.cliente_id)
    elif cliente_filtro and cliente_filtro != "todos":
        # Assessor pode filtrar por um cliente específico
        q = q.filter_by(cliente_id=cliente_filtro)
    if status_filtro and status_filtro != "todos":
        q = q.filter_by(status=status_filtro)
    else:
        # Painel geral ("Todos"): mostra apenas status ativos
        q = q.filter(Licitacao.status.in_(STATUS_ATIVOS))
    lics = q.order_by(Licitacao.data_disputa.asc()).all()
    if status_filtro == "todos" or not status_filtro:
        # Ordena por prioridade de status, depois por data
        lics.sort(key=lambda l: (_ORDEM_STATUS.get(l.status, 99), l.data_disputa or datetime.max))
    return lics


@main_bp.route("/painel")
@login_required
def painel():
    status_filtro = request.args.get("status", "todos")
    cliente_filtro = request.args.get("cliente_id", "todos")
    licitacoes = _licitacoes_do_usuario(status_filtro, cliente_filtro)
    clientes = Cliente.query.order_by(Cliente.nome).all() if current_user.is_assessor() else []
    return render_template(
        "painel.html",
        licitacoes=licitacoes,
        status_choices=STATUS_CHOICES,
        status_filtro=status_filtro,
        cliente_filtro=cliente_filtro,
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
        hoje=date.today(),
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
            return render_template("form_cliente.html", ufs=UFS)
        cliente = Cliente(
            nome=nome,
            cnpj=cnpj,
            rua=request.form.get("rua", "").strip(),
            numero=request.form.get("numero", "").strip(),
            complemento=request.form.get("complemento", "").strip(),
            bairro=request.form.get("bairro", "").strip(),
            cidade=request.form.get("cidade", "").strip(),
            estado=request.form.get("estado", "").strip().upper(),
            cep=request.form.get("cep", "").strip(),
            nome_contato=request.form.get("nome_contato", "").strip(),
            cargo_contato=request.form.get("cargo_contato", "").strip(),
            telefone_fixo=request.form.get("telefone_fixo", "").strip(),
            telefone_wpp=request.form.get("telefone_wpp", "").strip(),
            email_contato=request.form.get("email_contato", "").strip(),
            email_financeiro=request.form.get("email_financeiro", "").strip(),
            cor=request.form.get("cor", "").strip() or None,
        )
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
    return render_template("form_cliente.html", ufs=UFS)


@main_bp.route("/clientes/<int:id>/editar", methods=["GET", "POST"])
@login_required
def editar_cliente(id):
    if not current_user.is_assessor():
        return redirect(url_for("main.painel"))
    cliente = Cliente.query.get_or_404(id)
    if request.method == "POST":
        nome = request.form.get("nome", "").strip()
        if not nome:
            flash("O nome da empresa é obrigatório.", "erro")
            return render_template("form_cliente_editar.html", cliente=cliente, ufs=UFS)
        cliente.nome = nome
        cliente.cnpj = request.form.get("cnpj", "").strip()
        cliente.rua = request.form.get("rua", "").strip()
        cliente.numero = request.form.get("numero", "").strip()
        cliente.complemento = request.form.get("complemento", "").strip()
        cliente.bairro = request.form.get("bairro", "").strip()
        cliente.cidade = request.form.get("cidade", "").strip()
        cliente.estado = request.form.get("estado", "").strip().upper()
        cliente.cep = request.form.get("cep", "").strip()
        cliente.nome_contato = request.form.get("nome_contato", "").strip()
        cliente.cargo_contato = request.form.get("cargo_contato", "").strip()
        cliente.telefone_fixo = request.form.get("telefone_fixo", "").strip()
        cliente.telefone_wpp = request.form.get("telefone_wpp", "").strip()
        cliente.email_contato = request.form.get("email_contato", "").strip()
        cliente.email_financeiro = request.form.get("email_financeiro", "").strip()
        cliente.cor = request.form.get("cor", "").strip() or None
        taxa_str = request.form.get("taxa_consultoria", "").strip().replace(",", ".")
        if taxa_str:
            try:
                cliente.taxa_consultoria = float(taxa_str)
            except ValueError:
                pass
        db.session.commit()
        flash("Cliente atualizado.", "ok")
        return redirect(url_for("main.clientes"))
    return render_template("form_cliente_editar.html", cliente=cliente, ufs=UFS)


@main_bp.route("/clientes/<int:id>/palavras-chave/adicionar", methods=["POST"])
@login_required
def adicionar_palavra_chave(id):
    if not current_user.is_assessor():
        return redirect(url_for("main.painel"))
    cliente = Cliente.query.get_or_404(id)
    texto = request.form.get("palavra", "").strip()
    if texto:
        # Permite colar varias palavras separadas por virgula de uma vez
        novas = [p.strip() for p in texto.split(",") if p.strip()]
        existentes = {p.palavra.lower() for p in cliente.palavras_chave}
        for p in novas:
            if p.lower() not in existentes:
                db.session.add(PalavraChaveCliente(cliente_id=cliente.id, palavra=p))
                existentes.add(p.lower())
        db.session.commit()
        flash("Palavra(s)-chave adicionada(s).", "ok")
    return redirect(url_for("main.editar_cliente", id=id))


@main_bp.route("/clientes/palavras-chave/<int:palavra_id>/excluir", methods=["POST"])
@login_required
def excluir_palavra_chave(palavra_id):
    if not current_user.is_assessor():
        return redirect(url_for("main.painel"))
    palavra = PalavraChaveCliente.query.get_or_404(palavra_id)
    cliente_id = palavra.cliente_id
    db.session.delete(palavra)
    db.session.commit()
    flash("Palavra-chave removida.", "ok")
    return redirect(url_for("main.editar_cliente", id=cliente_id))


# ─── Gestão de Assessores (só master) ────────────────────────────────────────

@main_bp.route("/assessores")
@login_required
def assessores():
    if not current_user.is_master():
        return redirect(url_for("main.painel"))
    todos = User.query.filter_by(perfil="assessor").order_by(User.nome).all()
    return render_template("assessores.html", assessores=todos)


@main_bp.route("/assessores/novo", methods=["GET", "POST"])
@login_required
def novo_assessor():
    if not current_user.is_master():
        return redirect(url_for("main.painel"))
    clientes = Cliente.query.order_by(Cliente.nome).all()
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        nome = request.form.get("nome", "").strip()
        email = request.form.get("email", "").strip().lower() or None
        senha = request.form.get("senha", "")
        telefone = request.form.get("telefone", "").strip()
        endereco = request.form.get("endereco", "").strip()
        clientes_ids = request.form.getlist("clientes_ids")
        if not username or not nome or not senha:
            flash("Nome de usuário, nome e senha são obrigatórios.", "erro")
            return render_template("form_assessor.html", clientes=clientes)
        if User.query.filter(db.func.lower(User.username) == username.lower()).first():
            flash("Esse nome de usuário já está em uso.", "erro")
            return render_template("form_assessor.html", clientes=clientes)
        assessor = User(
            username=username,
            nome=nome,
            email=email,
            senha=bcrypt.generate_password_hash(senha).decode("utf-8"),
            perfil="assessor",
            telefone=telefone,
            endereco=endereco,
        )
        db.session.add(assessor)
        db.session.flush()
        for cid in clientes_ids:
            c = Cliente.query.get(int(cid))
            if c:
                assessor.clientes_atendidos.append(c)
        db.session.commit()
        flash(f"Assessor {nome} criado com sucesso.", "ok")
        return redirect(url_for("main.assessores"))
    return render_template("form_assessor.html", clientes=clientes, assessor=None)


@main_bp.route("/assessores/<int:id>/editar", methods=["GET", "POST"])
@login_required
def editar_assessor(id):
    if not current_user.is_master():
        return redirect(url_for("main.painel"))
    assessor = User.query.get_or_404(id)
    clientes = Cliente.query.order_by(Cliente.nome).all()
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        nome = request.form.get("nome", "").strip()
        email = request.form.get("email", "").strip().lower() or None
        telefone = request.form.get("telefone", "").strip()
        endereco = request.form.get("endereco", "").strip()
        nova_senha = request.form.get("senha", "").strip()
        clientes_ids = request.form.getlist("clientes_ids")
        if not username or not nome:
            flash("Nome de usuário e nome são obrigatórios.", "erro")
            return render_template("form_assessor.html", clientes=clientes, assessor=assessor)
        conflito = User.query.filter(
            db.func.lower(User.username) == username.lower(),
            User.id != id
        ).first()
        if conflito:
            flash("Esse nome de usuário já está em uso.", "erro")
            return render_template("form_assessor.html", clientes=clientes, assessor=assessor)
        assessor.username = username
        assessor.nome = nome
        assessor.email = email
        assessor.telefone = telefone
        assessor.endereco = endereco
        if nova_senha:
            assessor.senha = bcrypt.generate_password_hash(nova_senha).decode("utf-8")
        # Atualiza vinculos
        assessor.clientes_atendidos = []
        for cid in clientes_ids:
            c = Cliente.query.get(int(cid))
            if c:
                assessor.clientes_atendidos.append(c)
        db.session.commit()
        flash("Assessor atualizado.", "ok")
        return redirect(url_for("main.assessores"))
    return render_template("form_assessor.html", clientes=clientes, assessor=assessor)


# ─── Gestão de Acessos (só master) ───────────────────────────────────────────

@main_bp.route("/gestao-acessos")
@login_required
def gestao_acessos():
    if not current_user.is_master():
        return redirect(url_for("main.painel"))
    usuarios = User.query.order_by(User.perfil, User.nome).all()
    return render_template("gestao_acessos.html", usuarios=usuarios)


@main_bp.route("/gestao-acessos/<int:id>/editar", methods=["GET", "POST"])
@login_required
def editar_acesso(id):
    if not current_user.is_master():
        return redirect(url_for("main.painel"))
    usuario = User.query.get_or_404(id)
    clientes = Cliente.query.order_by(Cliente.nome).all()
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        nome = request.form.get("nome", "").strip()
        email = request.form.get("email", "").strip().lower() or None
        nova_senha = request.form.get("senha", "").strip()
        perfil = request.form.get("perfil", usuario.perfil)
        cliente_id = request.form.get("cliente_id") or None
        if not username or not nome:
            flash("Nome de usuário e nome são obrigatórios.", "erro")
            return render_template("form_acesso.html", usuario=usuario, clientes=clientes)
        conflito = User.query.filter(
            db.func.lower(User.username) == username.lower(),
            User.id != id
        ).first()
        if conflito:
            flash("Esse nome de usuário já está em uso.", "erro")
            return render_template("form_acesso.html", usuario=usuario, clientes=clientes)
        usuario.username = username
        usuario.nome = nome
        usuario.email = email
        usuario.perfil = perfil
        usuario.cliente_id = int(cliente_id) if cliente_id else None
        if nova_senha:
            usuario.senha = bcrypt.generate_password_hash(nova_senha).decode("utf-8")
        db.session.commit()
        flash("Acesso atualizado.", "ok")
        return redirect(url_for("main.gestao_acessos"))
    return render_template("form_acesso.html", usuario=usuario, clientes=clientes)
