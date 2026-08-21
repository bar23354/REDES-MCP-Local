"""Despachador de métodos MCP.

Este es el punto que comparten los dos transportes: stdio le entrega un ``dict``
leído de una línea de stdin, y el transporte HTTP de la siguiente fase le
entregará un ``dict`` leído del cuerpo de un POST. El despachador no sabe ni le
importa de dónde vino el mensaje, que es justamente lo que permite que el
chatbot use el servidor remoto igual que el local.

Ciclo de vida MCP que se implementa:

    cliente -> initialize                  -> servidor responde capacidades
    cliente -> notifications/initialized   -> (notificación: sin respuesta)
    cliente -> tools/list                  -> servidor responde las 3 herramientas
    cliente -> tools/call                  -> servidor ejecuta y responde
"""

from __future__ import annotations

import json
import logging
from typing import Any

from .. import NOMBRE_SERVIDOR, TITULO_SERVIDOR, VERSION
from . import herramientas
from .jsonrpc import (
    INTERNAL_ERROR,
    INVALID_PARAMS,
    METHOD_NOT_FOUND,
    ErrorRPC,
    es_notificacion,
    parsear_mensaje,
    respuesta_error,
    respuesta_exito,
    validar_solicitud,
)

registro = logging.getLogger(__name__)
VERSIONES_SOPORTADAS = ("2025-11-25", "2025-06-18", "2025-03-26", "2024-11-05")
VERSION_PREFERIDA = "2025-06-18"

INSTRUCCIONES = (
    "Servidor de logística del CEDIS. El flujo normal encadena las tres herramientas: "
    "1) validar_recepcion sobre el archivo de pedidos que envió la sucursal, junto con su "
    "CRC-32; 2) consolidar_carga con la lista 'pedidos_validos' que devolvió el paso "
    "anterior y las capacidades del camión; 3) planificar_ruta con el CEDIS y los destinos "
    "de cada camión. Si validar_recepcion reporta CRC_NO_COINCIDE, no continúes: el archivo "
    "se alteró en tránsito y hay que pedir el reenvío."
)


class Despachador:
    """Traduce mensajes JSON-RPC a llamadas de herramientas y de vuelta."""

    def __init__(self) -> None:
        self.inicializado = False
        self.version_negociada = VERSION_PREFERIDA
        self.cliente: dict[str, Any] = {}

    # -- entrada principal -------------------------------------------------

    def manejar_linea(self, linea: str) -> dict[str, Any] | None:
        """Procesa una línea cruda. Devuelve la respuesta, o None si no lleva."""
        try:
            mensaje = parsear_mensaje(linea)
        except ErrorRPC as error:
            # Con un JSON ilegible no hay forma de saber el id: se responde con null.
            return respuesta_error(None, error.codigo, error.mensaje, error.datos)
        return self.manejar(mensaje)

    def manejar(self, mensaje: dict[str, Any]) -> dict[str, Any] | None:
        """Procesa un mensaje ya parseado."""
        notificacion = es_notificacion(mensaje)
        id_solicitud = mensaje.get("id")

        try:
            metodo, parametros, id_solicitud = validar_solicitud(mensaje)
            resultado = self._ejecutar(metodo, parametros)
        except ErrorRPC as error:
            if notificacion:
                registro.warning("Notificación con error, no se responde: %s", error.mensaje)
                return None
            return respuesta_error(id_solicitud, error.codigo, error.mensaje, error.datos)
        except Exception as exc:  # noqa: BLE001 - una herramienta no debe tumbar el servidor
            registro.exception("Fallo no controlado procesando el mensaje")
            if notificacion:
                return None
            return respuesta_error(
                id_solicitud, INTERNAL_ERROR, "Error interno del servidor", {"detalle": str(exc)}
            )

        # Una notificación nunca lleva respuesta, aunque se haya procesado bien.
        if notificacion:
            return None
        return respuesta_exito(id_solicitud, resultado)

    # -- métodos MCP -------------------------------------------------------

    def _ejecutar(self, metodo: str, parametros: dict[str, Any]) -> Any:
        if metodo == "initialize":
            return self._initialize(parametros)
        if metodo == "notifications/initialized":
            self.inicializado = True
            registro.info("Handshake completo con %s", self.cliente.get("name", "cliente"))
            return None
        if metodo in ("notifications/cancelled", "notifications/progress"):
            # Se aceptan y se ignoran: no hay operaciones largas que cancelar.
            return None
        if metodo == "ping":
            return {}
        if metodo == "tools/list":
            return {"tools": herramientas.listar_descriptores()}
        if metodo == "tools/call":
            return self._tools_call(parametros)

        raise ErrorRPC(METHOD_NOT_FOUND, f"Método no soportado: {metodo}")

    def _initialize(self, parametros: dict[str, Any]) -> dict[str, Any]:
        pedida = parametros.get("protocolVersion")
        self.version_negociada = (
            pedida if pedida in VERSIONES_SOPORTADAS else VERSION_PREFERIDA
        )
        if pedida and pedida not in VERSIONES_SOPORTADAS:
            registro.warning(
                "El cliente pidió la versión %s; se responde con %s", pedida, VERSION_PREFERIDA
            )

        cliente = parametros.get("clientInfo")
        if isinstance(cliente, dict):
            self.cliente = cliente

        return {
            "protocolVersion": self.version_negociada,
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": {
                "name": NOMBRE_SERVIDOR,
                "title": TITULO_SERVIDOR,
                "version": VERSION,
            },
            "instructions": INSTRUCCIONES,
        }

    def _tools_call(self, parametros: dict[str, Any]) -> dict[str, Any]:
        nombre = parametros.get("name")
        if not isinstance(nombre, str):
            raise ErrorRPC(INVALID_PARAMS, '"name" es obligatorio en tools/call')

        herramienta = herramientas.HERRAMIENTAS.get(nombre)
        if herramienta is None:
            disponibles = ", ".join(herramientas.HERRAMIENTAS)
            raise ErrorRPC(
                INVALID_PARAMS,
                f"Herramienta desconocida: {nombre}",
                {"disponibles": disponibles},
            )

        argumentos = parametros.get("arguments", {})
        if argumentos is None:
            argumentos = {}
        if not isinstance(argumentos, dict):
            raise ErrorRPC(INVALID_PARAMS, '"arguments" debe ser un objeto')

        try:
            resultado = herramienta.manejador(argumentos)
        except ErrorRPC:
            # Argumentos mal formados: es un error de protocolo, sube tal cual.
            raise
        except Exception as exc:  # noqa: BLE001
            # Un fallo de negocio se devuelve como resultado con isError, no como
            # error JSON-RPC, para que el modelo lo lea y pueda corregir el rumbo.
            registro.exception("Fallo ejecutando la herramienta %s", nombre)
            return self._empaquetar(
                {"error": f"No se pudo ejecutar {nombre}: {exc}"}, es_error=True
            )

        return self._empaquetar(resultado)

    @staticmethod
    def _empaquetar(resultado: dict[str, Any], es_error: bool = False) -> dict[str, Any]:
        """Arma el resultado de ``tools/call``.

        Se envía dos veces la misma información: como texto legible para el
        modelo, y como ``structuredContent`` para que el cliente pueda usarla
        como datos. No se declara ``outputSchema`` a propósito: declararlo
        obligaría a que ``structuredContent`` lo cumpla bajo validación estricta.
        """
        return {
            "content": [
                {"type": "text", "text": json.dumps(resultado, ensure_ascii=False, indent=2)}
            ],
            "structuredContent": resultado,
            "isError": es_error,
        }
