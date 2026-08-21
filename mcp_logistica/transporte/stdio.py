"""Transporte stdio: mensajes JSON-RPC delimitados por salto de línea (NDJSON).

Es el transporte que usa la versión local. El cliente (Claude Desktop) lanza este
proceso como subproceso y habla con él por sus tuberías estándar:

    cliente --stdin--> servidor      una línea = un mensaje JSON-RPC
    cliente <-stdout-- servidor      una línea = un mensaje JSON-RPC
    cliente <-stderr-- servidor      bitácora, texto libre

Los mensajes no salen nunca del sistema operativo: no hay socket, no hay ruteo y
no hay intermediarios. Esa es la justificación de la ejecución local que plantea
la propuesta, porque los pedidos y el padrón de clientes son información interna.

Regla que no se puede romper: **a stdout no se escribe nada que no sea un mensaje
MCP**. Un ``print()`` de depuración perdido corrompe el flujo y el cliente
desconecta el servidor.
"""

from __future__ import annotations

import datetime as dt
import logging
import sys
from typing import Any, TextIO

from ..protocolo.despachador import Despachador
from ..protocolo.jsonrpc import serializar

registro = logging.getLogger(__name__)


class TransporteStdio:
    """Bucle de lectura y escritura sobre las tuberías estándar."""

    def __init__(
        self,
        entrada: TextIO | None = None,
        salida: TextIO | None = None,
        registro_trafico: TextIO | None = None,
    ) -> None:
        self.entrada = entrada if entrada is not None else sys.stdin
        self.salida = salida if salida is not None else sys.stdout
        self.registro_trafico = registro_trafico

    def ejecutar(self, despachador: Despachador) -> int:
        registro.info("Transporte stdio listo, esperando mensajes JSON-RPC por stdin")

        for linea_cruda in self.entrada:
            # El BOM se quita antes de nada: PowerShell antepone ﻿ al
            # canalizar hacia un ejecutable nativo, y un BOM nunca forma parte de
            # un mensaje JSON-RPC. Sin esto, el primer mensaje del handshake
            # (justo el initialize) fallaría con error de parseo en Windows.
            linea = linea_cruda.lstrip("﻿").strip()
            if not linea:
                # Líneas en blanco entre mensajes: se ignoran sin ruido.
                continue

            self._anotar("<<", linea)
            respuesta = despachador.manejar_linea(linea)

            if respuesta is None:
                # Era una notificación: JSON-RPC prohíbe responderle.
                continue

            self._escribir(respuesta)

        registro.info("stdin cerrado, el servidor termina de forma limpia")
        return 0

    def _escribir(self, mensaje: dict[str, Any]) -> None:
        linea = serializar(mensaje)
        self._anotar(">>", linea)
        self.salida.write(linea + "\n")
        # El flush es obligatorio: sin él la respuesta se queda en el búfer y el
        # cliente se queda esperando hasta que expira el tiempo de espera.
        self.salida.flush()

    def _anotar(self, direccion: str, linea: str) -> None:
        """Vuelca el tráfico a un archivo, si se pidió con --registro-trafico."""
        if self.registro_trafico is None:
            return
        marca = dt.datetime.now().isoformat(timespec="milliseconds")
        self.registro_trafico.write(f"{marca} {direccion} {linea}\n")
        self.registro_trafico.flush()


def preparar_tuberias() -> None:
    """Fuerza UTF-8 y saltos de línea LF en las tuberías estándar.

    MCP exige que los mensajes sean UTF-8. En Windows la codificación por defecto
    de la consola es cp1252, así que sin esto un acento en un nombre de cliente
    rompería el mensaje. Y ``newline="\\n"`` evita que Windows convierta cada
    salto en CRLF, lo que agregaría un byte extra al framing por líneas.
    """
    for flujo, opciones in (
        (sys.stdin, {"encoding": "utf-8", "errors": "replace"}),
        (sys.stdout, {"encoding": "utf-8", "newline": "\n"}),
        (sys.stderr, {"encoding": "utf-8", "errors": "replace"}),
    ):
        # Si el flujo fue reemplazado (por ejemplo por un StringIO en las
        # pruebas) no tiene reconfigure y no hay nada que ajustar.
        if hasattr(flujo, "reconfigure"):
            flujo.reconfigure(**opciones)
