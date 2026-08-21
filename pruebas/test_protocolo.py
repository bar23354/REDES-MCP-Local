"""Pruebas de la capa JSON-RPC 2.0 y del ciclo de vida MCP."""

import json
import logging
import unittest

from mcp_logistica import NOMBRE_SERVIDOR
from mcp_logistica.protocolo import jsonrpc
from mcp_logistica.protocolo.despachador import (
    VERSION_PREFERIDA,
    VERSIONES_SOPORTADAS,
    Despachador,
)


def setUpModule():
    # Varias pruebas provocan errores a propósito. El servidor los registra en
    # stderr, que es lo correcto, pero aquí solo ensucia la salida de las pruebas.
    logging.disable(logging.CRITICAL)


def tearDownModule():
    logging.disable(logging.NOTSET)


def solicitud(metodo: str, params: dict | None = None, id_: object = 1) -> dict:
    mensaje = {"jsonrpc": "2.0", "id": id_, "method": metodo}
    if params is not None:
        mensaje["params"] = params
    return mensaje


def notificacion(metodo: str, params: dict | None = None) -> dict:
    mensaje = {"jsonrpc": "2.0", "method": metodo}
    if params is not None:
        mensaje["params"] = params
    return mensaje


class PruebaMensajesJSONRPC(unittest.TestCase):
    def test_json_ilegible_es_parse_error(self):
        respuesta = Despachador().manejar_linea("{esto no es json")

        self.assertEqual(respuesta["error"]["code"], jsonrpc.PARSE_ERROR)
        # Sin JSON válido no hay forma de conocer el id: debe ir en null.
        self.assertIsNone(respuesta["id"])

    def test_un_arreglo_no_es_una_solicitud_valida(self):
        respuesta = Despachador().manejar_linea("[1,2,3]")
        self.assertEqual(respuesta["error"]["code"], jsonrpc.INVALID_REQUEST)

    def test_version_de_jsonrpc_incorrecta(self):
        respuesta = Despachador().manejar({"jsonrpc": "1.0", "id": 1, "method": "ping"})
        self.assertEqual(respuesta["error"]["code"], jsonrpc.INVALID_REQUEST)

    def test_metodo_faltante(self):
        respuesta = Despachador().manejar({"jsonrpc": "2.0", "id": 1})
        self.assertEqual(respuesta["error"]["code"], jsonrpc.INVALID_REQUEST)

    def test_metodo_desconocido(self):
        respuesta = Despachador().manejar(solicitud("resources/list"))

        self.assertEqual(respuesta["error"]["code"], jsonrpc.METHOD_NOT_FOUND)
        self.assertIn("resources/list", respuesta["error"]["message"])

    def test_params_posicionales_no_se_aceptan(self):
        respuesta = Despachador().manejar(
            {"jsonrpc": "2.0", "id": 1, "method": "ping", "params": [1, 2]}
        )
        self.assertEqual(respuesta["error"]["code"], jsonrpc.INVALID_PARAMS)

    def test_la_respuesta_conserva_el_id_incluso_si_es_cadena(self):
        respuesta = Despachador().manejar(solicitud("ping", id_="abc-123"))
        self.assertEqual(respuesta["id"], "abc-123")

    def test_serializar_no_deja_saltos_de_linea_embebidos(self):
        # El framing del transporte stdio se rompería si un salto de línea dentro
        # de una cadena saliera sin escapar.
        linea = jsonrpc.serializar({"jsonrpc": "2.0", "id": 1, "result": {"t": "a\nb"}})

        self.assertNotIn("\n", linea)
        self.assertEqual(json.loads(linea)["result"]["t"], "a\nb")

    def test_serializar_conserva_los_acentos(self):
        linea = jsonrpc.serializar({"result": {"nombre": "Bárcenas"}})
        self.assertIn("Bárcenas", linea)


class PruebaNotificaciones(unittest.TestCase):
    def test_una_notificacion_no_lleva_respuesta(self):
        self.assertIsNone(Despachador().manejar(notificacion("notifications/initialized")))

    def test_una_notificacion_con_metodo_desconocido_tampoco_responde(self):
        self.assertIsNone(Despachador().manejar(notificacion("notifications/inventada")))

    def test_una_notificacion_mal_formada_tampoco_responde(self):
        self.assertIsNone(Despachador().manejar({"jsonrpc": "2.0"}))

    def test_initialized_marca_el_handshake_como_completo(self):
        despachador = Despachador()
        self.assertFalse(despachador.inicializado)

        despachador.manejar(notificacion("notifications/initialized"))
        self.assertTrue(despachador.inicializado)


class PruebaHandshake(unittest.TestCase):
    def test_initialize_devuelve_las_capacidades(self):
        respuesta = Despachador().manejar(
            solicitud(
                "initialize",
                {
                    "protocolVersion": VERSION_PREFERIDA,
                    "capabilities": {},
                    "clientInfo": {"name": "prueba", "version": "1.0"},
                },
            )
        )
        resultado = respuesta["result"]

        self.assertEqual(resultado["protocolVersion"], VERSION_PREFERIDA)
        self.assertIn("tools", resultado["capabilities"])
        self.assertEqual(resultado["serverInfo"]["name"], NOMBRE_SERVIDOR)
        self.assertIn("instructions", resultado)

    def test_se_hace_eco_de_cualquier_version_soportada(self):
        for version in VERSIONES_SOPORTADAS:
            respuesta = Despachador().manejar(
                solicitud("initialize", {"protocolVersion": version})
            )
            self.assertEqual(respuesta["result"]["protocolVersion"], version)

    def test_una_version_desconocida_cae_en_la_preferida(self):
        respuesta = Despachador().manejar(
            solicitud("initialize", {"protocolVersion": "1999-01-01"})
        )
        self.assertEqual(respuesta["result"]["protocolVersion"], VERSION_PREFERIDA)

    def test_ping_responde_vacio(self):
        respuesta = Despachador().manejar(solicitud("ping"))
        self.assertEqual(respuesta["result"], {})


class PruebaListadoDeHerramientas(unittest.TestCase):
    def setUp(self):
        self.resultado = Despachador().manejar(solicitud("tools/list"))["result"]

    def test_estan_las_tres_herramientas(self):
        nombres = {h["name"] for h in self.resultado["tools"]}
        self.assertEqual(
            nombres, {"validar_recepcion", "consolidar_carga", "planificar_ruta"}
        )

    def test_cada_herramienta_trae_su_esquema(self):
        for herramienta in self.resultado["tools"]:
            self.assertIn("description", herramienta)
            esquema = herramienta["inputSchema"]
            self.assertEqual(esquema["type"], "object")
            self.assertIn("required", esquema)

    def test_el_listado_es_serializable(self):
        # Si algo del registro no fuera serializable, el servidor moriría al
        # responder, no al construir el listado.
        json.dumps(self.resultado)


class PruebaLlamadaDeHerramientas(unittest.TestCase):
    def llamar(self, nombre, argumentos):
        return Despachador().manejar(
            solicitud("tools/call", {"name": nombre, "arguments": argumentos})
        )

    def test_llamada_exitosa_devuelve_texto_y_datos(self):
        respuesta = self.llamar(
            "consolidar_carga",
            {
                "pedidos": [{"id_pedido": "PED-0001", "peso_kg": 100, "volumen_m3": 1}],
                "capacidad_peso": 1000,
                "capacidad_volumen": 10,
            },
        )
        resultado = respuesta["result"]

        self.assertFalse(resultado["isError"])
        self.assertEqual(resultado["content"][0]["type"], "text")
        self.assertEqual(resultado["structuredContent"]["total_camiones"], 1)
        # El texto y los datos estructurados dicen lo mismo.
        self.assertEqual(
            json.loads(resultado["content"][0]["text"]), resultado["structuredContent"]
        )

    def test_herramienta_desconocida_es_error_de_protocolo(self):
        respuesta = self.llamar("optimizar_todo", {})

        self.assertIn("error", respuesta)
        self.assertEqual(respuesta["error"]["code"], jsonrpc.INVALID_PARAMS)
        self.assertIn("disponibles", respuesta["error"]["data"])

    def test_argumentos_invalidos_son_error_de_protocolo(self):
        respuesta = self.llamar("validar_recepcion", {"contenido": "algo"})

        self.assertIn("error", respuesta)
        self.assertEqual(respuesta["error"]["code"], jsonrpc.INVALID_PARAMS)
        self.assertIn("crc_esperado", respuesta["error"]["message"])

    def test_falta_el_nombre_de_la_herramienta(self):
        respuesta = Despachador().manejar(solicitud("tools/call", {"arguments": {}}))
        self.assertEqual(respuesta["error"]["code"], jsonrpc.INVALID_PARAMS)

    def test_arguments_debe_ser_objeto(self):
        respuesta = Despachador().manejar(
            solicitud("tools/call", {"name": "ping", "arguments": [1, 2]})
        )
        self.assertEqual(respuesta["error"]["code"], jsonrpc.INVALID_PARAMS)

    def test_un_lote_corrupto_no_es_un_error_de_protocolo(self):
        # Un problema de negocio se devuelve como resultado exitoso: el modelo
        # necesita leer el diagnóstico para poder reaccionar.
        respuesta = self.llamar(
            "validar_recepcion", {"contenido": "cualquier cosa", "crc_esperado": "deadbeef"}
        )
        resultado = respuesta["result"]

        self.assertNotIn("error", respuesta)
        self.assertFalse(resultado["isError"])
        self.assertEqual(resultado["structuredContent"]["estado_lote"], "CRC_NO_COINCIDE")

    def test_el_encadenamiento_entre_herramientas_funciona(self):
        despachador = Despachador()
        contenido = (
            "id_pedido,sucursal,destino,peso_kg,volumen_m3\n"
            "PED-0001,SUC,Tienda A,300,3\n"
            "PED-0002,SUC,Tienda B,400,4\n"
        )
        from mcp_logistica.nucleo.recepcion import calcular_crc32

        validacion = despachador.manejar(
            solicitud(
                "tools/call",
                {
                    "name": "validar_recepcion",
                    "arguments": {
                        "contenido": contenido,
                        "crc_esperado": calcular_crc32(contenido),
                    },
                },
            )
        )["result"]["structuredContent"]

        # La salida de una herramienta entra sin transformación en la siguiente.
        consolidacion = despachador.manejar(
            solicitud(
                "tools/call",
                {
                    "name": "consolidar_carga",
                    "arguments": {
                        "pedidos": validacion["pedidos_validos"],
                        "capacidad_peso": 1000,
                        "capacidad_volumen": 10,
                    },
                },
                id_=2,
            )
        )["result"]["structuredContent"]

        self.assertEqual(consolidacion["total_pedidos_asignados"], 2)


class PruebaRobustez(unittest.TestCase):
    def test_un_fallo_inesperado_se_convierte_en_error_interno(self):
        from mcp_logistica.protocolo import herramientas

        original = herramientas.HERRAMIENTAS["consolidar_carga"].manejador

        def explotar(_args):
            raise RuntimeError("fallo simulado")

        herramientas.HERRAMIENTAS["consolidar_carga"].manejador = explotar
        try:
            respuesta = Despachador().manejar(
                solicitud("tools/call", {"name": "consolidar_carga", "arguments": {}})
            )
        finally:
            herramientas.HERRAMIENTAS["consolidar_carga"].manejador = original

        # El servidor sigue vivo y contesta con isError en lugar de morirse.
        self.assertTrue(respuesta["result"]["isError"])
        self.assertIn("fallo simulado", respuesta["result"]["content"][0]["text"])


if __name__ == "__main__":
    unittest.main()
