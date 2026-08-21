"""Consolidación de pedidos en camiones.

El problema es un bin packing de dos dimensiones simultáneas: un camión se llena
por peso o por volumen, lo que ocurra primero. Se resuelve con First-Fit
Decreasing, que es la heurística clásica: ordenar los pedidos de mayor a menor
"estorbo" y meter cada uno en el primer camión donde quepa.

FFD no garantiza el óptimo (el problema es NP-difícil), pero en la práctica se
queda cerca y es determinista, que es lo que se necesita para que el planificador
pueda auditar por qué un pedido quedó en un camión y no en otro.
"""

from __future__ import annotations

from typing import Any


def _ocupacion(valor: float, capacidad: float) -> float:
    return round(valor / capacidad * 100, 2) if capacidad else 0.0


def consolidar(
    pedidos: list[dict[str, Any]],
    capacidad_peso: float,
    capacidad_volumen: float,
) -> dict[str, Any]:
    """Reparte los pedidos en la menor cantidad razonable de camiones."""
    sin_asignar: list[dict[str, Any]] = []
    asignables: list[dict[str, Any]] = []

    for pedido in pedidos:
        peso = float(pedido["peso_kg"])
        volumen = float(pedido["volumen_m3"])
        motivos = []
        if peso > capacidad_peso:
            motivos.append("excede_peso")
        if volumen > capacidad_volumen:
            motivos.append("excede_volumen")

        if motivos:
            # No cabe ni en un camión vacío: requiere un vehículo especial.
            sin_asignar.append(
                {
                    "id_pedido": pedido.get("id_pedido", ""),
                    "destino": pedido.get("destino", ""),
                    "peso_kg": peso,
                    "volumen_m3": volumen,
                    "motivos": motivos,
                }
            )
        else:
            asignables.append(pedido)

    # La dimensión que más aprieta define el orden: un pedido que ocupa el 90%
    # del volumen estorba tanto como uno que ocupa el 90% del peso.
    ordenados = sorted(
        asignables,
        key=lambda p: max(
            float(p["peso_kg"]) / capacidad_peso,
            float(p["volumen_m3"]) / capacidad_volumen,
        ),
        reverse=True,
    )

    camiones: list[dict[str, Any]] = []
    for pedido in ordenados:
        peso = float(pedido["peso_kg"])
        volumen = float(pedido["volumen_m3"])

        for camion in camiones:
            cabe_peso = camion["peso_total"] + peso <= capacidad_peso + 1e-9
            cabe_volumen = camion["volumen_total"] + volumen <= capacidad_volumen + 1e-9
            if cabe_peso and cabe_volumen:
                destino = camion
                break
        else:
            destino = {
                "camion": f"CAM-{len(camiones) + 1:02d}",
                "pedidos": [],
                "destinos": [],
                "peso_total": 0.0,
                "volumen_total": 0.0,
            }
            camiones.append(destino)

        destino["pedidos"].append(pedido.get("id_pedido", ""))
        nombre_destino = pedido.get("destino", "")
        if nombre_destino and nombre_destino not in destino["destinos"]:
            destino["destinos"].append(nombre_destino)
        destino["peso_total"] += peso
        destino["volumen_total"] += volumen

    for camion in camiones:
        camion["peso_total"] = round(camion["peso_total"], 3)
        camion["volumen_total"] = round(camion["volumen_total"], 3)
        camion["total_pedidos"] = len(camion["pedidos"])
        camion["ocupacion_peso_pct"] = _ocupacion(camion["peso_total"], capacidad_peso)
        camion["ocupacion_volumen_pct"] = _ocupacion(camion["volumen_total"], capacidad_volumen)
        # La ocupación efectiva es la mayor de las dos: es la que impide seguir cargando.
        camion["ocupacion_pct"] = max(
            camion["ocupacion_peso_pct"], camion["ocupacion_volumen_pct"]
        )

    promedio = (
        round(sum(c["ocupacion_pct"] for c in camiones) / len(camiones), 2) if camiones else 0.0
    )

    return {
        "total_camiones": len(camiones),
        "total_pedidos_asignados": sum(c["total_pedidos"] for c in camiones),
        "ocupacion_promedio_pct": promedio,
        "capacidad_peso": capacidad_peso,
        "capacidad_volumen": capacidad_volumen,
        "camiones": camiones,
        "pedidos_sin_asignar": sin_asignar,
        "algoritmo": "First-Fit Decreasing bidimensional (peso y volumen)",
        "resumen": _resumir(camiones, sin_asignar, promedio),
    }


def _resumir(
    camiones: list[dict[str, Any]], sin_asignar: list[dict[str, Any]], promedio: float
) -> str:
    partes = [
        f"{sum(c['total_pedidos'] for c in camiones)} pedidos en {len(camiones)} camiones "
        f"con {promedio}% de ocupación promedio."
    ]
    if sin_asignar:
        ids = ", ".join(p["id_pedido"] for p in sin_asignar)
        partes.append(
            f"{len(sin_asignar)} pedido(s) no caben en un camión estándar y necesitan "
            f"vehículo especial: {ids}."
        )
    return " ".join(partes)
