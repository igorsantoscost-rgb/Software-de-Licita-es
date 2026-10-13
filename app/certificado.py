"""
Certificados digitais (.pfx / .p12) NUNCA entram em nenhum .zip do sistema
(pasta de habilitação do cliente, lixeira, backup completo).

A conferência olha a extensão do nome e também o conteúdo do arquivo:
um certificado renomeado (ex.: "certificado.pdf") continua sendo barrado.
"""
import os

EXTENSOES_CERTIFICADO = {".pfx", ".p12"}

# Inicio de um arquivo PKCS#12: SEQUENCE, versao 3 e o identificador "pkcs7-data"
_VERSAO_PKCS12 = b"\x02\x01\x03"
_OID_PKCS7 = b"\x06\x09\x2a\x86\x48\x86\xf7\x0d\x01\x07"


def _conteudo_e_certificado(caminho):
    try:
        with open(caminho, "rb") as f:
            inicio = f.read(64)
    except OSError:
        return False
    return (len(inicio) > 12 and inicio[0] == 0x30
            and _VERSAO_PKCS12 in inicio[:10] and _OID_PKCS7 in inicio[:48])


def e_certificado(*nomes, caminho=None):
    """True se algum dos nomes tem extensao de certificado ou se o arquivo e um."""
    for nome in nomes:
        if nome and os.path.splitext(str(nome))[1].lower() in EXTENSOES_CERTIFICADO:
            return True
    return bool(caminho) and _conteudo_e_certificado(caminho)
