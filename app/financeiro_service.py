"""Servico financeiro — cria faturas e adiciona itens automaticamente."""

from datetime import date, timedelta
from decimal import Decimal
from app import db
from app.models import Fatura, ItemFatura

PERCENTUAL_EMPENHO = Decimal("0.02")  # 2%


def _primeiro_dia_mes(dt=None):
    """Retorna o primeiro dia do mes da data informada (ou hoje)."""
    d = dt or date.today()
    return d.replace(day=1)


def _vencimento_fatura(mes_ref):
    """Dia 10 do mes seguinte ao mes de referencia."""
    if mes_ref.month == 12:
        return date(mes_ref.year + 1, 1, 10)
    return date(mes_ref.year, mes_ref.month + 1, 10)


def obter_ou_criar_fatura(cliente, mes_ref=None):
    """Busca a fatura do mes para o cliente, ou cria uma nova."""
    mes = _primeiro_dia_mes(mes_ref)
    fatura = Fatura.query.filter_by(
        cliente_id=cliente.id,
        mes_referencia=mes,
    ).first()

    if not fatura:
        taxa = cliente.taxa_consultoria if cliente.taxa_consultoria is not None else Decimal("1621.00")
        # Verifica se e a primeira fatura do cliente (taxa de implantacao)
        primeira = Fatura.query.filter_by(cliente_id=cliente.id).first() is None
        fatura = Fatura(
            cliente_id=cliente.id,
            mes_referencia=mes,
            taxa_consultoria=taxa,
            taxa_implantacao=Decimal("2000.00") if primeira else Decimal("0"),
            vencimento=_vencimento_fatura(mes),
            status="aberta",
        )
        db.session.add(fatura)
        db.session.flush()

    return fatura


def adicionar_empenho_na_fatura(empenho):
    """Adiciona 2% do valor do empenho na fatura do mes atual do cliente.
    Chamado automaticamente ao cadastrar um empenho."""
    if not empenho.valor_total:
        return None

    fatura = obter_ou_criar_fatura(empenho.cliente)

    # Verifica se ja existe item pra esse empenho (evita duplicata)
    existente = ItemFatura.query.filter_by(
        fatura_id=fatura.id,
        empenho_id=empenho.id,
    ).first()
    if existente:
        return fatura

    valor_comissao = Decimal(str(empenho.valor_total)) * PERCENTUAL_EMPENHO
    item = ItemFatura(
        fatura_id=fatura.id,
        empenho_id=empenho.id,
        descricao=f"2% do Empenho {empenho.numero_empenho or '#' + str(empenho.id)} — {empenho.licitacao.orgao_licitante}",
        valor=valor_comissao,
    )
    db.session.add(item)
    db.session.commit()
    return fatura
