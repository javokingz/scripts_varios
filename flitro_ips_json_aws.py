#!/usr/bin/env python3
"""Filtra el archivo ip-ranges.json de AWS por una lista de regiones.

No requiere paquetes externos. Edita REGIONES y, si lo necesitas,
ARCHIVO_ENTRADA / ARCHIVO_SALIDA. El resultado es un TXT con un CIDR por linea.
"""

from __future__ import annotations

import argparse
import ipaddress
import json
import sys
from pathlib import Path
from typing import Any


# ---------------------------------------------------------------------------
# CONFIGURACION: agrega o elimina regiones de esta lista.
# ---------------------------------------------------------------------------
REGIONES = [
    "us-east-1",
    "us-west-2",
    "eu-west-1",
]

ARCHIVO_ENTRADA = Path("ip-ranges.json")
ARCHIVO_SALIDA = Path("ip-ranges-filtrado.txt")


class ErrorRangosAWS(Exception):
    """Error controlado al leer o validar el archivo de AWS."""


def normalizar_regiones(regiones: list[str]) -> list[str]:
    """Limpia, normaliza y elimina regiones repetidas conservando el orden."""
    resultado: list[str] = []
    vistas: set[str] = set()

    for region in regiones:
        region_limpia = region.strip().lower()
        if region_limpia and region_limpia not in vistas:
            vistas.add(region_limpia)
            resultado.append(region_limpia)

    if not resultado:
        raise ErrorRangosAWS("La lista REGIONES no contiene ninguna region valida.")

    return resultado


def cargar_json(ruta: Path) -> dict[str, Any]:
    """Carga el JSON y comprueba la estructura minima esperada."""
    try:
        with ruta.open("r", encoding="utf-8") as archivo:
            datos = json.load(archivo)
    except FileNotFoundError as exc:
        raise ErrorRangosAWS(f"No se encontro el archivo de entrada: {ruta}") from exc
    except json.JSONDecodeError as exc:
        raise ErrorRangosAWS(
            f"El archivo no contiene JSON valido (linea {exc.lineno}, "
            f"columna {exc.colno}): {exc.msg}"
        ) from exc
    except OSError as exc:
        raise ErrorRangosAWS(f"No fue posible leer {ruta}: {exc}") from exc

    if not isinstance(datos, dict):
        raise ErrorRangosAWS("La raiz del JSON debe ser un objeto.")

    for clave in ("prefixes", "ipv6_prefixes"):
        if clave not in datos:
            raise ErrorRangosAWS(f"Falta la propiedad obligatoria '{clave}'.")
        if not isinstance(datos[clave], list):
            raise ErrorRangosAWS(f"La propiedad '{clave}' debe ser una lista.")

    return datos


def filtrar_prefijos(
    registros: list[Any], regiones: set[str], campo_cidr: str, version: int
) -> list[str]:
    """Selecciona, normaliza y elimina CIDR repetidos."""
    filtrados: list[str] = []
    vistos: set[str] = set()

    for indice, registro in enumerate(registros):
        if not isinstance(registro, dict):
            raise ErrorRangosAWS(
                f"El elemento {indice} de la lista '{campo_cidr}' no es un objeto."
            )

        region = registro.get("region")
        if region not in regiones:
            continue

        cidr = registro.get(campo_cidr)
        if not isinstance(cidr, str):
            raise ErrorRangosAWS(
                f"El registro {indice} de la region '{region}' no tiene '{campo_cidr}'."
            )

        try:
            red = ipaddress.ip_network(cidr, strict=False)
        except ValueError as exc:
            raise ErrorRangosAWS(f"CIDR invalido '{cidr}': {exc}") from exc

        if red.version != version:
            raise ErrorRangosAWS(
                f"El CIDR '{cidr}' no corresponde a IPv{version}."
            )

        cidr_normalizado = str(red)
        if cidr_normalizado not in vistos:
            vistos.add(cidr_normalizado)
            filtrados.append(cidr_normalizado)

    return filtrados


def construir_resultado(datos: dict[str, Any], regiones: list[str]) -> dict[str, Any]:
    """Prepara los rangos unicos y la informacion usada por el resumen."""
    conjunto_regiones = set(regiones)
    ipv4 = filtrar_prefijos(datos["prefixes"], conjunto_regiones, "ip_prefix", 4)
    ipv6 = filtrar_prefijos(
        datos["ipv6_prefixes"], conjunto_regiones, "ipv6_prefix", 6
    )

    regiones_disponibles = {
        registro.get("region")
        for grupo in (datos["prefixes"], datos["ipv6_prefixes"])
        for registro in grupo
        if isinstance(registro, dict) and isinstance(registro.get("region"), str)
    }
    no_encontradas = [r for r in regiones if r not in regiones_disponibles]

    return {
        "regiones_no_encontradas": no_encontradas,
        "resumen": {
            "total_ipv4": len(ipv4),
            "total_ipv6": len(ipv6),
            "total": len(ipv4) + len(ipv6),
        },
        "rangos_ipv4": ipv4,
        "rangos_ipv6": ipv6,
    }


def guardar_txt(datos: dict[str, Any], ruta: Path) -> None:
    """Guarda un CIDR por linea, primero IPv4 y despues IPv6."""
    rangos = datos["rangos_ipv4"] + datos["rangos_ipv6"]
    try:
        ruta.parent.mkdir(parents=True, exist_ok=True)
        with ruta.open("w", encoding="utf-8") as archivo:
            if rangos:
                archivo.write("\n".join(rangos) + "\n")
    except OSError as exc:
        raise ErrorRangosAWS(f"No fue posible escribir {ruta}: {exc}") from exc


def crear_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Filtra los rangos IPv4 e IPv6 de AWS por region."
    )
    parser.add_argument(
        "entrada",
        nargs="?",
        type=Path,
        default=ARCHIVO_ENTRADA,
        help=f"JSON original de AWS (predeterminado: {ARCHIVO_ENTRADA})",
    )
    parser.add_argument(
        "-o",
        "--salida",
        type=Path,
        default=ARCHIVO_SALIDA,
        help=f"Archivo TXT de salida (predeterminado: {ARCHIVO_SALIDA})",
    )
    return parser


def main() -> int:
    argumentos = crear_parser().parse_args()

    try:
        regiones = normalizar_regiones(REGIONES)
        datos = cargar_json(argumentos.entrada)
        resultado = construir_resultado(datos, regiones)
        guardar_txt(resultado, argumentos.salida)
    except ErrorRangosAWS as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    resumen = resultado["resumen"]
    print(f"Archivo generado: {argumentos.salida}")
    print(f"Regiones: {', '.join(regiones)}")
    print(
        f"Rangos encontrados: {resumen['total']} "
        f"(IPv4: {resumen['total_ipv4']}, IPv6: {resumen['total_ipv6']})"
    )
    if resultado["regiones_no_encontradas"]:
        print(
            "Advertencia: regiones no encontradas: "
            + ", ".join(resultado["regiones_no_encontradas"]),
            file=sys.stderr,
        )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
