"""Broker simulado en memoria para tests y desarrollo sin TWS."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Optional, Sequence

from scanner_opciones.domain.errors import BrokerDisconnectedError, DataUnavailableError
from scanner_opciones.domain.models import (
    AccountSummary, OptionChain, OptionContract, OptionQuote, Position, UnderlyingQuote, VixData,
)


@dataclass
class FakeGateway:
    account: AccountSummary = field(default_factory=lambda: AccountSummary("DU000000"))
    positions: list[Position] = field(default_factory=list)
    sectors: dict[str, tuple[Optional[str], Optional[str]]] = field(default_factory=dict)
    prices: dict[str, float] = field(default_factory=dict)
    underlying_ivs: dict[str, float] = field(default_factory=dict)  # IV en directo
    chains: dict[str, OptionChain] = field(default_factory=dict)
    ex_dividend_days: dict[str, Optional[int]] = field(default_factory=dict)
    quotes: dict[OptionContract, OptionQuote] = field(default_factory=dict)
    margins: dict[OptionContract, float] = field(default_factory=dict)
    vix: VixData = field(default_factory=VixData)
    failing_tickers: set[str] = field(default_factory=set)
    invalid_contracts: set[OptionContract] = field(default_factory=set)  # no existen en el 'broker'
    pacing_wait: float = 0.0   # espera simulada por el límite de peticiones históricas
    connected: bool = False
    calls: list[tuple] = field(default_factory=list)  # registro para asserts en tests

    async def connect(self) -> None:
        self.connected = True

    async def disconnect(self) -> None:
        self.connected = False

    def is_connected(self) -> bool:
        return self.connected

    def historical_request_counts(self) -> dict[str, int]:
        return {}

    def pacing_wait_seconds(self) -> float:
        return self.pacing_wait

    def _check(self, ticker: Optional[str] = None) -> None:
        if not self.connected:
            raise BrokerDisconnectedError("FakeGateway no conectado")
        if ticker and ticker in self.failing_tickers:
            raise DataUnavailableError(f"Sin datos para {ticker}")

    async def get_account_summary(self) -> AccountSummary:
        self._check()
        return self.account

    async def get_positions(self) -> list[Position]:
        self._check()
        return list(self.positions)

    async def get_sector_info(self, ticker: str) -> tuple[Optional[str], Optional[str]]:
        self._check(ticker)
        return self.sectors.get(ticker, (None, None))

    async def get_underlying_price(self, ticker: str) -> Optional[float]:
        self._check(ticker)
        return self.prices.get(ticker)

    async def get_underlying_quotes(self, tickers: Sequence[str]) -> dict[str, UnderlyingQuote]:
        self._check()
        out = {}
        for t in tickers:
            if t in self.failing_tickers:
                continue
            q = UnderlyingQuote(self.prices.get(t), self.underlying_ivs.get(t))
            if q.price is not None or q.iv is not None:
                out[t] = q
        return out

    async def get_option_chain(self, ticker: str) -> OptionChain:
        self._check(ticker)
        return self.chains.get(ticker, OptionChain(ticker))

    async def get_days_to_ex_dividend(self, ticker: str) -> Optional[int]:
        self._check(ticker)
        return self.ex_dividend_days.get(ticker)

    async def get_days_to_ex_dividend_many(self, tickers: Sequence[str]) -> dict[str, Optional[int]]:
        self._check()
        self.calls.append(("get_days_to_ex_dividend_many", tuple(tickers)))
        return {t: self.ex_dividend_days.get(t) for t in tickers if t not in self.failing_tickers}

    async def qualify_contracts(self, contracts: Sequence[OptionContract]) -> list[OptionContract]:
        self._check()
        return [c for c in contracts if c not in self.invalid_contracts]

    async def get_quotes(self, contracts: Sequence[OptionContract]) -> dict[OptionContract, OptionQuote]:
        self._check()
        for c in contracts:
            if c.ticker in self.failing_tickers:
                raise DataUnavailableError(f"Sin datos para {c.ticker}")
        return {c: self.quotes[c] for c in contracts if c in self.quotes}

    async def what_if_margin(self, contract: OptionContract, quantity: int = 1) -> Optional[float]:
        self._check(contract.ticker)
        m = self.margins.get(contract)
        return None if m is None else m * quantity

    async def get_vix_data(self, history_days: int, futures_ahead: int) -> VixData:
        self._check()
        return VixData(
            self.vix.current, self.vix.last_closes[-history_days:],
            self.vix.futures[:futures_ahead], self.vix.updated_at,
        )
