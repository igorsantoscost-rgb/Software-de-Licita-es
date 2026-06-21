from flask import Flask
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager
from flask_bcrypt import Bcrypt
import os

db = SQLAlchemy()
login_manager = LoginManager()
bcrypt = Bcrypt()

def create_app():
    app = Flask(__name__)
    app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY", "dev-secret")
    app.config["SQLALCHEMY_DATABASE_URI"] = os.environ.get("DATABASE_URL", "sqlite:///licitacoes.db")
    app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
    app.config["UPLOAD_FOLDER"] = "/app/uploads"
    app.config["MAX_CONTENT_LENGTH"] = 50 * 1024 * 1024  # 50MB

    db.init_app(app)
    login_manager.init_app(app)
    bcrypt.init_app(app)

    login_manager.login_view = "auth.login"
    login_manager.login_message = "Faca login para acessar."

    from app.models import User

    @login_manager.user_loader
    def load_user(user_id):
        return User.query.get(int(user_id))

    from app.routes.auth import auth_bp
    from app.routes.main import main_bp
    from app.routes.licitacoes import lic_bp
    from app.routes.docs_cliente import docs_bp

    app.register_blueprint(auth_bp)
    app.register_blueprint(main_bp)
    app.register_blueprint(lic_bp)
    app.register_blueprint(docs_bp)

    with app.app_context():
        db.create_all()
        _migrar_coluna_tipo_documento()
        _seed_admin(app)

    return app


def _migrar_coluna_tipo_documento():
    """Adiciona a coluna 'tipo' na tabela documentos se o banco for de uma versao anterior."""
    from sqlalchemy import text
    try:
        with db.engine.connect() as conn:
            resultado = conn.execute(text("""
                SELECT column_name FROM information_schema.columns
                WHERE table_name = 'documentos' AND column_name = 'tipo'
            """))
            if resultado.first() is None:
                conn.execute(text(
                    "ALTER TABLE documentos ADD COLUMN tipo VARCHAR(30) NOT NULL DEFAULT 'outros'"
                ))
                conn.commit()
    except Exception:
        # Se for sqlite ou outro banco sem information_schema, ignora
        # (db.create_all() ja cobre o caso de banco novo/vazio).
        pass

def _seed_admin(app):
    from app.models import User
    from app import bcrypt
    if not User.query.filter_by(email="admin@consultoria.com").first():
        admin = User(
            nome="Administrador",
            email="admin@consultoria.com",
            senha=bcrypt.generate_password_hash("admin123").decode("utf-8"),
            perfil="assessor"
        )
        db.session.add(admin)
        db.session.commit()
