from app import db
from flask_login import UserMixin
from datetime import datetime

# Tabela de vinculo N:N entre assessores e clientes
assessor_clientes = db.Table(
    "assessor_clientes",
    db.Column("assessor_id", db.Integer, db.ForeignKey("users.id"), primary_key=True),
    db.Column("cliente_id", db.Integer, db.ForeignKey("clientes.id"), primary_key=True),
)


class User(UserMixin, db.Model):
    __tablename__ = "users"
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    nome = db.Column(db.String(120), nullable=False)
    email = db.Column(db.String(120), unique=True, nullable=True)  # so pra notificacoes
    senha = db.Column(db.String(255), nullable=False)
    perfil = db.Column(db.String(20), nullable=False, default="cliente")  # master | assessor | cliente
    cliente_id = db.Column(db.Integer, db.ForeignKey("clientes.id"), nullable=True)
    telefone = db.Column(db.String(20), nullable=True)
    endereco = db.Column(db.String(300), nullable=True)
    criado_em = db.Column(db.DateTime, default=datetime.utcnow)

    # Clientes que este assessor/master atende (N:N)
    clientes_atendidos = db.relationship(
        "Cliente", secondary=assessor_clientes,
        backref=db.backref("assessores", lazy="dynamic"),
        lazy="dynamic",
    )

    def is_master(self):
        return self.perfil == "master"

    def is_assessor(self):
        """Retorna True para assessor E master (ambos podem operar licitacoes)."""
        return self.perfil in ("assessor", "master")

    def is_assessor_puro(self):
        """Retorna True apenas para assessor (nao master)."""
        return self.perfil == "assessor"

    def pode_ver_cliente(self, cliente_id):
        """Master ve tudo. Assessor ve so clientes vinculados. Cliente ve so o proprio."""
        if self.perfil == "master":
            return True
        if self.perfil == "assessor":
            return self.clientes_atendidos.filter_by(id=cliente_id).first() is not None
        return self.cliente_id == cliente_id


class Cliente(db.Model):
    __tablename__ = "clientes"
    id = db.Column(db.Integer, primary_key=True)
    nome = db.Column(db.String(200), nullable=False)  # Razao Social
    cnpj = db.Column(db.String(20), nullable=True)
    # Endereco
    rua = db.Column(db.String(300), nullable=True)
    numero = db.Column(db.String(20), nullable=True)
    complemento = db.Column(db.String(100), nullable=True)
    bairro = db.Column(db.String(100), nullable=True)
    cidade = db.Column(db.String(100), nullable=True)
    estado = db.Column(db.String(2), nullable=True)
    cep = db.Column(db.String(10), nullable=True)
    # Contato
    nome_contato = db.Column(db.String(200), nullable=True)
    cargo_contato = db.Column(db.String(100), nullable=True)
    telefone_fixo = db.Column(db.String(20), nullable=True)
    telefone_wpp = db.Column(db.String(20), nullable=True)
    email_contato = db.Column(db.String(150), nullable=True)  # Avisos
    email_financeiro = db.Column(db.String(150), nullable=True)  # Boletos
    criado_em = db.Column(db.DateTime, default=datetime.utcnow)
    usuarios = db.relationship("User", backref="cliente", lazy=True, foreign_keys=[User.cliente_id])
    licitacoes = db.relationship("Licitacao", backref="cliente", lazy=True)
    palavras_chave = db.relationship("PalavraChaveCliente", backref="cliente", lazy=True,
                                     cascade="all, delete-orphan", order_by="PalavraChaveCliente.palavra")


class PalavraChaveCliente(db.Model):
    __tablename__ = "palavras_chave_cliente"
    id = db.Column(db.Integer, primary_key=True)
    cliente_id = db.Column(db.Integer, db.ForeignKey("clientes.id"), nullable=False)
    palavra = db.Column(db.String(150), nullable=False)
    criado_em = db.Column(db.DateTime, default=datetime.utcnow)


STATUS_CHOICES = [
    "agendada",
    "em disputa",
    "em julgamento",
    "em habilitacao",
    "homologada",
    "revogada",
    "cancelada",
    "encerrada",
]

PORTAL_CHOICES = [
    "ComprasNet",
    "BLL",
    "Licitanet",
    "PCP",
    "Banco do Brasil",
    "Licitar Digital",
    "BCN SP",
    "BNC Bahia",
    "PROCERGS",
    "AMMLICITA",
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
    valor_homologado = db.Column(db.Numeric(14, 2), nullable=True)
    motivo_encerramento = db.Column(db.String(300), nullable=True)
    resumo_ia = db.Column(db.Text, nullable=True)
    # CAPAG (Capacidade de Pagamento - Tesouro Nacional)
    esfera = db.Column(db.String(20), nullable=True)        # federal | estadual | municipal
    uf = db.Column(db.String(2), nullable=True)
    municipio = db.Column(db.String(150), nullable=True)
    capag_nota = db.Column(db.String(5), nullable=True)     # A+, A, B+, B, C, D
    capag_ambito = db.Column(db.String(20), nullable=True)  # municipio | estado
    capag_local = db.Column(db.String(160), nullable=True)  # ex: "Belo Horizonte/MG" ou "Minas Gerais"
    capag_referencia = db.Column(db.String(80), nullable=True)
    capag_consultado_em = db.Column(db.DateTime, nullable=True)
    criado_em = db.Column(db.DateTime, default=datetime.utcnow)
    atualizado_em = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    documentos = db.relationship("Documento", backref="licitacao", lazy=True, cascade="all, delete-orphan")
    itens = db.relationship("ItemLicitacao", backref="licitacao", lazy=True, cascade="all, delete-orphan")


class Documento(db.Model):
    __tablename__ = "documentos"
    id = db.Column(db.Integer, primary_key=True)
    licitacao_id = db.Column(db.Integer, db.ForeignKey("licitacoes.id"), nullable=False)
    categoria = db.Column(db.String(20), nullable=False, default="processo")  # processo | apoio
    tipo = db.Column(db.String(30), nullable=False, default="outros")  # edital | termo_referencia | outros
    nome_original = db.Column(db.String(300), nullable=False)
    caminho = db.Column(db.String(500), nullable=False)
    tamanho = db.Column(db.Integer, nullable=True)
    enviado_por = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)
    criado_em = db.Column(db.DateTime, default=datetime.utcnow)


# Tipos de documento da licitação que tem um slot fixo no formulário
# (apenas 1 arquivo cada, pode ser substituido). "outros" e a lista livre.
TIPOS_DOC_LICITACAO_UNICOS = {
    "edital": "Edital",
    "termo_referencia": "Termo de Referência",
}


# Documentos organizados por setor.
# Cada setor: (nome_do_setor, [(slug, rotulo), ...])
SETORES_DOCUMENTOS = [
    ("Regularidade Fiscal", [
        ("cnd_federal", "CND Federal"),
        ("cnd_municipal", "CND Municipal"),
        ("cnd_estadual", "CND Estadual"),
        ("cnd_fgts", "CND FGTS"),
        ("tcu", "Consulta Consolidada TCU"),
        ("cadin", "Consulta CADIN"),
    ]),
    ("Qualificação Técnica", [
        ("atestado_tecnico", "Atestado de Capacidade Técnica"),
        ("alvara_sanitario", "Alvará Sanitário"),
        ("alvara_funcionamento", "Alvará de Funcionamento"),
    ]),
    ("Qualificação Econômico-Financeira", [
        ("certidao_falencia", "Certidão Negativa de Falência e Concordata"),
        ("balanco_ultimo", "Último Balanço Patrimonial"),
        ("balanco_penultimo", "Penúltimo Balanço Patrimonial"),
    ]),
    ("Contrato e Credenciamento", [
        ("contrato_social", "Contrato Social / Contrato Consolidado"),
        ("alteracao_contratual", "Alteração Contratual"),
        ("inscricao_estado", "Prova de Inscrição no Estado"),
        ("inscricao_municipio", "Prova de Inscrição no Município"),
        ("doc_socio", "Documento de Identificação do Sócio"),
        ("doc_conjuge", "Identificação do Cônjuge do Sócio"),
        ("estado_civil", "Comprovação de Estado Civil"),
    ]),
    ("Outros Documentos", [
        ("outros_documentos", "Outros Documentos"),
    ]),
]

# Lista achatada (mantida para compatibilidade com o resto do código)
DOCUMENTOS_CLIENTE = [
    (slug, label)
    for _setor, _docs in SETORES_DOCUMENTOS
    for slug, label in _docs
]

DOCUMENTO_TIPOS = [slug for slug, _ in DOCUMENTOS_CLIENTE]

# Tipos que aceitam VÁRIOS arquivos (não substituem o anterior)
TIPOS_MULTIPLOS = {"alteracao_contratual", "outros_documentos"}

# Tipos opcionais — nunca contam como pendência
TIPOS_OPCIONAIS = {"outros_documentos"}


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
    numero_item = db.Column(db.String(20), nullable=True)
    descricao = db.Column(db.String(500), nullable=False)
    marca = db.Column(db.String(200), nullable=True)
    lote_grupo = db.Column(db.String(100), nullable=True)
    valor_minimo = db.Column(db.Numeric(14, 2), nullable=True)
    unidade = db.Column(db.String(50), nullable=True)
    quantidade = db.Column(db.Integer, nullable=True)
    criado_em = db.Column(db.DateTime, default=datetime.utcnow)


class ComentarioLicitacao(db.Model):
    __tablename__ = "comentarios_licitacao"
    id = db.Column(db.Integer, primary_key=True)
    licitacao_id = db.Column(db.Integer, db.ForeignKey("licitacoes.id"), nullable=False)
    autor_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    texto = db.Column(db.Text, nullable=False)
    criado_em = db.Column(db.DateTime, default=datetime.utcnow)
    editado_em = db.Column(db.DateTime, nullable=True)

    licitacao = db.relationship("Licitacao", backref=db.backref("comentarios", lazy=True, order_by="ComentarioLicitacao.criado_em", cascade="all, delete-orphan"))
    autor = db.relationship("User")


class ObservacaoApoio(db.Model):
    __tablename__ = "observacoes_apoio"
    id = db.Column(db.Integer, primary_key=True)
    licitacao_id = db.Column(db.Integer, db.ForeignKey("licitacoes.id"), nullable=False)
    autor_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    texto = db.Column(db.Text, nullable=False)
    criado_em = db.Column(db.DateTime, default=datetime.utcnow)

    licitacao = db.relationship("Licitacao", backref=db.backref("observacoes_apoio", lazy=True, order_by="ObservacaoApoio.criado_em", cascade="all, delete-orphan"))
    autor = db.relationship("User")


# ─── CAPAG (base de notas importada do Tesouro Nacional) ─────────────────────

class CapagEstado(db.Model):
    __tablename__ = "capag_estados"
    id = db.Column(db.Integer, primary_key=True)
    uf = db.Column(db.String(2), unique=True, nullable=False, index=True)
    classificacao = db.Column(db.String(5), nullable=True)   # A+, A, B+, B, C, D
    referencia = db.Column(db.String(80), nullable=True)
    atualizado_em = db.Column(db.DateTime, default=datetime.utcnow)


class CapagMunicipio(db.Model):
    __tablename__ = "capag_municipios"
    id = db.Column(db.Integer, primary_key=True)
    cod_ibge = db.Column(db.String(10), nullable=True, index=True)
    uf = db.Column(db.String(2), nullable=False, index=True)
    nome = db.Column(db.String(150), nullable=False)
    nome_normalizado = db.Column(db.String(150), nullable=False, index=True)
    classificacao = db.Column(db.String(5), nullable=True)
    referencia = db.Column(db.String(80), nullable=True)
    atualizado_em = db.Column(db.DateTime, default=datetime.utcnow)


# ─── EMPENHOS ────────────────────────────────────────────────────────────────

STATUS_EMPENHO = [
    "recebido",
    "em atendimento",
    "aguardando pagamento",
    "pago",
]

TIPO_EMPENHO = ["ordinario", "global"]


class Empenho(db.Model):
    __tablename__ = "empenhos"
    id = db.Column(db.Integer, primary_key=True)
    licitacao_id = db.Column(db.Integer, db.ForeignKey("licitacoes.id"), nullable=False)
    cliente_id = db.Column(db.Integer, db.ForeignKey("clientes.id"), nullable=False)
    numero_empenho = db.Column(db.String(60), nullable=True)
    contrato = db.Column(db.String(100), nullable=True)  # pode ser vazio
    tipo = db.Column(db.String(20), nullable=False, default="ordinario")  # ordinario | global
    valor_total = db.Column(db.Numeric(14, 2), nullable=True)
    prazo_entrega = db.Column(db.Date, nullable=True)
    status = db.Column(db.String(30), nullable=False, default="recebido")
    # Datas de transicao de status
    data_ciencia = db.Column(db.DateTime, nullable=True)     # cliente confirmou recebimento
    data_despacho = db.Column(db.DateTime, nullable=True)    # cliente informou despacho
    data_pagamento = db.Column(db.DateTime, nullable=True)
    criado_em = db.Column(db.DateTime, default=datetime.utcnow)
    atualizado_em = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    licitacao = db.relationship("Licitacao", backref=db.backref("empenhos", lazy=True))
    cliente = db.relationship("Cliente", backref=db.backref("empenhos", lazy=True))
    documentos = db.relationship("DocumentoEmpenho", backref="empenho", lazy=True, cascade="all, delete-orphan")
    observacoes = db.relationship("ObservacaoEmpenho", backref="empenho", lazy=True,
                                   order_by="ObservacaoEmpenho.criado_em", cascade="all, delete-orphan")


class DocumentoEmpenho(db.Model):
    __tablename__ = "documentos_empenho"
    id = db.Column(db.Integer, primary_key=True)
    empenho_id = db.Column(db.Integer, db.ForeignKey("empenhos.id"), nullable=False)
    nome_original = db.Column(db.String(300), nullable=False)
    caminho = db.Column(db.String(500), nullable=False)
    tamanho = db.Column(db.Integer, nullable=True)
    enviado_por = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)
    criado_em = db.Column(db.DateTime, default=datetime.utcnow)


class ObservacaoEmpenho(db.Model):
    __tablename__ = "observacoes_empenho"
    id = db.Column(db.Integer, primary_key=True)
    empenho_id = db.Column(db.Integer, db.ForeignKey("empenhos.id"), nullable=False)
    autor_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    texto = db.Column(db.Text, nullable=False)
    criado_em = db.Column(db.DateTime, default=datetime.utcnow)
    autor = db.relationship("User")
