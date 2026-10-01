"""Horario del mercado de opciones de EE. UU. (para no refrescar cotizaciones que no pueden cambiar)."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo


@dataclass(frozen=True)
class MarketCalendar:
    """Sesión regular lunes–viernes entre `open` y `close` (hora de `tz`), salvo `holidays`.
    No modela cierres anticipados (13:00): ese día se hace algún ciclo de más."""

    tz: ZoneInfo
    open: time = time(9, 30)
    close: time = time(16, 0)
    holidays: frozenset[date] = field(default_factory=frozenset)
    delay_minutes: float = 0   # datos diferidos: apertura y cierre «llegan» con este retraso

    @classmethod
    def from_settings(cls, s, delay_minutes: float = 0) -> "MarketCalendar":
        return cls(ZoneInfo(s.timezone), s.open, s.close, frozenset(s.holidays), delay_minutes)

    @property
    def _delay(self) -> timedelta:
        return timedelta(minutes=self.delay_minutes)

    def _local(self, now: datetime) -> datetime:
        """Hora en la zona del mercado, retrasada `delay_minutes`: con datos diferidos la sesión
        «visible» va esos minutos por detrás. Un `datetime` naive es hora local del sistema."""
        return now.astimezone(self.tz) - self._delay

    def _is_session_day(self, d: date) -> bool:
        return d.weekday() < 5 and d not in self.holidays

    def is_open(self, now: datetime) -> bool:
        local = self._local(now)
        return self._is_session_day(local.date()) and self.open <= local.time() < self.close

    def last_close(self, now: datetime) -> datetime:
        """Último cierre de sesión ya ocurrido (aware, en la zona del mercado)."""
        local = self._local(now)
        day = local.date()
        if not (self._is_session_day(day) and local.time() >= self.close):   # el cierre de hoy aún no ha llegado
            day -= timedelta(days=1)
            while not self._is_session_day(day):
                day -= timedelta(days=1)
        return datetime.combine(day, self.close, tzinfo=self.tz) + self._delay

    def next_open(self, now: datetime) -> datetime:
        """Próxima apertura (aware, en la zona del mercado); la de hoy si aún no ha abierto."""
        local = self._local(now)
        day = local.date()
        if not (self._is_session_day(day) and local.time() < self.open):
            day += timedelta(days=1)
            while not self._is_session_day(day):
                day += timedelta(days=1)
        return datetime.combine(day, self.open, tzinfo=self.tz) + self._delay
