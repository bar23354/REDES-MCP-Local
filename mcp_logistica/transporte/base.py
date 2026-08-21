"""Contrato que cumple todo transporte.

Un transporte solo hace tres cosas: recibir bytes de su medio, convertirlos en un
``dict`` que entrega al despachador, y devolver la respuesta por ese mismo medio.
Toda la lógica del protocolo vive en ``protocolo.despachador``, y toda la lógica
de negocio vive en ``nucleo``.

Mantener esta separación es lo que permite que el transporte HTTP de la fase
remota sea un archivo nuevo (``transporte/http.py``) que reutiliza el mismo
despachador, sin tocar ni una línea del núcleo ni de la capa de protocolo.
"""

from __future__ import annotations

from typing import Protocol

from ..protocolo.despachador import Despachador


class Transporte(Protocol):
    """Interfaz mínima de un transporte MCP."""

    def ejecutar(self, despachador: Despachador) -> int:
        """Atiende mensajes hasta que el medio se cierre.

        Devuelve el código de salida del proceso: 0 si terminó de forma limpia.
        """
        ...
