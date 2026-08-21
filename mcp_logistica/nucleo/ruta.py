"""Planificación de la ruta de reparto desde el CEDIS.

Es un TSP (viajante de comercio) sobre las coordenadas de los clientes, con
retorno al CEDIS. Se resuelve en dos pasos:

1. **Vecino más cercano**: construye una ruta inicial rápida saltando siempre al
   cliente no visitado más próximo.
2. **2-opt**: toma la ruta anterior y desenreda los cruces invirtiendo tramos,
   mientras eso siga acortando el recorrido.

La distancia base es Haversine (línea recta sobre la esfera). Como los camiones
no vuelan, se multiplica por un factor de ruteo que la aproxima a la distancia
real por carretera.
"""

from __future__ import annotations

import math
from typing import Any

RADIO_TIERRA_KM = 6371.0088

VELOCIDAD_KMH_DEFECTO = 35.0
MINUTOS_POR_PARADA_DEFECTO = 12.0
FACTOR_RUTEO_DEFECTO = 1.3

# Tope de pasadas de 2-opt para que la herramienta responda rápido aunque le
# manden muchos clientes.
MAX_PASADAS_2OPT = 60


def distancia_haversine(a: dict[str, Any], b: dict[str, Any]) -> float:
    """Distancia en kilómetros entre dos puntos {lat, lon} sobre la esfera."""
    lat1, lon1 = math.radians(float(a["lat"])), math.radians(float(a["lon"]))
    lat2, lon2 = math.radians(float(b["lat"])), math.radians(float(b["lon"]))
    dlat = lat2 - lat1
    dlon = lon2 - lon1
    h = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    return 2 * RADIO_TIERRA_KM * math.asin(math.sqrt(h))


def _matriz_distancias(puntos: list[dict[str, Any]]) -> list[list[float]]:
    n = len(puntos)
    matriz = [[0.0] * n for _ in range(n)]
    for i in range(n):
        for j in range(i + 1, n):
            d = distancia_haversine(puntos[i], puntos[j])
            matriz[i][j] = d
            matriz[j][i] = d
    return matriz


def _largo_ciclo(orden: list[int], matriz: list[list[float]]) -> float:
    """Largo del ciclo cerrado: recorre el orden dado y regresa al inicio."""
    total = 0.0
    for i in range(len(orden) - 1):
        total += matriz[orden[i]][orden[i + 1]]
    return total + matriz[orden[-1]][orden[0]]


def _vecino_mas_cercano(matriz: list[list[float]]) -> list[int]:
    """Ruta inicial: desde el índice 0 (CEDIS), siempre al más próximo sin visitar."""
    n = len(matriz)
    pendientes = set(range(1, n))
    orden = [0]
    actual = 0
    while pendientes:
        siguiente = min(pendientes, key=lambda j: matriz[actual][j])
        orden.append(siguiente)
        pendientes.remove(siguiente)
        actual = siguiente
    return orden


def _mejorar_2opt(orden: list[int], matriz: list[list[float]]) -> list[int]:
    """Invierte tramos mientras eso acorte el ciclo. El CEDIS (índice 0) no se mueve."""
    n = len(orden)
    if n < 4:
        return orden

    mejor = orden[:]
    for _ in range(MAX_PASADAS_2OPT):
        hubo_mejora = False
        for i in range(1, n - 1):
            for j in range(i + 1, n):
                a, b = mejor[i - 1], mejor[i]
                c = mejor[j]
                d = mejor[(j + 1) % n]
                # Costo de las dos aristas actuales contra las dos que resultarían
                # de invertir el tramo [i..j]. Solo se comparan esas cuatro.
                actual = matriz[a][b] + matriz[c][d]
                propuesto = matriz[a][c] + matriz[b][d]
                if propuesto < actual - 1e-9:
                    mejor[i : j + 1] = reversed(mejor[i : j + 1])
                    hubo_mejora = True
        if not hubo_mejora:
            break
    return mejor


def planificar(
    cedis: dict[str, Any],
    clientes: list[dict[str, Any]],
    velocidad_kmh: float = VELOCIDAD_KMH_DEFECTO,
    minutos_por_parada: float = MINUTOS_POR_PARADA_DEFECTO,
    factor_ruteo: float = FACTOR_RUTEO_DEFECTO,
) -> dict[str, Any]:
    """Ordena las paradas y estima distancia y tiempo del reparto completo."""
    puntos = [cedis] + list(clientes)
    matriz = _matriz_distancias(puntos)

    orden_inicial = _vecino_mas_cercano(matriz)
    km_inicial = _largo_ciclo(orden_inicial, matriz) * factor_ruteo

    orden = _mejorar_2opt(orden_inicial, matriz)
    km_total = _largo_ciclo(orden, matriz) * factor_ruteo

    secuencia: list[dict[str, Any]] = []
    acumulado_km = 0.0
    acumulado_min = 0.0

    # El ciclo se cierra: se agrega el CEDIS otra vez al final como retorno.
    recorrido = orden + [0]
    for posicion, indice in enumerate(recorrido):
        punto = puntos[indice]
        if posicion == 0:
            tramo_km = 0.0
        else:
            tramo_km = matriz[recorrido[posicion - 1]][indice] * factor_ruteo
            acumulado_km += tramo_km
            acumulado_min += tramo_km / velocidad_kmh * 60

        if posicion == 0:
            tipo = "origen"
        elif posicion == len(recorrido) - 1:
            tipo = "retorno"
        else:
            tipo = "cliente"

        secuencia.append(
            {
                "orden": posicion,
                "tipo": tipo,
                "parada": punto.get("nombre", f"punto_{indice}"),
                "lat": float(punto["lat"]),
                "lon": float(punto["lon"]),
                "distancia_desde_anterior_km": round(tramo_km, 2),
                "distancia_acumulada_km": round(acumulado_km, 2),
                "eta_min": round(acumulado_min, 1),
            }
        )

        if tipo == "cliente":
            # El tiempo de servicio se suma después de llegar, no antes.
            acumulado_min += float(punto.get("minutos_servicio", minutos_por_parada))

    minutos_conduccion = km_total / velocidad_kmh * 60
    minutos_servicio = sum(
        float(c.get("minutos_servicio", minutos_por_parada)) for c in clientes
    )
    total_min = minutos_conduccion + minutos_servicio

    return {
        "secuencia": secuencia,
        "total_paradas": len(clientes),
        "distancia_total_km": round(km_total, 2),
        "tiempo_estimado_min": round(total_min, 1),
        "tiempo_estimado_hhmm": _hhmm(total_min),
        "tiempo_conduccion_min": round(minutos_conduccion, 1),
        "tiempo_servicio_min": round(minutos_servicio, 1),
        "mejora_2opt_km": round(km_inicial - km_total, 2),
        "parametros": {
            "velocidad_kmh": velocidad_kmh,
            "minutos_por_parada": minutos_por_parada,
            "factor_ruteo": factor_ruteo,
            "algoritmo": "vecino más cercano + 2-opt sobre distancia Haversine",
        },
        "resumen": (
            f"{len(clientes)} paradas, {round(km_total, 2)} km en total y "
            f"{_hhmm(total_min)} estimados saliendo de {cedis.get('nombre', 'el CEDIS')} "
            f"y regresando a él."
        ),
    }


def _hhmm(minutos: float) -> str:
    horas = int(minutos // 60)
    resto = int(round(minutos - horas * 60))
    if resto == 60:
        horas += 1
        resto = 0
    return f"{horas}h {resto:02d}m"
