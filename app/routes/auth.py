from flask import Blueprint, render_template, redirect, url_for, request, flash
from flask_login import login_user, logout_user, login_required
from app.models import User
from app import bcrypt, db

auth_bp = Blueprint("auth", __name__)

@auth_bp.route("/", methods=["GET"])
def index():
    return redirect(url_for("auth.login"))

@auth_bp.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        senha = request.form.get("senha", "")
        user = User.query.filter(
            db.func.lower(User.username) == username.lower()
        ).first()
        if user and bcrypt.check_password_hash(user.senha, senha):
            remember = request.form.get("remember") == "on"
            login_user(user, remember=remember)
            return redirect(url_for("main.painel"))
        flash("Usuário ou senha incorretos.", "erro")
    return render_template("login.html")

@auth_bp.route("/logout")
@login_required
def logout():
    logout_user()
    return redirect(url_for("auth.login"))
