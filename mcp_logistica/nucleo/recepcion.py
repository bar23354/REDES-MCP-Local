"""Validación de integridad de los lotes de pedidos que llegan de las sucursales.

Un lote es el texto completo de un archivo CSV que una sucursal envía al CEDIS,
acompañado del CRC-32 que calculó en origen. Aquí se responden dos preguntas
distintas que conviene no mezclar:

1. ¿El archivo llegó igual a como salió?  -> lo contesta el CRC-32.
2. ¿Los datos que trae tienen sentido?    -> lo contesta la validación por registro.

El CRC-32 de ``zlib`` usa el polinomio reflejado 0xEDB88320, el mismo que emplea
el FCS de las tramas Ethernet. Es decir, esta herramienta aplica a nivel de
aplicación la misma técnica de detección de errores que la capa de enlace.
"""

from __future__ import annotations

import re
import zlib
from typing import Any

# Encabezado que toda sucursal debe respetar en el archivo de pedidos.
COLUMNAS = ("id_pedido", "sucursal", "destino", "peso_kg", "volumen_m3")

# Formato acordado para el identificador de pedido: PED- seguido de 4 dígitos.
PATRON_ID = re.compile(r"^PED-\d{4}$")

# Estados posibles del lote, de peor a mejor.
CRC_NO_COINCIDE = "CRC_NO_COINCIDE"
REGISTROS_CORRUPTOS = "REGISTROS_CORRUPTOS"
INTEGRO = "INTEGRO"


def normalizar_contenido(contenido: str) -> str:
    """Deja el texto con saltos de línea LF y sin BOM.

    El contenido llega como *string* desde el chatbot, no como bytes. Si no se
    normalizara, un archivo guardado en Windows (CRLF) daría un CRC distinto al
    que calculó la sucursal en Linux (LF) aunque los datos fueran idénticos, y
    todos los lotes se reportarían como corruptos.
    """
    return contenido.lstrip("\ufeff").replace("\r\n", "\n").replace("\r", "\n")


def calcular_crc32(contenido: str) -> str:
    """CRC-32 del contenido normalizado, en 8 dígitos hexadecimales minúsculos."""
    datos = normalizar_contenido(contenido).encode("utf-8")
    return format(zlib.crc32(datos) & 0xFFFFFFFF, "08x")


def normalizar_crc(valor: str) -> str:
    """Acepta las variantes con las que suele venir un CRC y las unifica.

    ``0x1A2B3C4D``, ``1a2b3c4d`` y ``1A2B 3C4D`` son el mismo valor.
    """
    limpio = "".join(valor.split()).lower()
    if limpio.startswith("0x"):
        limpio = limpio[2:]
    return limpio.rjust(8, "0")


def _validar_registro(campos: list[str], vistos: set[str]) -> tuple[dict[str, Any] | None, list[str]]:
    """Valida una fila. Devuelve (pedido, motivos); pedido es None si hay motivos."""
    motivos: list[str] = []

    if len(campos) != len(COLUMNAS):
        # Sin el número correcto de columnas no se puede interpretar nada más.
        return None, ["columnas_invalidas"]

    id_pedido, sucursal, destino, peso_txt, volumen_txt = (c.strip() for c in campos)

    if not PATRON_ID.match(id_pedido):
        motivos.append("id_invalido")
    elif id_pedido in vistos:
        motivos.append("id_duplicado")

    if not sucursal:
        motivos.append("sucursal_vacia")

    if not destino:
        motivos.append("destino_vacio")

    peso = _a_numero(peso_txt)
    if peso is None or peso <= 0:
        motivos.append("peso_invalido")

    volumen = _a_numero(volumen_txt)
    if volumen is None or volumen <= 0:
        motivos.append("volumen_invalido")

    if motivos:
        return None, motivos

    return {
        "id_pedido": id_pedido,
        "sucursal": sucursal,
        "destino": destino,
        "peso_kg": peso,
        "volumen_m3": volumen,
    }, []


def _a_numero(texto: str) -> float | None:
    try:
        return float(texto)
    except ValueError:
        return None


def validar_lote(contenido: str, crc_esperado: str) -> dict[str, Any]:
    """Valida integridad de transmisión y calidad de los registros de un lote."""
    crc_calculado = calcular_crc32(contenido)
    crc_referencia = normalizar_crc(crc_esperado)
    crc_coincide = crc_calculado == crc_referencia

    lineas = [ln for ln in normalizar_contenido(contenido).split("\n") if ln.strip()]

    encabezado_valido = False
    inicio = 0
    if lineas:
        primera = [c.strip().lower() for c in lineas[0].split(",")]
        if tuple(primera) == COLUMNAS:
            encabezado_valido = True
            inicio = 1

    pedidos_validos: list[dict[str, Any]] = []
    registros_corruptos: list[dict[str, Any]] = []
    vistos: set[str] = set()

    for desplazamiento, linea in enumerate(lineas[inicio:]):
        # Número de línea tal como lo vería el usuario al abrir el archivo.
        numero_linea = inicio + desplazamiento + 1
        campos = linea.split(",")
        pedido, motivos = _validar_registro(campos, vistos)

        if pedido is None:
            registros_corruptos.append(
                {
                    "linea": numero_linea,
                    "id_pedido": campos[0].strip() if campos else "",
                    "motivos": motivos,
                    "contenido_crudo": linea,
                }
            )
        else:
            vistos.add(pedido["id_pedido"])
            pedidos_validos.append(pedido)

    if not crc_coincide:
        estado = CRC_NO_COINCIDE
    elif registros_corruptos or not encabezado_valido:
        estado = REGISTROS_CORRUPTOS
    else:
        estado = INTEGRO

    total = len(lineas) - inicio
    return {
        "estado_lote": estado,
        "crc_calculado": crc_calculado,
        "crc_esperado": crc_referencia,
        "crc_coincide": crc_coincide,
        "encabezado_valido": encabezado_valido,
        "total_registros": total,
        "registros_validos": len(pedidos_validos),
        "total_corruptos": len(registros_corruptos),
        "registros_corruptos": registros_corruptos,
        "pedidos_validos": pedidos_validos,
        "resumen": _resumir(estado, crc_calculado, crc_referencia, len(pedidos_validos), total),
    }


def _resumir(estado: str, calculado: str, esperado: str, validos: int, total: int) -> str:
    if estado == CRC_NO_COINCIDE:
        return (
            f"El lote se alteró en tránsito: se esperaba CRC-32 {esperado} y se calculó "
            f"{calculado}. No se debe procesar; hay que pedir el reenvío del archivo."
        )
    if estado == REGISTROS_CORRUPTOS:
        return (
            f"El archivo llegó íntegro (CRC-32 {calculado}), pero {total - validos} de {total} "
            f"registros tienen errores de captura en origen. Los {validos} válidos sí se pueden "
            f"consolidar."
        )
    return f"Lote íntegro: CRC-32 {calculado} coincide y los {total} registros son válidos."
