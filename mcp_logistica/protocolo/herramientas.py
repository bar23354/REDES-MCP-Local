"""Registro de las tres herramientas MCP y validación de sus argumentos.

Cada herramienta declara su ``inputSchema`` en JSON Schema escrito a mano (no se
usa la librería ``jsonschema``, ni ningún SDK). La validación real la hacen los
ayudantes ``_exigir_*``: el esquema le dice al chatbot qué mandar, y los
ayudantes garantizan que lo que llegó de verdad sirva.
"""

from __future__ import annotations

from typing import Any, Callable

from ..nucleo import carga, recepcion, ruta
from .jsonrpc import INVALID_PARAMS, ErrorRPC


# --------------------------------------------------------------------------
# Ayudantes de validación
# --------------------------------------------------------------------------

def _exigir_str(args: dict[str, Any], campo: str) -> str:
    valor = args.get(campo)
    if not isinstance(valor, str) or not valor.strip():
        raise ErrorRPC(INVALID_PARAMS, f'"{campo}" es obligatorio y debe ser una cadena no vacía')
    return valor


def _exigir_numero_positivo(args: dict[str, Any], campo: str) -> float:
    valor = args.get(campo)
    if isinstance(valor, bool) or not isinstance(valor, (int, float)):
        raise ErrorRPC(INVALID_PARAMS, f'"{campo}" es obligatorio y debe ser un número')
    if valor <= 0:
        raise ErrorRPC(INVALID_PARAMS, f'"{campo}" debe ser mayor que cero (llegó {valor})')
    return float(valor)


def _numero_opcional(args: dict[str, Any], campo: str, por_defecto: float) -> float:
    if campo not in args or args[campo] is None:
        return por_defecto
    return _exigir_numero_positivo(args, campo)


def _exigir_lista(args: dict[str, Any], campo: str) -> list[Any]:
    valor = args.get(campo)
    if not isinstance(valor, list):
        raise ErrorRPC(INVALID_PARAMS, f'"{campo}" es obligatorio y debe ser una lista')
    if not valor:
        raise ErrorRPC(INVALID_PARAMS, f'"{campo}" no puede estar vacía')
    return valor


def _exigir_punto(valor: Any, etiqueta: str) -> dict[str, Any]:
    if not isinstance(valor, dict):
        raise ErrorRPC(INVALID_PARAMS, f"{etiqueta} debe ser un objeto con nombre, lat y lon")

    punto: dict[str, Any] = {"nombre": str(valor.get("nombre", etiqueta))}
    for eje, minimo, maximo in (("lat", -90, 90), ("lon", -180, 180)):
        coordenada = valor.get(eje)
        if isinstance(coordenada, bool) or not isinstance(coordenada, (int, float)):
            raise ErrorRPC(INVALID_PARAMS, f'{etiqueta}: "{eje}" debe ser un número')
        if not minimo <= coordenada <= maximo:
            raise ErrorRPC(
                INVALID_PARAMS,
                f'{etiqueta}: "{eje}" fuera de rango, debe estar entre {minimo} y {maximo}',
            )
        punto[eje] = float(coordenada)

    if "minutos_servicio" in valor and valor["minutos_servicio"] is not None:
        minutos = valor["minutos_servicio"]
        if isinstance(minutos, bool) or not isinstance(minutos, (int, float)) or minutos < 0:
            raise ErrorRPC(
                INVALID_PARAMS, f'{etiqueta}: "minutos_servicio" debe ser un número no negativo'
            )
        punto["minutos_servicio"] = float(minutos)

    return punto


def _exigir_pedido(valor: Any, posicion: int) -> dict[str, Any]:
    etiqueta = f"pedidos[{posicion}]"
    if not isinstance(valor, dict):
        raise ErrorRPC(INVALID_PARAMS, f"{etiqueta} debe ser un objeto")

    pedido: dict[str, Any] = {
        "id_pedido": str(valor.get("id_pedido", f"PEDIDO-{posicion + 1}")),
        "destino": str(valor.get("destino", "")),
    }
    for campo in ("peso_kg", "volumen_m3"):
        numero = valor.get(campo)
        if isinstance(numero, bool) or not isinstance(numero, (int, float)):
            raise ErrorRPC(INVALID_PARAMS, f'{etiqueta}: "{campo}" debe ser un número')
        if numero <= 0:
            raise ErrorRPC(INVALID_PARAMS, f'{etiqueta}: "{campo}" debe ser mayor que cero')
        pedido[campo] = float(numero)

    return pedido


# --------------------------------------------------------------------------
# Manejadores: traducen argumentos validados a llamadas al núcleo
# --------------------------------------------------------------------------

def _validar_recepcion(args: dict[str, Any]) -> dict[str, Any]:
    contenido = _exigir_str(args, "contenido")
    crc_esperado = _exigir_str(args, "crc_esperado")
    return recepcion.validar_lote(contenido, crc_esperado)


def _consolidar_carga(args: dict[str, Any]) -> dict[str, Any]:
    crudos = _exigir_lista(args, "pedidos")
    pedidos = [_exigir_pedido(p, i) for i, p in enumerate(crudos)]
    capacidad_peso = _exigir_numero_positivo(args, "capacidad_peso")
    capacidad_volumen = _exigir_numero_positivo(args, "capacidad_volumen")
    return carga.consolidar(pedidos, capacidad_peso, capacidad_volumen)


def _planificar_ruta(args: dict[str, Any]) -> dict[str, Any]:
    if "cedis" not in args:
        raise ErrorRPC(INVALID_PARAMS, '"cedis" es obligatorio')
    cedis = _exigir_punto(args["cedis"], "cedis")
    clientes = [
        _exigir_punto(c, f"clientes[{i}]") for i, c in enumerate(_exigir_lista(args, "clientes"))
    ]
    return ruta.planificar(
        cedis,
        clientes,
        velocidad_kmh=_numero_opcional(args, "velocidad_kmh", ruta.VELOCIDAD_KMH_DEFECTO),
        minutos_por_parada=_numero_opcional(
            args, "minutos_por_parada", ruta.MINUTOS_POR_PARADA_DEFECTO
        ),
        factor_ruteo=_numero_opcional(args, "factor_ruteo", ruta.FACTOR_RUTEO_DEFECTO),
    )


# --------------------------------------------------------------------------
# Registro
# --------------------------------------------------------------------------

class Herramienta:
    """Una herramienta expuesta por ``tools/list`` y ejecutable por ``tools/call``."""

    def __init__(
        self,
        nombre: str,
        titulo: str,
        descripcion: str,
        esquema_entrada: dict[str, Any],
        manejador: Callable[[dict[str, Any]], dict[str, Any]],
    ) -> None:
        self.nombre = nombre
        self.titulo = titulo
        self.descripcion = descripcion
        self.esquema_entrada = esquema_entrada
        self.manejador = manejador

    def descriptor(self) -> dict[str, Any]:
        """Forma en la que la herramienta viaja dentro de ``tools/list``."""
        return {
            "name": self.nombre,
            "title": self.titulo,
            "description": self.descripcion,
            "inputSchema": self.esquema_entrada,
        }


HERRAMIENTAS: dict[str, Herramienta] = {}


def _registrar(herramienta: Herramienta) -> None:
    HERRAMIENTAS[herramienta.nombre] = herramienta


_registrar(
    Herramienta(
        nombre="validar_recepcion",
        titulo="Validar recepción de un lote de pedidos",
        descripcion=(
            "Verifica la integridad de un archivo de pedidos recibido de una sucursal. "
            "Calcula el CRC-32 del contenido y lo compara con el que envió la sucursal, y "
            "además revisa registro por registro que los campos sean válidos. Distingue "
            "entre un archivo alterado en tránsito (CRC_NO_COINCIDE) y un archivo íntegro "
            "con errores de captura (REGISTROS_CORRUPTOS). Devuelve 'pedidos_validos', que "
            "se puede pasar tal cual a consolidar_carga. Úsala como primer paso antes de "
            "consolidar cualquier lote."
        ),
        esquema_entrada={
            "type": "object",
            "properties": {
                "contenido": {
                    "type": "string",
                    "description": (
                        "Texto completo del archivo CSV recibido, con el encabezado "
                        "id_pedido,sucursal,destino,peso_kg,volumen_m3"
                    ),
                },
                "crc_esperado": {
                    "type": "string",
                    "description": (
                        "CRC-32 en hexadecimal que la sucursal declaró para ese archivo. Se "
                        "aceptan variantes como '1a2b3c4d' o '0x1A2B3C4D'."
                    ),
                },
            },
            "required": ["contenido", "crc_esperado"],
            "additionalProperties": False,
        },
        manejador=_validar_recepcion,
    )
)

_registrar(
    Herramienta(
        nombre="consolidar_carga",
        titulo="Consolidar pedidos en camiones",
        descripcion=(
            "Reparte una lista de pedidos entre camiones respetando a la vez el límite de "
            "peso y el de volumen, usando First-Fit Decreasing bidimensional. Devuelve la "
            "asignación por camión, el porcentaje de ocupación de cada uno y los pedidos que "
            "no caben en un camión estándar. Normalmente se usa después de validar_recepcion, "
            "con su lista 'pedidos_validos'."
        ),
        esquema_entrada={
            "type": "object",
            "properties": {
                "pedidos": {
                    "type": "array",
                    "description": "Pedidos a consolidar.",
                    "items": {
                        "type": "object",
                        "properties": {
                            "id_pedido": {"type": "string"},
                            "destino": {"type": "string"},
                            "peso_kg": {"type": "number", "exclusiveMinimum": 0},
                            "volumen_m3": {"type": "number", "exclusiveMinimum": 0},
                        },
                        "required": ["peso_kg", "volumen_m3"],
                    },
                },
                "capacidad_peso": {
                    "type": "number",
                    "exclusiveMinimum": 0,
                    "description": "Capacidad máxima de peso por camión, en kilogramos.",
                },
                "capacidad_volumen": {
                    "type": "number",
                    "exclusiveMinimum": 0,
                    "description": "Capacidad máxima de volumen por camión, en metros cúbicos.",
                },
            },
            "required": ["pedidos", "capacidad_peso", "capacidad_volumen"],
            "additionalProperties": False,
        },
        manejador=_consolidar_carga,
    )
)

_registrar(
    Herramienta(
        nombre="planificar_ruta",
        titulo="Planificar la ruta de reparto",
        descripcion=(
            "Ordena las paradas de un camión saliendo del CEDIS y regresando a él, usando "
            "vecino más cercano refinado con 2-opt sobre distancias Haversine. Devuelve la "
            "secuencia de paradas con su ETA, la distancia total en kilómetros y el tiempo "
            "estimado del reparto. Se usa como último paso, con los destinos de un camión "
            "de consolidar_carga."
        ),
        esquema_entrada={
            "type": "object",
            "properties": {
                "cedis": {
                    "type": "object",
                    "description": (
                        "Centro de distribución del que sale y al que regresa el camión."
                    ),
                    "properties": {
                        "nombre": {"type": "string"},
                        "lat": {"type": "number", "minimum": -90, "maximum": 90},
                        "lon": {"type": "number", "minimum": -180, "maximum": 180},
                    },
                    "required": ["lat", "lon"],
                },
                "clientes": {
                    "type": "array",
                    "description": "Destinos a visitar.",
                    "items": {
                        "type": "object",
                        "properties": {
                            "nombre": {"type": "string"},
                            "lat": {"type": "number", "minimum": -90, "maximum": 90},
                            "lon": {"type": "number", "minimum": -180, "maximum": 180},
                            "minutos_servicio": {
                                "type": "number",
                                "minimum": 0,
                                "description": "Minutos de descarga en ese cliente.",
                            },
                        },
                        "required": ["lat", "lon"],
                    },
                },
                "velocidad_kmh": {
                    "type": "number",
                    "exclusiveMinimum": 0,
                    "description": "Velocidad promedio del camión. Por defecto 35 km/h.",
                },
                "minutos_por_parada": {
                    "type": "number",
                    "exclusiveMinimum": 0,
                    "description": "Minutos de descarga por parada. Por defecto 12.",
                },
                "factor_ruteo": {
                    "type": "number",
                    "exclusiveMinimum": 0,
                    "description": (
                        "Multiplicador que convierte la distancia en línea recta a distancia "
                        "por carretera. Por defecto 1.3."
                    ),
                },
            },
            "required": ["cedis", "clientes"],
            "additionalProperties": False,
        },
        manejador=_planificar_ruta,
    )
)


def listar_descriptores() -> list[dict[str, Any]]:
    """Los descriptores de las tres herramientas, para responder ``tools/list``."""
    return [h.descriptor() for h in HERRAMIENTAS.values()]
