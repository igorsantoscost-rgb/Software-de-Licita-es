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

    from app.routes.empenhos import emp_bp
    app.register_blueprint(emp_bp)

    from app.routes.financeiro import fin_bp
    app.register_blueprint(fin_bp)

    @app.template_filter("markdown_seguro")
    def markdown_seguro(texto):
        """Converte markdown (texto gerado por IA) em HTML seguro para exibicao.
        Usa markupsafe para evitar reescapar o HTML ja gerado pelo markdown."""
        import markdown as md_lib
        from markupsafe import Markup
        import bleach

        if not texto:
            return ""
        html = md_lib.markdown(texto, extensions=["extra", "nl2br", "sane_lists"])
        tags_permitidas = [
            "h1", "h2", "h3", "h4", "p", "strong", "em", "ul", "ol", "li",
            "br", "hr", "blockquote", "code", "pre", "table", "thead",
            "tbody", "tr", "th", "td", "a",
        ]
        atributos_permitidos = {"a": ["href", "title", "target"]}
        html_limpo = bleach.clean(html, tags=tags_permitidas, attributes=atributos_permitidos, strip=True)
        return Markup(html_limpo)

    @app.template_filter("tem_comentario_assessor")
    def tem_comentario_assessor(licitacao):
        """True se a licitacao tem ao menos um comentario escrito por um assessor.
        Usado para mostrar o icone de recado (💬) no calendario."""
        if not licitacao.comentarios:
            return False
        return any(c.autor and c.autor.perfil == "assessor" for c in licitacao.comentarios)

    with app.app_context():
        try:
            db.create_all()
        except Exception:
            # Corrida entre workers do gunicorn — ignora se outro worker ja criou
            db.session.rollback()
        _migrar_coluna_tipo_documento()
        _seed_admin(app)
        _seed_capag_estados()

    return app


def create_app_for_cli():
    """Cria a app Flask para uso em scripts CLI (ex: lembrete_diario.py).
    Igual a create_app() mas sem seed/migrations — o banco ja existe."""
    app = Flask(__name__)
    app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY", "dev-secret")
    app.config["SQLALCHEMY_DATABASE_URI"] = os.environ.get("DATABASE_URL", "sqlite:///licitacoes.db")
    app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
    db.init_app(app)
    return app


def _migrar_coluna_tipo_documento():
    """Adiciona colunas novas se o banco for de uma versao anterior."""
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

            resultado2 = conn.execute(text("""
                SELECT column_name FROM information_schema.columns
                WHERE table_name = 'licitacoes' AND column_name = 'obs_cliente'
            """))
            if resultado2.first() is None:
                conn.execute(text(
                    "ALTER TABLE licitacoes ADD COLUMN obs_cliente TEXT"
                ))
                conn.commit()

            resultado3 = conn.execute(text("""
                SELECT column_name FROM information_schema.columns
                WHERE table_name = 'itens_licitacao' AND column_name = 'marca'
            """))
            if resultado3.first() is None:
                conn.execute(text(
                    "ALTER TABLE itens_licitacao ADD COLUMN marca VARCHAR(200)"
                ))
                conn.commit()

            resultado4 = conn.execute(text("""
                SELECT column_name FROM information_schema.columns
                WHERE table_name = 'itens_licitacao' AND column_name = 'numero_item'
            """))
            if resultado4.first() is None:
                conn.execute(text(
                    "ALTER TABLE itens_licitacao ADD COLUMN numero_item VARCHAR(20)"
                ))
                conn.commit()

            resultado5 = conn.execute(text("""
                SELECT column_name FROM information_schema.columns
                WHERE table_name = 'licitacoes' AND column_name = 'valor_homologado'
            """))
            if resultado5.first() is None:
                conn.execute(text(
                    "ALTER TABLE licitacoes ADD COLUMN valor_homologado NUMERIC(14,2)"
                ))
                conn.commit()

            resultado6 = conn.execute(text("""
                SELECT column_name FROM information_schema.columns
                WHERE table_name = 'licitacoes' AND column_name = 'motivo_encerramento'
            """))
            if resultado6.first() is None:
                conn.execute(text(
                    "ALTER TABLE licitacoes ADD COLUMN motivo_encerramento VARCHAR(300)"
                ))
                conn.commit()

            resultado7 = conn.execute(text("""
                SELECT column_name FROM information_schema.columns
                WHERE table_name = 'documentos' AND column_name = 'categoria'
            """))
            if resultado7.first() is None:
                conn.execute(text(
                    "ALTER TABLE documentos ADD COLUMN categoria VARCHAR(20) NOT NULL DEFAULT 'processo'"
                ))
                conn.commit()

            resultado8 = conn.execute(text("""
                SELECT column_name FROM information_schema.columns
                WHERE table_name = 'comentarios_licitacao' AND column_name = 'editado_em'
            """))
            if resultado8.first() is None:
                conn.execute(text(
                    "ALTER TABLE comentarios_licitacao ADD COLUMN editado_em TIMESTAMP"
                ))
                conn.commit()

            # Colunas CAPAG na tabela de licitacoes
            colunas_capag = {
                "esfera": "VARCHAR(20)",
                "uf": "VARCHAR(2)",
                "municipio": "VARCHAR(150)",
                "capag_nota": "VARCHAR(5)",
                "capag_ambito": "VARCHAR(20)",
                "capag_local": "VARCHAR(160)",
                "capag_referencia": "VARCHAR(80)",
                "capag_consultado_em": "TIMESTAMP",
            }
            for coluna, tipo in colunas_capag.items():
                existe = conn.execute(text("""
                    SELECT column_name FROM information_schema.columns
                    WHERE table_name = 'licitacoes' AND column_name = :col
                """), {"col": coluna})
                if existe.first() is None:
                    conn.execute(text(f"ALTER TABLE licitacoes ADD COLUMN {coluna} {tipo}"))
                    conn.commit()

            # Colunas novas na tabela de clientes (cadastro expandido)
            colunas_cliente = {
                "rua": "VARCHAR(300)",
                "numero": "VARCHAR(20)",
                "complemento": "VARCHAR(100)",
                "bairro": "VARCHAR(100)",
                "cidade": "VARCHAR(100)",
                "estado": "VARCHAR(2)",
                "cep": "VARCHAR(10)",
                "nome_contato": "VARCHAR(200)",
                "cargo_contato": "VARCHAR(100)",
                "telefone_fixo": "VARCHAR(20)",
                "telefone_wpp": "VARCHAR(20)",
                "email_contato": "VARCHAR(150)",
                "email_financeiro": "VARCHAR(150)",
                "taxa_consultoria": "NUMERIC(10,2) DEFAULT 1621.00",
                "cor": "VARCHAR(7)",
            }
            for coluna, tipo in colunas_cliente.items():
                existe = conn.execute(text("""
                    SELECT column_name FROM information_schema.columns
                    WHERE table_name = 'clientes' AND column_name = :col
                """), {"col": coluna})
                if existe.first() is None:
                    conn.execute(text(f"ALTER TABLE clientes ADD COLUMN {coluna} {tipo}"))
                    conn.commit()

            # Cores padrao para clientes conhecidos (so preenche quem ainda nao tem cor
            # definida, pra nunca sobrescrever um ajuste manual feito depois pelo assessor)
            cores_padrao = {
                "%meridian%": "#1e3a5f",   # azul marinho
                "%atacad%": "#eab308",     # amarelo (cobre Atacadao/Atacadão)
                "%fibralog%": "#f97316",   # laranja
            }
            for padrao, cor in cores_padrao.items():
                conn.execute(text("""
                    UPDATE clientes SET cor = :cor
                    WHERE cor IS NULL AND nome ILIKE :padrao
                """), {"cor": cor, "padrao": padrao})
                conn.commit()

            # Tabela N:N assessor_clientes
            conn.execute(text("""
                CREATE TABLE IF NOT EXISTS assessor_clientes (
                    assessor_id INTEGER NOT NULL REFERENCES users(id),
                    cliente_id INTEGER NOT NULL REFERENCES clientes(id),
                    PRIMARY KEY (assessor_id, cliente_id)
                )
            """))
            conn.commit()
    except Exception:
        # Se for sqlite ou outro banco sem information_schema, ignora
        # (db.create_all() ja cobre o caso de banco novo/vazio).
        pass

def _seed_admin(app):
    from app.models import User
    from app import bcrypt
    from sqlalchemy import text

    # Migra coluna username se nao existir
    try:
        with db.engine.connect() as conn:
            # username
            r = conn.execute(text("""
                SELECT column_name FROM information_schema.columns
                WHERE table_name='users' AND column_name='username'
            """))
            if r.first() is None:
                conn.execute(text("ALTER TABLE users ADD COLUMN username VARCHAR(80)"))
                conn.commit()
                # Preenche username com base no nome (sem espacos, minusculo) pra usuarios existentes
                conn.execute(text("""
                    UPDATE users SET username = LOWER(REPLACE(nome, ' ', '_'))
                    WHERE username IS NULL
                """))
                conn.commit()
                # Torna unique depois de preencher
                conn.execute(text("""
                    ALTER TABLE users ADD CONSTRAINT users_username_unique UNIQUE (username)
                """))
                conn.commit()
            # telefone
            r2 = conn.execute(text("""
                SELECT column_name FROM information_schema.columns
                WHERE table_name='users' AND column_name='telefone'
            """))
            if r2.first() is None:
                conn.execute(text("ALTER TABLE users ADD COLUMN telefone VARCHAR(20)"))
                conn.execute(text("ALTER TABLE users ADD COLUMN endereco VARCHAR(300)"))
                conn.commit()
            # email nullable
            conn.execute(text("""
                ALTER TABLE users ALTER COLUMN email DROP NOT NULL
            """))
            conn.commit()
    except Exception:
        pass

    # Promove admin existente a master e define username Igor
    admin = User.query.filter_by(email="admin@consultoria.com").first()
    if admin:
        admin.perfil = "master"
        if not admin.username or admin.username == "administrador":
            admin.username = "Igor"
        db.session.commit()

    # Cria master se nao existir nenhum
    if not User.query.filter_by(perfil="master").first():
        master = User(
            username="Igor",
            nome="Igor Costa",
            email="admin@consultoria.com",
            senha=bcrypt.generate_password_hash("admin123").decode("utf-8"),
            perfil="master",
        )
        db.session.add(master)
        db.session.commit()


def _seed_capag_estados():
    """Carrega as notas CAPAG dos estados a partir do CSV embutido, caso a
    tabela ainda esteja vazia. Garante que a consulta estadual funcione logo
    apos o deploy, antes mesmo da primeira atualizacao online da base."""
    import csv as _csv
    from datetime import datetime as _dt
    from app.models import CapagEstado
    try:
        if CapagEstado.query.first() is not None:
            return
        caminho = os.path.join(os.path.dirname(__file__), "data", "capag_estados_seed.csv")
        if not os.path.exists(caminho):
            return
        ref = "Tesouro Transparente (base 2025, ano-base 2024)"
        with open(caminho, encoding="utf-8") as f:
            for linha in _csv.DictReader(f):
                uf = (linha.get("uf") or "").strip().upper()
                if len(uf) != 2:
                    continue
                db.session.add(CapagEstado(
                    uf=uf,
                    classificacao=(linha.get("classificacao") or "").strip().upper(),
                    referencia=ref,
                ))
        db.session.commit()
    except Exception:
        db.session.rollback()
