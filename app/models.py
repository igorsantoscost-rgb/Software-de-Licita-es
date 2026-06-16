from app import db
from flask_login import UserMixin
from datetime import datetime

class User(UserMixin, db.Model):
    __tablename__ = "users"
    id = db.Column(db.Integer, primary_key=True)
    nome = db.Column(db.String(120), nullable=False)
    email = db.Column(db.String(120), unique=True, nullable=False)
    senha = db.Column(db.String(255), nullable=False)
    perfil = db.Column(db.String(20), nullable=False, default="cliente")  # assessor | cliente
    cliente_id = db.Column(db.Integer, db.ForeignKey("clientes.id"), nullable=True)
    criado_em = db.Column(db.DateTime, default=datetime.utcnow)

    def is_assessor(self):
        return self.perfil == "assessor"


class Cliente(db.Model):
    __tablename__ = "clientes"
    id = db.Column(db.Integer, primary_key=True)
    nome = db.Column(db.String(200), nullable=False)
    cnpj = db.Column(db.String(20), nullable=True)
    criado_em = db.Column(db.DateTime, default=datetime.utcnow)
    usuarios = db.relationship("User", backref="cliente", lazy=True, foreign_keys=[User.cliente_id])
    licitacoes = db.relationship("Licitacao", backref="cliente", lazy=True)


STATUS_CHOICES = [
    "agendada",
    "em disputa",
    "em julgamento",
    "em habilitacao",
    "homologada",
    "revogada",
    "cancelada",
]

PORTAL_CHOICES = [
    "ComprasNet",
    "BLL",
    "Licitanet",
    "Portal Nacional de Contratacoes Publicas (PNCP)",
    "Banco do Brasil",
    "Outro",
]


class Licitacao(db.Model):
    __tablename__ = "licitacoes"
    id = db.Column(db.Integer, primary_key=True)
    cliente_id = db.Column(db.Integer, db.ForeignKey("clientes.id"), nullable=False)
    orgao_licitante = db.Column(db.String(300), nullable=False)
    numero_pregao = db.Column(db.String(60), nullable=False)
    uasg = db.Column(db.String(30), nullable=True)
    portal = db.Column(db.String(100), nullable=True)
    data_disputa = db.Column(db.DateTime, nullable=True)
    status = db.Column(db.String(30), nullable=False, default="agendada")
    objeto = db.Column(db.Text, nullable=True)
    link_edital = db.Column(db.String(500), nullable=True)
    resumo_ia = db.Column(db.Text, nullable=True)
    criado_em = db.Column(db.DateTime, default=datetime.utcnow)
    atualizado_em = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    documentos = db.relationship("Documento", backref="licitacao", lazy=True, cascade="all, delete-orphan")
    itens = db.relationship("ItemLicitacao", backref="licitacao", lazy=True, cascade="all, delete-orphan")


class Documento(db.Model):
    __tablename__ = "documentos"
    id = db.Column(db.Integer, primary_key=True)
    licitacao_id = db.Column(db.Integer, db.ForeignKey("licitacoes.id"), nullable=False)
    nome_original = db.Column(db.String(300), nullable=False)
    caminho = db.Column(db.String(500), nullable=False)
    tamanho = db.Column(db.Integer, nullable=True)
    enviado_por = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)
    criado_em = db.Column(db.DateTime, default=datetime.utcnow)


DOCUMENTOS_CLIENTE = [
    ("cnd_federal", "CND Federal"),
    ("cnd_municipal", "CND Municipal"),
    ("cnd_estadual", "CND Estadual"),
    ("cnd_fgts", "CND FGTS"),
    ("tcu", "Consulta Consolidada TCU"),
    ("cadin", "Consulta CADIN"),
    ("certidao_falencia", "Certidão Negativa de Falência e Concordata"),
    ("contrato_social", "Contrato Social / Contrato Consolidado"),
    ("alteracao_contratual", "Alteração Contratual"),
    ("inscricao_estado", "Prova de Inscrição no Estado"),
    ("inscricao_municipio", "Prova de Inscrição no Município"),
    ("alvara_funcionamento", "Alvará de Funcionamento"),
    ("alvara_sanitario", "Alvará Sanitário"),
    ("doc_socio", "Documento de Identificação do Sócio"),
    ("doc_conjuge", "Identificação do Cônjuge do Sócio"),
    ("estado_civil", "Comprovação de Estado Civil"),
    ("atestado_tecnico", "Atestado de Capacidade Técnica"),
]

DOCUMENTO_TIPOS = [slug for slug, _ in DOCUMENTOS_CLIENTE]


class DocumentoCliente(db.Model):
    __tablename__ = "documentos_cliente"
    id = db.Column(db.Integer, primary_key=True)
    cliente_id = db.Column(db.Integer, db.ForeignKey("clientes.id"), nullable=False)
    tipo = db.Column(db.String(60), nullable=False)
    nome_original = db.Column(db.String(300), nullable=False)
    caminho = db.Column(db.String(500), nullable=False)
    tamanho = db.Column(db.Integer, nullable=True)
    validade = db.Column(db.Date, nullable=True)
    nao_se_aplica = db.Column(db.Boolean, default=False)
    obs = db.Column(db.Text, nullable=True)
    enviado_por = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)
    criado_em = db.Column(db.DateTime, default=datetime.utcnow)


class ItemLicitacao(db.Model):
    __tablename__ = "itens_licitacao"
    id = db.Column(db.Integer, primary_key=True)
    licitacao_id = db.Column(db.Integer, db.ForeignKey("licitacoes.id"), nullable=False)
    descricao = db.Column(db.String(500), nullable=False)
    lote_grupo = db.Column(db.String(100), nullable=True)
    valor_minimo = db.Column(db.Numeric(14, 2), nullable=True)
    unidade = db.Column(db.String(50), nullable=True)
    quantidade = db.Column(db.Integer, nullable=True)
    criado_em = db.Column(db.DateTime, default=datetime.utcnow)
