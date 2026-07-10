"""
Script de lembretes e alertas diarios do Bidfy.
Roda via cron todo dia as 8h.

Logica:
  - Todo dia      -> lembrete de pregoes com disputa amanha
  - Todo dia 1    -> alerta de documentos que vencem no mes
  - Toda segunda  -> alerta de documentos que vencem nos proximos 7 dias

Uso: python -m app.lembrete_diario
"""

from datetime import datetime
from app import create_app_for_cli
from app.email_service import (
    enviar_lembretes_diarios,
    enviar_alertas_vencimento_mensal,
    enviar_alertas_vencimento_semanal,
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
            print(f"Alerta mensal: {res['clientes_alertados']} cliente(s) notificado(s).")
        else:
            print("Alerta mensal: nao e dia 1, pulando.")

        # 3. Alerta semanal de documentos (toda segunda = weekday 0)
        if hoje.weekday() == 0:
            res = enviar_alertas_vencimento_semanal()
            print(f"Alerta semanal: {res['clientes_alertados']} cliente(s) notificado(s).")
        else:
            print("Alerta semanal: nao e segunda-feira, pulando.")

        print("=== Concluido ===")


if __name__ == "__main__":
    main()
