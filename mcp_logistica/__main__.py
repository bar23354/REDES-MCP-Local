"""Punto de entrada del servidor MCP.

    python -m mcp_logistica                       transporte stdio (por defecto)
    python -m mcp_logistica --log-nivel DEBUG     bitácora detallada en stderr
    python -m mcp_logistica --registro-trafico t.log
                                                  guarda cada mensaje entrante y
                                                  saliente para el informe
"""

from __future__ import annotations

import argparse
import logging
import sys

from . import NOMBRE_SERVIDOR, VERSION
from .protocolo.despachador import Despachador
from .transporte.stdio import TransporteStdio, preparar_tuberias


def _argumentos(argv: list[str] | None = None) -> argparse.Namespace:
    analizador = argparse.ArgumentParser(
        prog="mcp_logistica",
        description="Servidor MCP local de logística (CEDIS). JSON-RPC 2.0 sin SDK.",
    )
    analizador.add_argument(
        "--transporte",
        choices=["stdio"],
        default="stdio",
        help="Transporte a usar. Por ahora solo stdio; HTTP llega en la fase remota.",
    )
    analizador.add_argument(
        "--log-nivel",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Nivel de la bitácora que se escribe en stderr.",
    )
    analizador.add_argument(
        "--registro-trafico",
        metavar="ARCHIVO",
        help=(
            "Archivo donde volcar cada mensaje JSON-RPC entrante (<<) y saliente (>>) "
            "con su marca de tiempo. Sirve como evidencia del intercambio."
        ),
    )
    analizador.add_argument("--version", action="version", version=f"{NOMBRE_SERVIDOR} {VERSION}")
    return analizador.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _argumentos(argv)

    # La bitácora va SIEMPRE a stderr: stdout está reservado para los mensajes MCP.
    logging.basicConfig(
        stream=sys.stderr,
        level=getattr(logging, args.log_nivel),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    preparar_tuberias()

    archivo_trafico = None
    if args.registro_trafico:
        archivo_trafico = open(args.registro_trafico, "a", encoding="utf-8")

    try:
        logging.getLogger(__name__).info("Iniciando %s %s", NOMBRE_SERVIDOR, VERSION)
        transporte = TransporteStdio(registro_trafico=archivo_trafico)
        return transporte.ejecutar(Despachador())
    except KeyboardInterrupt:
        return 0
    finally:
        if archivo_trafico is not None:
            archivo_trafico.close()


if __name__ == "__main__":
    raise SystemExit(main())
