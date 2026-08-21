"""Pruebas del núcleo de validación de recepción."""

import unittest

from mcp_logistica.nucleo import recepcion

ENCABEZADO = "id_pedido,sucursal,destino,peso_kg,volumen_m3"
FILA_OK = "PED-0001,SUC-ZONA10,Tienda El Ahorro,100.5,1.2"


def lote(*filas: str) -> str:
    return "\n".join([ENCABEZADO, *filas]) + "\n"


class PruebaCRC(unittest.TestCase):
    def test_crc_de_valor_conocido(self):
        # zlib.crc32(b"123456789") = 0xCBF43926, el vector de prueba estándar
        # del CRC-32 (mismo polinomio que el FCS de Ethernet).
        self.assertEqual(recepcion.calcular_crc32("123456789"), "cbf43926")

    def test_crc_ignora_el_tipo_de_salto_de_linea(self):
        crlf = recepcion.calcular_crc32("a,b\r\nc,d\r\n")
        lf = recepcion.calcular_crc32("a,b\nc,d\n")
        self.assertEqual(crlf, lf)

    def test_crc_ignora_el_bom(self):
        self.assertEqual(
            recepcion.calcular_crc32("﻿hola"), recepcion.calcular_crc32("hola")
        )

    def test_normalizar_crc_acepta_variantes(self):
        for entrada in ("1a2b3c4d", "0x1A2B3C4D", "1A2B 3C4D", "0X1a2b3c4d"):
            self.assertEqual(recepcion.normalizar_crc(entrada), "1a2b3c4d")

    def test_normalizar_crc_rellena_ceros(self):
        self.assertEqual(recepcion.normalizar_crc("ff"), "000000ff")


class PruebaEstadoDelLote(unittest.TestCase):
    def test_lote_integro(self):
        contenido = lote(FILA_OK)
        resultado = recepcion.validar_lote(contenido, recepcion.calcular_crc32(contenido))

        self.assertEqual(resultado["estado_lote"], recepcion.INTEGRO)
        self.assertTrue(resultado["crc_coincide"])
        self.assertEqual(resultado["registros_validos"], 1)
        self.assertEqual(resultado["registros_corruptos"], [])

    def test_crc_distinto_gana_sobre_los_registros(self):
        # Aunque los registros estén bien, si el CRC no coincide el archivo se
        # alteró en tránsito y ese es el diagnóstico que debe prevalecer.
        resultado = recepcion.validar_lote(lote(FILA_OK), "deadbeef")

        self.assertEqual(resultado["estado_lote"], recepcion.CRC_NO_COINCIDE)
        self.assertFalse(resultado["crc_coincide"])
        self.assertEqual(resultado["crc_esperado"], "deadbeef")

    def test_registros_corruptos_con_crc_valido(self):
        contenido = lote(FILA_OK, "PED-0002,SUC-ZONA10,Tienda,-5,1.0")
        resultado = recepcion.validar_lote(contenido, recepcion.calcular_crc32(contenido))

        self.assertEqual(resultado["estado_lote"], recepcion.REGISTROS_CORRUPTOS)
        self.assertTrue(resultado["crc_coincide"])
        self.assertEqual(resultado["registros_validos"], 1)


class PruebaDeteccionDeDefectos(unittest.TestCase):
    def motivos_de(self, fila: str, previas: tuple[str, ...] = ()) -> list[str]:
        contenido = lote(*previas, fila)
        resultado = recepcion.validar_lote(contenido, recepcion.calcular_crc32(contenido))
        corruptos = resultado["registros_corruptos"]
        self.assertTrue(corruptos, f"se esperaba que la fila fuera corrupta: {fila}")
        return corruptos[-1]["motivos"]

    def test_peso_negativo(self):
        self.assertIn("peso_invalido", self.motivos_de("PED-0002,SUC,Tienda,-10,1.0"))

    def test_peso_cero(self):
        self.assertIn("peso_invalido", self.motivos_de("PED-0002,SUC,Tienda,0,1.0"))

    def test_volumen_no_numerico(self):
        self.assertIn("volumen_invalido", self.motivos_de("PED-0002,SUC,Tienda,10,mucho"))

    def test_id_con_formato_invalido(self):
        self.assertIn("id_invalido", self.motivos_de("XYZ-1,SUC,Tienda,10,1.0"))

    def test_id_duplicado(self):
        motivos = self.motivos_de(FILA_OK, previas=(FILA_OK,))
        self.assertIn("id_duplicado", motivos)

    def test_destino_vacio(self):
        self.assertIn("destino_vacio", self.motivos_de("PED-0002,SUC,,10,1.0"))

    def test_columnas_de_menos(self):
        motivos = self.motivos_de("PED-0002,SUC,Tienda,10")
        self.assertEqual(motivos, ["columnas_invalidas"])

    def test_una_fila_puede_acumular_varios_motivos(self):
        motivos = self.motivos_de("PED-0002,SUC,,-10,1.0")
        self.assertIn("destino_vacio", motivos)
        self.assertIn("peso_invalido", motivos)

    def test_el_numero_de_linea_apunta_a_la_fila_real(self):
        contenido = lote(FILA_OK, "PED-0002,SUC,Tienda,-10,1.0")
        resultado = recepcion.validar_lote(contenido, recepcion.calcular_crc32(contenido))
        # Encabezado en la línea 1, primera fila en la 2, la corrupta en la 3.
        self.assertEqual(resultado["registros_corruptos"][0]["linea"], 3)


class PruebaEncadenamiento(unittest.TestCase):
    def test_pedidos_validos_tienen_la_forma_que_espera_consolidar_carga(self):
        contenido = lote(FILA_OK)
        resultado = recepcion.validar_lote(contenido, recepcion.calcular_crc32(contenido))
        pedido = resultado["pedidos_validos"][0]

        self.assertEqual(pedido["id_pedido"], "PED-0001")
        self.assertEqual(pedido["destino"], "Tienda El Ahorro")
        self.assertIsInstance(pedido["peso_kg"], float)
        self.assertIsInstance(pedido["volumen_m3"], float)

    def test_encabezado_faltante_marca_el_lote(self):
        contenido = FILA_OK + "\n"
        resultado = recepcion.validar_lote(contenido, recepcion.calcular_crc32(contenido))

        self.assertFalse(resultado["encabezado_valido"])
        self.assertEqual(resultado["estado_lote"], recepcion.REGISTROS_CORRUPTOS)


if __name__ == "__main__":
    unittest.main()
