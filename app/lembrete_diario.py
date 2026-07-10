"""
Script de lembretes e alertas diarios do Bidfy.
Roda via cron todo dia as 8h.

Uso: python -m app.lembrete_diario
"""

from datetime import datetime
from app import create_app_for_cli
from app.email_service import (
    enviar_lembretes_diarios,
    enviar_alertas_vencimento_mensal,
    enviar_alertas_vencimento_semanal,
    enviar_lembretes_empenho_ciencia,
    enviar_lembretes_prazo_empenho,
)


def main():
    app = create_app_for_cli()
    with app.app_context():
        hoje = datetime.now()
        print(f"=== Bidfy Lembretes — {hoje.strftime('%d/%m/%Y %H:%M')} ===")

        # 1. Lembrete de pregoes amanha (todo dia)
        res = enviar_lembretes_diarios()
        print(f"Pregoes amanha: {res['total']} encontrados, {res['enviados']} lembretes enviados.")

        # 2. Alerta mensal de documentos (todo dia 1)
        if hoje.day == 1:
            res = enviar_alertas_vencimento_mensal()
            print(f"Alerta mensal docs: {res['clientes_alertados']} cliente(s) notificado(s).")
        else:
            print("Alerta mensal docs: nao e dia 1, pulando.")

        # 3. Alerta semanal de documentos (toda segunda = weekday 0)
        if hoje.weekday() == 0:
            res = enviar_alertas_vencimento_semanal()
            print(f"Alerta semanal docs: {res['clientes_alertados']} cliente(s) notificado(s).")
        else:
            print("Alerta semanal docs: nao e segunda-feira, pulando.")

        # 4. Lembrete empenhos sem ciencia > 2 dias (todo dia)
        res = enviar_lembretes_empenho_ciencia()
        print(f"Empenhos sem ciencia: {res['total']} encontrados, {res['enviados']} lembretes enviados.")

        # 5. Alerta prazo entrega empenho < 10 dias (todo dia)
        res = enviar_lembretes_prazo_empenho()
        print(f"Empenhos prazo proximo: {res['total']} encontrados, {res['enviados']} alertas enviados.")

        print("=== Concluido ===")


if __name__ == "__main__":
    main()
