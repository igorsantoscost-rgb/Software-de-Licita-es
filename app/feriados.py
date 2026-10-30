"""
Feriados nacionais: datas fixas + Sexta-feira Santa (calculada pela Pascoa).
Pontos facultativos (Carnaval, Corpus Christi) nao entram.
"""
from datetime import date, timedelta

_FIXOS = {
    (1, 1): "Confraterniza\u00e7\u00e3o Universal",
    (4, 21): "Tiradentes",
    (5, 1): "Dia do Trabalho",
    (9, 7): "Independ\u00eancia do Brasil",
    (10, 12): "Nossa Senhora Aparecida",
    (11, 2): "Finados",
    (11, 15): "Proclama\u00e7\u00e3o da Rep\u00fablica",
    (11, 20): "Dia da Consci\u00eancia Negra",
    (12, 25): "Natal",
}


def _pascoa(ano):
    """Domingo de Pascoa (algoritmo de Meeus/Jones/Butcher)."""
    a = ano % 19
    b, c = divmod(ano, 100)
    d, e = divmod(b, 4)
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = divmod(c, 4)
    l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l) // 451
    mes = (h + l - 7 * m + 114) // 31
    dia = ((h + l - 7 * m + 114) % 31) + 1
    return date(ano, mes, dia)


def feriados_entre(inicio, fim):
    """{date: nome} dos feriados nacionais entre as duas datas (inclusive)."""
    out = {}
    for ano in range(inicio.year, fim.year + 1):
        dias = {date(ano, m, d): nome for (m, d), nome in _FIXOS.items()}
        dias[_pascoa(ano) - timedelta(days=2)] = "Sexta-feira Santa"
        for d, nome in dias.items():
            if inicio <= d <= fim:
                out[d] = nome
    return out
