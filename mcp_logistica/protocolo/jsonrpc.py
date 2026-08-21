"""JSON-RPC 2.0 implementado a mano, según la especificación oficial.

Esta capa no sabe nada de logística ni de MCP: solo arma, valida y serializa
mensajes. Los dos conceptos que hay que tener claros son:

* **Solicitud**: trae ``id``. Siempre se le debe responder, con éxito o error.
* **Notificación**: no trae ``id``. Nunca se le responde. Responder a una
  notificación es un error de protocolo que rompe al cliente.
"""

from __future__ import annotations

import json
from typing import Any

VERSION_JSONRPC = "2.0"

# Códigos de error reservados por la especificación JSON-RPC 2.0.
PARSE_ERROR = -32700
INVALID_REQUEST = -32600
METHOD_NOT_FOUND = -32601
INVALID_PARAMS = -32602
INTERNAL_ERROR = -32603


class ErrorRPC(Exception):
    """Error que se debe traducir a una respuesta de error JSON-RPC."""

    def __init__(self, codigo: int, mensaje: str, datos: Any = None) -> None:
        super().__init__(mensaje)
        self.codigo = codigo
        self.mensaje = mensaje
        self.datos = datos


def parsear_mensaje(linea: str) -> dict[str, Any]:
    """Convierte una línea de texto en un mensaje JSON-RPC."""
    try:
        mensaje = json.loads(linea)
    except json.JSONDecodeError as exc:
        raise ErrorRPC(PARSE_ERROR, "JSON inválido", {"detalle": str(exc)}) from exc

    if not isinstance(mensaje, dict):
        raise ErrorRPC(INVALID_REQUEST, "El mensaje debe ser un objeto JSON")

    return mensaje


def validar_solicitud(mensaje: dict[str, Any]) -> tuple[str, dict[str, Any], Any]:
    """Comprueba la forma del mensaje y devuelve (metodo, parametros, id)."""
    if mensaje.get("jsonrpc") != VERSION_JSONRPC:
        raise ErrorRPC(INVALID_REQUEST, 'El campo "jsonrpc" debe ser exactamente "2.0"')

    metodo = mensaje.get("method")
    if not isinstance(metodo, str) or not metodo:
        raise ErrorRPC(INVALID_REQUEST, 'El campo "method" debe ser una cadena no vacía')

    parametros = mensaje.get("params", {})
    if parametros is None:
        parametros = {}
    if not isinstance(parametros, dict):
        # Este servidor solo usa parámetros por nombre, no posicionales.
        raise ErrorRPC(INVALID_PARAMS, 'El campo "params" debe ser un objeto')

    return metodo, parametros, mensaje.get("id")


def es_notificacion(mensaje: dict[str, Any]) -> bool:
    """Una notificación es un mensaje sin ``id``; no lleva respuesta."""
    return "id" not in mensaje


def respuesta_exito(id_solicitud: Any, resultado: Any) -> dict[str, Any]:
    return {"jsonrpc": VERSION_JSONRPC, "id": id_solicitud, "result": resultado}


def respuesta_error(
    id_solicitud: Any, codigo: int, mensaje: str, datos: Any = None
) -> dict[str, Any]:
    error: dict[str, Any] = {"code": codigo, "message": mensaje}
    if datos is not None:
        error["data"] = datos
    return {"jsonrpc": VERSION_JSONRPC, "id": id_solicitud, "error": error}


def serializar(mensaje: dict[str, Any]) -> str:
    """Serializa un mensaje a una sola línea.

    ``json.dumps`` escapa los saltos de línea que vengan dentro de las cadenas,
    así que el resultado nunca contiene un salto embebido: es exactamente lo que
    exige el framing por líneas del transporte stdio.
    """
    return json.dumps(mensaje, ensure_ascii=False, separators=(",", ":"))
