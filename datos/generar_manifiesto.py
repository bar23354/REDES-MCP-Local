"""Recalcula datos/manifiesto.json a partir de los CSV de prueba.

El manifiesto simula el checksum que cada sucursal envía junto con su archivo de
pedidos: es el 'crc_esperado' que se le pasa a la herramienta validar_recepcion.

Correr después de editar cualquier CSV de datos/:

    python datos/generar_manifiesto.py
"""

from __future__ import annotations

import json
import pathlib
import sys

RAIZ = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(RAIZ.parent))

from mcp_logistica.nucleo.recepcion import calcular_crc32  # noqa: E402


def main() -> int:
    manifiesto: dict[str, dict[str, object]] = {}

    for csv in sorted(RAIZ.glob("*.csv")):
        # Se lee con newline="" para no dejar que Python traduzca los saltos:
        # la normalización a LF la hace calcular_crc32, igual que en producción.
        with open(csv, encoding="utf-8", newline="") as archivo:
            texto = archivo.read()
        lineas = [ln for ln in texto.replace("\r\n", "\n").split("\n") if ln.strip()]
        manifiesto[csv.name] = {
            "crc32": calcular_crc32(texto),
            "registros": max(len(lineas) - 1, 0),
        }

    destino = RAIZ / "manifiesto.json"
    destino.write_text(
        json.dumps(manifiesto, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )

    for nombre, datos in manifiesto.items():
        print(f"{nombre}: CRC-32 {datos['crc32']} ({datos['registros']} registros)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
