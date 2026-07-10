"""
Script de lembrete diario.
Roda via cron (ex: todo dia as 8h) e envia e-mail para licitacoes
cuja disputa acontece no dia seguinte.

Uso:  python -m app.lembrete_diario
"""

from app import create_app_for_cli
from app.email_service import enviar_lembretes_diarios


def main():
    app = create_app_for_cli()
    with app.app_context():
        resultado = enviar_lembretes_diarios()
        total = resultado["total"]
        enviados = resultado["enviados"]
        if total == 0:
            print("Nenhuma licitacao com disputa amanha.")
        else:
            print(f"Licitacoes com disputa amanha: {total}. Lembretes enviados: {enviados}.")


if __name__ == "__main__":
    main()
