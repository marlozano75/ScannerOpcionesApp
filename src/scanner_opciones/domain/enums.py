"""Enumeraciones del dominio."""
from enum import Enum


class AccountMode(str, Enum):
    LIVE = "live"
    PAPER = "paper"


class OptionRight(str, Enum):
    PUT = "P"
    CALL = "C"


class TrafficLight(str, Enum):
    GREEN = "green"      # Normal / Holgado
    AMBER = "amber"      # Preocupación
    ORANGE = "orange"    # Riesgo elevado / cerca del límite (severidad 2)
    RED = "red"          # Riesgo alto
    UNKNOWN = "unknown"  # dato no disponible (nunca verde)


class OperationType(str, Enum):
    REGULAR = "regular"
    TACTICAL = "tactical"
