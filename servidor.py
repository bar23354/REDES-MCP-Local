"""Lanzador del servidor MCP de logística.

Existe para que la configuración de Claude Desktop sea una sola ruta absoluta,
sin necesidad de PYTHONPATH ni de un directorio de trabajo específico: este
archivo agrega su propia carpeta a sys.path y arranca el servidor.

    python servidor.py
"""

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

from mcp_logistica.__main__ import main  # noqa: E402  (debe ir tras ajustar sys.path)

if __name__ == "__main__":
    raise SystemExit(main())
