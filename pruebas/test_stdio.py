"""Pruebas del transporte stdio: framing NDJSON y disciplina de stdout."""

import io
import json
import logging
import pathlib
import subprocess
import sys
import unittest

from mcp_logistica.protocolo.despachador import Despachador
from mcp_logistica.transporte.stdio import TransporteStdio

RAIZ = pathlib.Path(__file__).resolve().parent.parent
HANDSHAKE = RAIZ / "pruebas" / "handshake.ndjson"


def setUpModule():
    # El servidor registra en stderr a propósito; aquí solo estorba la lectura.
    logging.disable(logging.CRITICAL)


def tearDownModule():
    logging.disable(logging.NOTSET)


def correr(lineas: list[str]) -> list[dict]:
    """Alimenta el transporte con líneas y devuelve las respuestas parseadas."""
    entrada = io.StringIO("\n".join(lineas) + "\n")
    salida = io.StringIO()
    codigo = TransporteStdio(entrada=entrada, salida=salida).ejecutar(Despachador())

    assert codigo == 0, f"el transporte terminó con código {codigo}"
    return [json.loads(ln) for ln in salida.getvalue().splitlines() if ln]


class PruebaFraming(unittest.TestCase):
    def test_una_solicitud_produce_una_linea(self):
        salida = io.StringIO()
        TransporteStdio(
            entrada=io.StringIO('{"jsonrpc":"2.0","id":1,"method":"ping"}\n'), salida=salida
        ).ejecutar(Despachador())

        texto = salida.getvalue()
        self.assertTrue(texto.endswith("\n"))
        self.assertEqual(len(texto.splitlines()), 1)

    def test_las_notificaciones_no_generan_linea(self):
        respuestas = correr(
            [
                '{"jsonrpc":"2.0","id":1,"method":"ping"}',
                '{"jsonrpc":"2.0","method":"notifications/initialized"}',
                '{"jsonrpc":"2.0","id":2,"method":"ping"}',
            ]
        )

        self.assertEqual(len(respuestas), 2)
        self.assertEqual([r["id"] for r in respuestas], [1, 2])

    def test_las_lineas_en_blanco_se_ignoran(self):
        respuestas = correr(
            ["", '{"jsonrpc":"2.0","id":1,"method":"ping"}', "", "   "]
        )
        self.assertEqual(len(respuestas), 1)

    def test_un_bom_al_inicio_no_rompe_el_handshake(self):
        # PowerShell antepone un BOM UTF-8 al canalizar hacia un ejecutable
        # nativo. Sin limpiarlo, el initialize fallaría con -32700 en Windows.
        respuestas = correr(['﻿{"jsonrpc":"2.0","id":1,"method":"ping"}'])

        self.assertEqual(len(respuestas), 1)
        self.assertNotIn("error", respuestas[0])
        self.assertEqual(respuestas[0]["result"], {})

    def test_una_linea_ilegible_no_tumba_el_servidor(self):
        respuestas = correr(
            [
                "{roto",
                '{"jsonrpc":"2.0","id":2,"method":"ping"}',
            ]
        )

        self.assertEqual(len(respuestas), 2)
        self.assertEqual(respuestas[0]["error"]["code"], -32700)
        # El segundo mensaje se atiende con normalidad pese al error anterior.
        self.assertEqual(respuestas[1]["result"], {})

    def test_ninguna_respuesta_contiene_saltos_embebidos(self):
        contenido = "id_pedido,sucursal,destino,peso_kg,volumen_m3\nPED-0001,SUC,T,10,1\n"
        linea = json.dumps(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {
                    "name": "validar_recepcion",
                    "arguments": {"contenido": contenido, "crc_esperado": "0"},
                },
            }
        )
        salida = io.StringIO()
        TransporteStdio(entrada=io.StringIO(linea + "\n"), salida=salida).ejecutar(
            Despachador()
        )

        # El contenido lleva saltos de línea, pero la respuesta debe ser UNA línea.
        self.assertEqual(len(salida.getvalue().splitlines()), 1)

    def test_los_acentos_sobreviven_el_viaje(self):
        respuestas = correr(
            [
                json.dumps(
                    {
                        "jsonrpc": "2.0",
                        "id": 1,
                        "method": "tools/call",
                        "params": {
                            "name": "planificar_ruta",
                            "arguments": {
                                "cedis": {"nombre": "CEDIS Villa Nueva", "lat": 14.5, "lon": -90.5},
                                "clientes": [
                                    {"nombre": "Minimercado Bárcenas", "lat": 14.6, "lon": -90.6}
                                ],
                            },
                        },
                    }
                )
            ]
        )
        paradas = [p["parada"] for p in respuestas[0]["result"]["structuredContent"]["secuencia"]]
        self.assertIn("Minimercado Bárcenas", paradas)


class PruebaRegistroDeTrafico(unittest.TestCase):
    def test_registra_entrada_y_salida(self):
        traza = io.StringIO()
        TransporteStdio(
            entrada=io.StringIO('{"jsonrpc":"2.0","id":1,"method":"ping"}\n'),
            salida=io.StringIO(),
            registro_trafico=traza,
        ).ejecutar(Despachador())

        lineas = traza.getvalue().splitlines()
        self.assertEqual(len(lineas), 2)
        self.assertIn("<<", lineas[0])
        self.assertIn(">>", lineas[1])


class PruebaProcesoReal(unittest.TestCase):
    """Ejecuta servidor.py como subproceso, tal como lo lanza Claude Desktop."""

    def test_el_handshake_completo_contra_el_proceso(self):
        entrada = HANDSHAKE.read_text(encoding="utf-8")
        proceso = subprocess.run(
            [sys.executable, str(RAIZ / "servidor.py")],
            input=entrada,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=30,
        )

        self.assertEqual(proceso.returncode, 0, proceso.stderr)

        lineas = [ln for ln in proceso.stdout.splitlines() if ln.strip()]
        # 4 mensajes de entrada, pero uno es notificación: 3 respuestas.
        self.assertEqual(len(lineas), 3)

        respuestas = [json.loads(ln) for ln in lineas]
        for respuesta in respuestas:
            # Nada que no sea un mensaje MCP debe haber llegado a stdout.
            self.assertEqual(respuesta["jsonrpc"], "2.0")
            self.assertNotIn("error", respuesta)

        self.assertEqual([r["id"] for r in respuestas], [1, 2, 3])
        self.assertEqual(len(respuestas[1]["result"]["tools"]), 3)

    def test_la_bitacora_va_a_stderr_y_no_a_stdout(self):
        proceso = subprocess.run(
            [sys.executable, str(RAIZ / "servidor.py"), "--log-nivel", "DEBUG"],
            input='{"jsonrpc":"2.0","id":1,"method":"ping"}\n',
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=30,
        )

        self.assertIn("logistica-local", proceso.stderr)
        # stdout trae exactamente un mensaje JSON y nada más.
        self.assertEqual(json.loads(proceso.stdout.strip())["result"], {})


if __name__ == "__main__":
    unittest.main()
