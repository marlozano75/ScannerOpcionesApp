"""Excepciones de la aplicación."""


class AppError(Exception):
    """Base de los errores propios de la aplicación."""


class ConfigError(AppError):
    """Configuración ausente o inválida."""


class BrokerError(AppError):
    """Fallo al comunicar con el broker."""


class BrokerDisconnectedError(BrokerError):
    """No hay conexión con TWS."""


class DataUnavailableError(BrokerError):
    """El broker no devolvió datos (sin suscripción, ticker inválido, ...)."""


class WatchlistError(AppError):
    """Watchlist inválida o ilegible."""
