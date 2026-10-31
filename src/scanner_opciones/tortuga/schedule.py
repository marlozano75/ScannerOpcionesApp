"""Periodicidad de las fuentes que se consultan por API o MCP (rutinas de Tortuga y listas públicas de tastytrade).

Cada una tiene el mismo modelo de horario, editable desde el Universo (se guarda en `meta.feed_schedules`; lo que no se toca sale de la configuración):
- **en mercado**: cada N minutos mientras el mercado está abierto;
- **fuera de mercado**: cada N minutos en día hábil con el mercado cerrado;
- **descargas especiales**: horas fijas (hora local) de los días hábiles, una vez cada una;
- **pasada tras el cierre**: una lectura más cuando falta la foto del último cierre (también al abrir la app fuera de sesión).
Un fallo no se reintenta hasta pasados `retry_minutes`. Fin de semana y festivo no hay lecturas por intervalo ni a hora fija."""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, time as dtime
from typing import Optional

from scanner_opciones.config.settings import Settings
from scanner_opciones.market.hours import MarketCalendar
from scanner_opciones.storage.repositories import MetaRepo

SCHEDULES_KEY = "feed_schedules"
LISTS_KEY = "tasty_lists"
MIN_MINUTES = {LISTS_KEY: 5}          # las listas de tastytrade no tienen sentido más seguidas; el resto, 1 minuto


class ScheduleError(ValueError):
    """Un valor del horario no es válido."""


@dataclass(frozen=True)
class FeedSchedule:
    in_market: Optional[int] = None
    out_market: Optional[int] = None
    times: tuple[str, ...] = ()
    after_close: bool = False

    def is_due(self, now: datetime, market: MarketCalendar, last_try: Optional[datetime], last_ok: Optional[datetime], retry_minutes: int,
               min_days: Optional[int] = None) -> bool:
        is_open = market.is_open(now)
        session = market.is_session_day_now(now)
        every = self.in_market if is_open else (self.out_market if session else None)
        if every and (last_try is None or (now - last_try).total_seconds() >= every * 60):
            return True
        if self.after_close and not is_open:
            if last_ok is None or last_ok.astimezone() < market.last_close(now):
                failed = last_try is not None and (last_ok is None or last_try > last_ok)       # el último intento falló: espera `retry_minutes`
                if not failed or (now - last_try).total_seconds() >= retry_minutes * 60:
                    return True
        return session and self._slot_due(now, last_try, last_ok, retry_minutes, min_days)

    def _slot_due(self, now: datetime, last_try: Optional[datetime], last_ok: Optional[datetime], retry_minutes: int, min_days: Optional[int]) -> bool:
        passed = [datetime.combine(now.date(), dtime(*(int(p) for p in t.split(":"))), tzinfo=now.tzinfo) for t in self.times]
        passed = [slot for slot in passed if slot <= now]
        if not passed:
            return False
        slot = max(passed)
        if last_ok is not None:
            if last_ok >= slot or (len(self.times) == 1 and last_ok.date() == now.date()):
                return False
            if min_days is not None and (now - last_ok).days < min_days:         # datos que cambian poco (13F): no se piden a diario
                return False
        return last_try is None or (now - last_try).total_seconds() >= retry_minutes * 60

    def to_json(self) -> dict:
        return {"in_market": self.in_market, "out_market": self.out_market, "times": list(self.times), "after_close": self.after_close}

    @classmethod
    def from_json(cls, data: object) -> "FeedSchedule":
        if not isinstance(data, dict):
            raise ScheduleError("horario no válido")
        return cls(_minutes(data.get("in_market")), _minutes(data.get("out_market")), parse_times(data.get("times") or ()), bool(data.get("after_close")))


def _minutes(value: object) -> Optional[int]:
    if value in (None, ""):
        return None
    try:
        minutes = int(value)
    except (TypeError, ValueError):
        raise ScheduleError(f"«{value}» no es un número de minutos") from None
    return minutes


def parse_times(values) -> tuple[str, ...]:
    """Horas HH:MM (lista o texto separado por comas/espacios), normalizadas y ordenadas, sin repetidas."""
    if isinstance(values, str):
        values = [v for v in values.replace(";", ",").replace(" ", ",").split(",") if v.strip()]
    out = []
    for raw in values:
        try:
            hh, mm = str(raw).strip().split(":")
            if not (0 <= int(hh) < 24 and 0 <= int(mm) < 60):
                raise ValueError
        except ValueError:
            raise ScheduleError(f"hora «{raw}» no válida (HH:MM)") from None
        out.append(f"{int(hh):02d}:{int(mm):02d}")
    return tuple(sorted(set(out)))


def default_schedule(settings: Settings, key: str) -> FeedSchedule:
    """El horario que tiene hoy la configuración: lista de tastytrade (cada N minutos en día hábil), rutina por intervalo (radar: en mercado y una
    pasada tras el cierre) o rutina diaria (a su hora)."""
    if key == LISTS_KEY:
        every = settings.tasty_lists.refresh_minutes
        return FeedSchedule(every, every)
    cfg = settings.tortuga
    if key == "radar_puts" and key not in cfg.intervals and key not in cfg.times:      # config.yaml anterior a la separación: igual que el radar de spreads
        key = "radar_spreads"
    if key in cfg.intervals:
        return FeedSchedule(in_market=cfg.intervals[key], after_close=True)
    return FeedSchedule(times=(cfg.times.get(key, "11:00"),))


def stored(meta: MetaRepo) -> dict[str, dict]:
    try:
        data = json.loads(meta.get(SCHEDULES_KEY) or "{}")
    except (ValueError, TypeError):
        return {}
    return data if isinstance(data, dict) else {}


def load_schedule(settings: Settings, meta: MetaRepo, key: str) -> FeedSchedule:
    saved = stored(meta).get(key)
    if saved is not None:
        try:
            return FeedSchedule.from_json(saved)
        except ScheduleError:
            pass
    return default_schedule(settings, key)


def save_schedule(meta: MetaRepo, key: str, schedule: FeedSchedule) -> None:
    minimum = MIN_MINUTES.get(key, 1)
    for label, value in (("en mercado", schedule.in_market), ("fuera de mercado", schedule.out_market)):
        if value is not None and value < minimum:
            raise ScheduleError(f"{key}: «{label}» admite {minimum} minutos como mínimo")
    data = stored(meta)
    data[key] = schedule.to_json()
    meta.set(SCHEDULES_KEY, json.dumps(data))


def reset_schedules(meta: MetaRepo) -> None:
    meta.set(SCHEDULES_KEY, "{}")


def describe(schedule: FeedSchedule) -> str:
    parts = []
    if schedule.in_market:
        parts.append(f"en mercado cada {schedule.in_market} min")
    if schedule.out_market:
        parts.append(f"fuera de mercado cada {schedule.out_market} min")
    if schedule.times:
        parts.append("a las " + ", ".join(schedule.times))
    if schedule.after_close:
        parts.append("una pasada tras el cierre")
    return " · ".join(parts) or "solo a mano"

