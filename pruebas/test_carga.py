"""Pruebas del núcleo de consolidación de carga."""

import unittest

from mcp_logistica.nucleo import carga
from mcp_logistica.protocolo import herramientas
from mcp_logistica.protocolo.jsonrpc import INVALID_PARAMS, ErrorRPC


def pedido(id_pedido: str, peso: float, volumen: float, destino: str = "Cliente") -> dict:
    return {
        "id_pedido": id_pedido,
        "destino": destino,
        "peso_kg": peso,
        "volumen_m3": volumen,
    }


class PruebaConsolidacion(unittest.TestCase):
    def test_todo_cabe_en_un_camion(self):
        resultado = carga.consolidar(
            [pedido("PED-0001", 100, 1), pedido("PED-0002", 200, 2)], 1000, 10
        )

        self.assertEqual(resultado["total_camiones"], 1)
        self.assertEqual(resultado["camiones"][0]["peso_total"], 300)
        self.assertEqual(resultado["camiones"][0]["volumen_total"], 3)
        self.assertEqual(resultado["pedidos_sin_asignar"], [])

    def test_se_abre_un_camion_cuando_se_llena_el_peso(self):
        resultado = carga.consolidar(
            [pedido("PED-0001", 600, 1), pedido("PED-0002", 600, 1)], 1000, 10
        )
        self.assertEqual(resultado["total_camiones"], 2)

    def test_se_abre_un_camion_cuando_se_llena_el_volumen(self):
        # El peso sobra, pero el volumen no: la restricción que aprieta manda.
        resultado = carga.consolidar(
            [pedido("PED-0001", 10, 6), pedido("PED-0002", 10, 6)], 1000, 10
        )
        self.assertEqual(resultado["total_camiones"], 2)

    def test_pedido_que_excede_el_peso_no_se_asigna(self):
        resultado = carga.consolidar([pedido("PED-0001", 5000, 1)], 1000, 10)

        self.assertEqual(resultado["total_camiones"], 0)
        self.assertEqual(len(resultado["pedidos_sin_asignar"]), 1)
        self.assertIn("excede_peso", resultado["pedidos_sin_asignar"][0]["motivos"])

    def test_pedido_que_excede_ambas_dimensiones_reporta_los_dos_motivos(self):
        resultado = carga.consolidar([pedido("PED-0001", 5000, 50)], 1000, 10)
        motivos = resultado["pedidos_sin_asignar"][0]["motivos"]

        self.assertIn("excede_peso", motivos)
        self.assertIn("excede_volumen", motivos)

    def test_un_pedido_inviable_no_impide_asignar_el_resto(self):
        resultado = carga.consolidar(
            [pedido("PED-0001", 5000, 1), pedido("PED-0002", 100, 1)], 1000, 10
        )

        self.assertEqual(resultado["total_camiones"], 1)
        self.assertEqual(resultado["camiones"][0]["pedidos"], ["PED-0002"])
        self.assertEqual(len(resultado["pedidos_sin_asignar"]), 1)

    def test_ffd_usa_menos_camiones_que_el_orden_natural(self):
        # Caso clásico donde el orden importa: en orden natural los pequeños
        # ocupan los camiones primero y los grandes obligan a abrir más.
        pedidos = [
            pedido("PED-0001", 400, 1),
            pedido("PED-0002", 600, 1),
            pedido("PED-0003", 400, 1),
            pedido("PED-0004", 600, 1),
        ]
        resultado = carga.consolidar(pedidos, 1000, 100)
        self.assertEqual(resultado["total_camiones"], 2)

    def test_ocupacion_efectiva_es_la_dimension_mas_apretada(self):
        # 50% de peso pero 90% de volumen: el camión está lleno al 90%.
        resultado = carga.consolidar([pedido("PED-0001", 500, 9)], 1000, 10)
        camion = resultado["camiones"][0]

        self.assertEqual(camion["ocupacion_peso_pct"], 50.0)
        self.assertEqual(camion["ocupacion_volumen_pct"], 90.0)
        self.assertEqual(camion["ocupacion_pct"], 90.0)

    def test_no_se_excede_ninguna_capacidad(self):
        pedidos = [pedido(f"PED-{i:04d}", 137.5, 1.9) for i in range(1, 20)]
        resultado = carga.consolidar(pedidos, 1000, 10)

        for camion in resultado["camiones"]:
            self.assertLessEqual(camion["peso_total"], 1000)
            self.assertLessEqual(camion["volumen_total"], 10)

    def test_no_se_pierde_ni_se_duplica_ningun_pedido(self):
        pedidos = [pedido(f"PED-{i:04d}", 137.5, 1.9) for i in range(1, 20)]
        resultado = carga.consolidar(pedidos, 1000, 10)

        asignados = [p for c in resultado["camiones"] for p in c["pedidos"]]
        sin_asignar = [p["id_pedido"] for p in resultado["pedidos_sin_asignar"]]

        self.assertEqual(len(asignados), len(set(asignados)))
        self.assertEqual(
            sorted(asignados + sin_asignar), sorted(p["id_pedido"] for p in pedidos)
        )

    def test_los_destinos_del_camion_se_acumulan_sin_repetir(self):
        resultado = carga.consolidar(
            [
                pedido("PED-0001", 10, 1, "Tienda A"),
                pedido("PED-0002", 10, 1, "Tienda A"),
                pedido("PED-0003", 10, 1, "Tienda B"),
            ],
            1000,
            10,
        )
        self.assertEqual(resultado["camiones"][0]["destinos"], ["Tienda A", "Tienda B"])

    def test_lista_vacia_no_abre_camiones(self):
        resultado = carga.consolidar([], 1000, 10)

        self.assertEqual(resultado["total_camiones"], 0)
        self.assertEqual(resultado["ocupacion_promedio_pct"], 0.0)


class PruebaValidacionDeArgumentos(unittest.TestCase):
    """La validación es responsabilidad de la capa de protocolo, no del núcleo."""

    def llamar(self, args: dict):
        return herramientas.HERRAMIENTAS["consolidar_carga"].manejador(args)

    def test_capacidad_cero_es_invalida(self):
        with self.assertRaises(ErrorRPC) as ctx:
            self.llamar(
                {
                    "pedidos": [pedido("PED-0001", 10, 1)],
                    "capacidad_peso": 0,
                    "capacidad_volumen": 10,
                }
            )
        self.assertEqual(ctx.exception.codigo, INVALID_PARAMS)

    def test_capacidad_negativa_es_invalida(self):
        with self.assertRaises(ErrorRPC):
            self.llamar(
                {
                    "pedidos": [pedido("PED-0001", 10, 1)],
                    "capacidad_peso": -5,
                    "capacidad_volumen": 10,
                }
            )

    def test_lista_de_pedidos_vacia_es_invalida(self):
        with self.assertRaises(ErrorRPC):
            self.llamar({"pedidos": [], "capacidad_peso": 100, "capacidad_volumen": 10})

    def test_pedido_sin_peso_es_invalido(self):
        with self.assertRaises(ErrorRPC) as ctx:
            self.llamar(
                {
                    "pedidos": [{"id_pedido": "PED-0001", "volumen_m3": 1}],
                    "capacidad_peso": 100,
                    "capacidad_volumen": 10,
                }
            )
        self.assertIn("peso_kg", ctx.exception.mensaje)

    def test_booleano_no_cuenta_como_numero(self):
        # En Python bool es subclase de int; sin el filtro, True pasaría por 1.
        with self.assertRaises(ErrorRPC):
            self.llamar(
                {
                    "pedidos": [pedido("PED-0001", 10, 1)],
                    "capacidad_peso": True,
                    "capacidad_volumen": 10,
                }
            )


if __name__ == "__main__":
    unittest.main()
