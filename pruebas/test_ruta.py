"""Pruebas del núcleo de planificación de ruta."""

import unittest

from mcp_logistica.nucleo import ruta
from mcp_logistica.protocolo import herramientas
from mcp_logistica.protocolo.jsonrpc import INVALID_PARAMS, ErrorRPC

CEDIS = {"nombre": "CEDIS Villa Nueva", "lat": 14.5261, "lon": -90.5875}


def punto(nombre: str, lat: float, lon: float) -> dict:
    return {"nombre": nombre, "lat": lat, "lon": lon}


class PruebaHaversine(unittest.TestCase):
    def test_distancia_entre_el_mismo_punto_es_cero(self):
        self.assertAlmostEqual(ruta.distancia_haversine(CEDIS, CEDIS), 0.0, places=6)

    def test_un_grado_de_latitud_son_unos_111_km(self):
        d = ruta.distancia_haversine({"lat": 0, "lon": 0}, {"lat": 1, "lon": 0})
        self.assertAlmostEqual(d, 111.19, places=1)

    def test_distancia_conocida_guatemala_antigua(self):
        # Guatemala (14.6349, -90.5069) a Antigua (14.5586, -90.7295): ~25 km
        # en línea recta.
        d = ruta.distancia_haversine(
            {"lat": 14.6349, "lon": -90.5069}, {"lat": 14.5586, "lon": -90.7295}
        )
        self.assertTrue(24 < d < 27, f"se esperaban ~25 km, se obtuvieron {d}")

    def test_es_simetrica(self):
        a, b = {"lat": 10, "lon": 20}, {"lat": -30, "lon": 100}
        self.assertAlmostEqual(
            ruta.distancia_haversine(a, b), ruta.distancia_haversine(b, a), places=9
        )


class PruebaPlanificacion(unittest.TestCase):
    def test_la_ruta_empieza_y_termina_en_el_cedis(self):
        resultado = ruta.planificar(
            CEDIS, [punto("A", 14.6, -90.6), punto("B", 14.5, -90.7)]
        )
        secuencia = resultado["secuencia"]

        self.assertEqual(secuencia[0]["tipo"], "origen")
        self.assertEqual(secuencia[0]["parada"], CEDIS["nombre"])
        self.assertEqual(secuencia[-1]["tipo"], "retorno")
        self.assertEqual(secuencia[-1]["parada"], CEDIS["nombre"])

    def test_visita_cada_cliente_exactamente_una_vez(self):
        clientes = [punto(f"C{i}", 14.5 + i * 0.01, -90.6 - i * 0.01) for i in range(6)]
        resultado = ruta.planificar(CEDIS, clientes)

        visitados = [p["parada"] for p in resultado["secuencia"] if p["tipo"] == "cliente"]
        self.assertEqual(sorted(visitados), sorted(c["nombre"] for c in clientes))

    def test_la_distancia_acumulada_cierra_con_el_total(self):
        clientes = [punto(f"C{i}", 14.5 + i * 0.02, -90.6 - i * 0.02) for i in range(5)]
        resultado = ruta.planificar(CEDIS, clientes)

        self.assertAlmostEqual(
            resultado["secuencia"][-1]["distancia_acumulada_km"],
            resultado["distancia_total_km"],
            places=1,
        )

    def test_2opt_nunca_empeora_la_ruta(self):
        # Clientes en cruz, un caso donde vecino más cercano suele dejar cruces
        # que 2-opt puede desenredar.
        clientes = [
            punto("N", 14.70, -90.58),
            punto("S", 14.35, -90.58),
            punto("E", 14.52, -90.40),
            punto("O", 14.52, -90.78),
            punto("NE", 14.68, -90.42),
            punto("SO", 14.37, -90.76),
        ]
        resultado = ruta.planificar(CEDIS, clientes)
        self.assertGreaterEqual(resultado["mejora_2opt_km"], 0)

    def test_el_factor_de_ruteo_escala_la_distancia(self):
        clientes = [punto("A", 14.6, -90.6), punto("B", 14.5, -90.7)]
        recta = ruta.planificar(CEDIS, clientes, factor_ruteo=1.0)
        carretera = ruta.planificar(CEDIS, clientes, factor_ruteo=2.0)

        self.assertAlmostEqual(
            carretera["distancia_total_km"], recta["distancia_total_km"] * 2, places=1
        )

    def test_el_tiempo_suma_conduccion_y_servicio(self):
        clientes = [punto("A", 14.6, -90.6), punto("B", 14.5, -90.7)]
        resultado = ruta.planificar(CEDIS, clientes, minutos_por_parada=10)

        self.assertAlmostEqual(resultado["tiempo_servicio_min"], 20.0, places=1)
        self.assertAlmostEqual(
            resultado["tiempo_estimado_min"],
            resultado["tiempo_conduccion_min"] + resultado["tiempo_servicio_min"],
            places=1,
        )

    def test_minutos_de_servicio_por_cliente_pisan_el_valor_general(self):
        clientes = [
            {"nombre": "A", "lat": 14.6, "lon": -90.6, "minutos_servicio": 30},
            {"nombre": "B", "lat": 14.5, "lon": -90.7},
        ]
        resultado = ruta.planificar(CEDIS, clientes, minutos_por_parada=5)
        self.assertAlmostEqual(resultado["tiempo_servicio_min"], 35.0, places=1)

    def test_un_solo_cliente_produce_ida_y_vuelta(self):
        resultado = ruta.planificar(CEDIS, [punto("A", 14.6, -90.6)])

        self.assertEqual(len(resultado["secuencia"]), 3)
        self.assertEqual(resultado["total_paradas"], 1)

    def test_formato_hhmm(self):
        self.assertEqual(ruta._hhmm(0), "0h 00m")
        self.assertEqual(ruta._hhmm(59.6), "1h 00m")
        self.assertEqual(ruta._hhmm(125), "2h 05m")


class PruebaValidacionDeArgumentos(unittest.TestCase):
    def llamar(self, args: dict):
        return herramientas.HERRAMIENTAS["planificar_ruta"].manejador(args)

    def test_latitud_fuera_de_rango(self):
        with self.assertRaises(ErrorRPC) as ctx:
            self.llamar({"cedis": {"lat": 95, "lon": 0}, "clientes": [punto("A", 14, -90)]})
        self.assertEqual(ctx.exception.codigo, INVALID_PARAMS)
        self.assertIn("lat", ctx.exception.mensaje)

    def test_longitud_fuera_de_rango(self):
        with self.assertRaises(ErrorRPC):
            self.llamar({"cedis": CEDIS, "clientes": [{"nombre": "A", "lat": 14, "lon": 200}]})

    def test_coordenada_no_numerica(self):
        with self.assertRaises(ErrorRPC):
            self.llamar({"cedis": {"lat": "catorce", "lon": -90}, "clientes": [punto("A", 14, -90)]})

    def test_falta_el_cedis(self):
        with self.assertRaises(ErrorRPC) as ctx:
            self.llamar({"clientes": [punto("A", 14, -90)]})
        self.assertIn("cedis", ctx.exception.mensaje)

    def test_lista_de_clientes_vacia(self):
        with self.assertRaises(ErrorRPC):
            self.llamar({"cedis": CEDIS, "clientes": []})

    def test_velocidad_cero_es_invalida(self):
        with self.assertRaises(ErrorRPC):
            self.llamar(
                {"cedis": CEDIS, "clientes": [punto("A", 14, -90)], "velocidad_kmh": 0}
            )

    def test_los_parametros_opcionales_usan_su_valor_por_defecto(self):
        resultado = self.llamar({"cedis": CEDIS, "clientes": [punto("A", 14.6, -90.6)]})
        parametros = resultado["parametros"]

        self.assertEqual(parametros["velocidad_kmh"], ruta.VELOCIDAD_KMH_DEFECTO)
        self.assertEqual(parametros["factor_ruteo"], ruta.FACTOR_RUTEO_DEFECTO)


if __name__ == "__main__":
    unittest.main()
