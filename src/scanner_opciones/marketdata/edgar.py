"""SEC EDGAR (API pública `data.sec.gov`, gratuita y oficial): balance y flujos de caja de cada empresa.

Una sola petición por empresa (`companyfacts`, 1-5 MB) trae todos sus datos XBRL; las etiquetas alternativas se
resuelven aquí, en local. La SEC exige un `User-Agent` con un contacto (sin él bloquea las peticiones) y limita a
10 peticiones por segundo: se usa `edgar.contact` de la configuración y 5 por segundo como máximo.

Cobertura: las empresas estadounidenses (10-K/10-Q, US-GAAP) dan balance y flujo de 12 meses. Los emisores
extranjeros (20-F, IFRS) solo publican el año fiscal y, a menudo, sin etiquetas útiles: pueden quedar sin dato.
"""
from __future__ import annotations

import asyncio
import logging
import statistics
import time
import unicodedata
from datetime import date, datetime, timedelta
from typing import Any, Awaitable, Callable, Optional

from scanner_opciones.domain.errors import FinancialsError
from scanner_opciones.marketdata.financials import NO_LIMIT, Financials

log = logging.getLogger(__name__)

TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
FACTS_URL = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik:010d}.json"
STALE_DAYS = 460   # un dato más viejo que esto (≈15 meses) es de otra época: etiquetas abandonadas, empresa que dejó de reportar
ANNUAL_FORMS = ("10-K", "10-K/A", "20-F", "20-F/A", "40-F", "40-F/A")
QUARTER_FORMS = ("10-Q", "10-Q/A")

# etiquetas por taxonomía, en orden de preferencia
TAXONOMIES = {
    "us-gaap": dict(
        equity=("StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest", "StockholdersEquity"),
        liabilities=("Liabilities",), liabilities_and_equity=("LiabilitiesAndStockholdersEquity",),
        ocf=("NetCashProvidedByUsedInOperatingActivities", "NetCashProvidedByUsedInOperatingActivitiesContinuingOperations"),
        capex=("PaymentsToAcquirePropertyPlantAndEquipment", "PaymentsToAcquireProductiveAssets", "PaymentsForCapitalImprovements"),
        assets=("Assets",), cash=("CashAndCashEquivalentsAtCarryingValue",),
        # deuda: «LongTermDebt» ya incluye la parte corriente; «DebtCurrent» es la deuda que vence en 12 meses
        debt_total=("LongTermDebt", "LongTermDebtAndCapitalLeaseObligations", "DebtLongtermAndShorttermCombinedAmount"),
        debt_noncurrent=("LongTermDebtNoncurrent",), debt_current_lt=("LongTermDebtCurrent",), debt_current=("DebtCurrent",),
        short_debt=("ShortTermBorrowings", "CommercialPaper"),
        other_debt=("ConvertibleNotesPayable", "ConvertibleNotesPayableNoncurrent", "ConvertibleDebtNoncurrent", "ConvertibleDebt",
                    "SeniorNotes", "NotesPayable", "LongTermNotesPayable", "SecuredDebt", "UnsecuredDebt", "LineOfCredit"),
        op_income=("OperatingIncomeLoss",),
        # ROIC: tasa efectiva = impuestos / beneficio antes de impuestos; beneficios de cada año fiscal (años con pérdidas)
        tax=("IncomeTaxExpenseBenefit",),
        pretax=("IncomeLossFromContinuingOperationsBeforeIncomeTaxesExtraordinaryItemsNoncontrollingInterest",
                "IncomeLossFromContinuingOperationsBeforeIncomeTaxesMinorityInterestAndIncomeLossFromEquityMethodInvestments",
                "IncomeLossFromContinuingOperationsBeforeIncomeTaxesDomestic"),
        net_income=("NetIncomeLoss", "ProfitLoss", "NetIncomeLossAvailableToCommonStockholdersBasic"),
        revenue=("Revenues", "RevenueFromContractWithCustomerExcludingAssessedTax", "SalesRevenueNet",
                 "RevenueFromContractWithCustomerIncludingAssessedTax"),
        interest=("InterestExpense", "InterestExpenseNonoperating", "InterestExpenseDebt", "InterestAndDebtExpense"),
    ),
    "ifrs-full": dict(
        equity=("Equity", "EquityAttributableToOwnersOfParent"),
        liabilities=("Liabilities",), liabilities_and_equity=("EquityAndLiabilities",),
        ocf=("CashFlowsFromUsedInOperatingActivities", "CashFlowsFromUsedInOperatingActivitiesContinuingOperations"),
        capex=("PurchaseOfPropertyPlantAndEquipmentClassifiedAsInvestingActivities", "PurchaseOfPropertyPlantAndEquipment"),
        net_income=("ProfitLoss",), revenue=("Revenue",),
    ),
}


def _days(a: str, b: str) -> int:
    return (date.fromisoformat(b) - date.fromisoformat(a)).days


def _usd(tree: dict, tags: tuple[str, ...]) -> list[dict]:
    """Hechos en dólares de la primera etiqueta que tenga alguno (cada hecho: start?, end, val, form, filed)."""
    for tag in tags:
        facts = tree.get(tag, {}).get("units", {}).get("USD")
        if facts:
            return facts
    return []


def _latest_end(facts: list[dict]) -> str:
    return max((f["end"] for f in facts), default="")


def _freshest(tree: dict, tags: tuple[str, ...]) -> list[dict]:
    """Hechos en dólares de la etiqueta con el dato MÁS RECIENTE. Una empresa puede conservar una etiqueta antigua con
    datos de 2015 y usar otra ahora (MU, LITE): no vale la primera que tenga algo. En un empate manda la primera."""
    candidates = [tree.get(tag, {}).get("units", {}).get("USD") or [] for tag in tags]
    return max(candidates, key=_latest_end, default=[])


def _instants(facts: list[dict]) -> dict[str, float]:
    """Valores de balance por fecha de cierre; si hay varios para una fecha, el del informe más reciente."""
    best: dict[str, dict] = {}
    for f in facts:
        if "start" in f:
            continue
        if f["end"] not in best or f.get("filed", "") > best[f["end"]].get("filed", ""):
            best[f["end"]] = f
    return {end: f["val"] for end, f in best.items()}


def flow_ttm(facts: list[dict]) -> Optional[tuple[float, str]]:
    """Flujo de los últimos 12 meses y su fecha de fin: el último año fiscal (10-K/20-F) más el acumulado del año en
    curso (10-Q) menos el mismo acumulado del año anterior. Sin trimestres posteriores o sin comparable, el año fiscal."""
    by_period: dict[tuple[str, str], dict] = {}
    for f in facts:
        if "start" not in f:
            continue
        key = (f["start"], f["end"])
        if key not in by_period or f.get("filed", "") > by_period[key].get("filed", ""):
            by_period[key] = f
    periods = list(by_period.values())
    annual = [f for f in periods if 350 <= _days(f["start"], f["end"]) <= 380 and f.get("form", "") in ANNUAL_FORMS]
    if not annual:
        return None
    last = max(annual, key=lambda f: f["end"])
    later = [f for f in periods if f["end"] > last["end"] and f.get("form", "") in QUARTER_FORMS
             and 60 <= _days(f["start"], f["end"]) <= 290]
    if later:
        ytd = max(later, key=lambda f: (f["end"], _days(f["start"], f["end"])))
        prior = [f for f in periods if abs(_days(f["end"], ytd["end"]) - 365) <= 12
                 and abs(_days(f["start"], ytd["start"]) - 365) <= 12]
        if prior:
            return last["val"] + ytd["val"] - max(prior, key=lambda f: f.get("filed", ""))["val"], ytd["end"]
    return last["val"], last["end"]


def _freshest_flow(tree: dict, tags: tuple[str, ...]) -> Optional[tuple[float, str]]:
    """Flujo de 12 meses de la etiqueta cuyo cálculo termina más tarde."""
    results = [r for tag in tags if (r := flow_ttm(tree.get(tag, {}).get("units", {}).get("USD") or []))]
    return max(results, key=lambda r: r[1], default=None)


def parse_company_facts(data: dict, today: date) -> Optional[Financials]:
    """`Financials` de un documento `companyfacts`, o None si no hay datos recientes utilizables."""
    facts = data.get("facts", {})
    for taxonomy, tags in TAXONOMIES.items():
        tree = facts.get(taxonomy)
        if not tree:
            continue
        fin = _from_tree(tree, tags, today)
        if fin is not None:
            return fin
    return None


def _instant_value(tree: dict, tags: tuple[str, ...], fresh: str) -> Optional[float]:
    """Último valor de balance de la etiqueta más reciente, o None si falta o es viejo."""
    values = _instants(_freshest(tree, tags))
    if not values:
        return None
    end = max(values)
    return values[end] if end >= fresh else None


def _flow_value(tree: dict, tags: tuple[str, ...], fresh: str) -> Optional[float]:
    r = _freshest_flow(tree, tags)
    return r[0] if r is not None and r[1] >= fresh else None


def _debt(tree: dict, tags: dict, fresh: str, equity: Optional[float], liabilities: Optional[float]) -> tuple[Optional[float], Optional[float]]:
    """(deuda financiera total, deuda a corto plazo) en dólares; (None, None) si no se puede saber.
    Sin ninguna etiqueta de deuda se da por «sin deuda» solo si el balance lo hace creíble (pasivo/patrimonio < 1,5):
    muchas tecnológicas (ANET, CDNS…) no publican deuda porque no tienen."""
    total = _instant_value(tree, tags.get("debt_total", ()), fresh)
    if total is None:
        noncurrent = _instant_value(tree, tags.get("debt_noncurrent", ()), fresh)
        if noncurrent is not None:
            total = noncurrent + (_instant_value(tree, tags.get("debt_current_lt", ()), fresh) or 0.0)
    shorts = [v for tag in tags.get("short_debt", ()) if (v := _instant_value(tree, (tag,), fresh)) is not None]
    if total is None:
        others = [v for tag in tags.get("other_debt", ()) if (v := _instant_value(tree, (tag,), fresh)) is not None]
        total = max(others) if others else None
    if total is None and not shorts:
        credible = equity is not None and equity > 0 and liabilities is not None and liabilities / equity < 1.5
        return (0.0, 0.0) if credible else (None, None)
    debt = (total or 0.0) + sum(shorts)
    current = _instant_value(tree, tags.get("debt_current", ()), fresh)
    if current is None:
        current = (_instant_value(tree, tags.get("debt_current_lt", ()), fresh) or 0.0) + sum(shorts)
    return debt, current


TAX_FALLBACK = 0.21        # tasa federal de EE. UU. cuando la efectiva no se puede calcular o es absurda (negativa, > 40 %)
PROFIT_YEARS = 10          # años fiscales que se miran para contar años con pérdidas
MIN_PROFIT_YEARS = 5       # con menos historia no se juzga la estabilidad (sin dato)
MIN_GROWTH_OBSERVATIONS = 4   # crecimientos anuales mínimos para medir su volatilidad


def _roic(tree: dict, tags: dict, fresh: str, equity: Optional[float], debt: Optional[float]) -> Optional[float]:
    """ROIC = resultado operativo después de impuestos (12 meses) / capital invertido (deuda financiera + patrimonio).
    El capital incluye el efectivo, como en la definición del analista («dinero que se pone en el negocio: caja, deuda,
    maquinaria…»). Sin capital positivo o sin resultado operativo, None. Las pérdidas operativas no generan escudo fiscal."""
    op = _flow_value(tree, tags.get("op_income", ()), fresh)
    if op is None or debt is None or equity is None or debt + equity <= 0:
        return None
    tax, pretax = _flow_value(tree, tags.get("tax", ()), fresh), _flow_value(tree, tags.get("pretax", ()), fresh)
    rate = TAX_FALLBACK
    if tax is not None and pretax is not None and pretax > 0 and 0 <= tax / pretax <= 0.40:
        rate = tax / pretax
    return (op * (1.0 - rate) if op > 0 else op) / (debt + equity)


def _annual_series(tree: dict, tags: tuple[str, ...], fresh: str) -> list[tuple[str, float]]:
    """(fin del año fiscal, valor) de los últimos `PROFIT_YEARS` años fiscales (10-K/20-F), del más antiguo al más
    reciente; [] si falta o el último año es viejo. La etiqueta con el año más reciente manda y las demás solo rellenan
    los años que a ella le faltan (las empresas cambian de etiqueta, p. ej. SalesRevenueNet -> RevenueFromContract…)."""
    per_tag: list[dict[str, float]] = []
    for tag in tags:
        best: dict[str, dict] = {}
        for f in tree.get(tag, {}).get("units", {}).get("USD", []):
            if "start" in f and 350 <= _days(f["start"], f["end"]) <= 380 and f.get("form", "") in ANNUAL_FORMS:
                if f["end"] not in best or f.get("filed", "") > best[f["end"]].get("filed", ""):
                    best[f["end"]] = f
        if best:
            per_tag.append({end: f["val"] for end, f in best.items()})
    merged: dict[str, float] = {}
    for values in sorted(per_tag, key=lambda v: (max(v), len(v)), reverse=True):
        for end, val in values.items():
            merged.setdefault(end, val)
    ends = sorted(merged)[-PROFIT_YEARS:]
    return [] if not ends or ends[-1] < fresh else [(e, merged[e]) for e in ends]


def _consecutive(series: list[tuple[str, float]]):
    """Pares (anterior, actual) de años fiscales seguidos (de 350 a 380 días): un año que falta no se compara con otro."""
    for (e0, v0), (e1, v1) in zip(series, series[1:]):
        if 350 <= _days(e0, e1) <= 380:
            yield v0, v1


def _fiscal_profit(tree: dict, tags: dict, fresh: str) -> dict[str, Optional[float]]:
    """Estabilidad de los beneficios y de los ingresos en los últimos `PROFIT_YEARS` años fiscales. Todo es None con
    menos de `MIN_PROFIT_YEARS` años de historia:
      · loss_years / fiscal_years: años con beneficio neto < 0 y años evaluados;
      · revenue_drop_years / revenue_years: años en que los ingresos bajaron y años comparados con su anterior;
      · earnings_volatility: desviación típica del crecimiento anual del beneficio neto (como el análisis del vídeo,
        que mide cuánto oscilan los beneficios de un año a otro). Solo cuentan los años con beneficio positivo el año
        anterior, y cada crecimiento se limita a [−100 %, +200 %] para que una base minúscula no lo dispare."""
    out: dict[str, Optional[float]] = dict.fromkeys(
        ("loss_years", "fiscal_years", "revenue_drop_years", "revenue_years", "earnings_volatility"))
    income = _annual_series(tree, tags.get("net_income", ()), fresh)
    out["fiscal_years"] = len(income) or None
    if len(income) >= MIN_PROFIT_YEARS:
        out["loss_years"] = sum(1 for _, v in income if v < 0)
        growth = [max(-1.0, min(2.0, (v1 - v0) / v0)) for v0, v1 in _consecutive(income) if v0 > 0]
        if len(growth) >= MIN_GROWTH_OBSERVATIONS:
            out["earnings_volatility"] = statistics.pstdev(growth)
    revenue = _annual_series(tree, tags.get("revenue", ()), fresh)
    if len(revenue) >= MIN_PROFIT_YEARS:
        pairs = list(_consecutive(revenue))
        out["revenue_years"] = len(pairs) or None
        out["revenue_drop_years"] = sum(1 for v0, v1 in pairs if v1 < v0) if pairs else None
    return out


def _net_buyback_pct(tree: dict, fresh: str) -> Optional[float]:
    """Reducción (en %) del nº de acciones diluidas medias entre los dos últimos años fiscales (10-K)."""
    facts = tree.get("WeightedAverageNumberOfDilutedSharesOutstanding", {}).get("units", {}).get("shares", [])
    annual: dict[str, float] = {}
    for f in facts:
        if "start" in f and 350 <= _days(f["start"], f["end"]) <= 380 and f.get("form", "") in ANNUAL_FORMS:
            annual[f["end"]] = f["val"]
    ends = sorted(annual)
    if len(ends) < 2 or ends[-1] < fresh or not annual[ends[-2]] or not (350 <= _days(ends[-2], ends[-1]) <= 380):
        return None
    return -(annual[ends[-1]] / annual[ends[-2]] - 1.0) * 100.0


def _from_tree(tree: dict, tags: dict, today: date) -> Optional[Financials]:
    fresh = (today - timedelta(days=STALE_DAYS)).isoformat()
    ratio, balance_end, equity_val, liabilities_val = None, None, None, None
    equity = _instants(_freshest(tree, tags["equity"]))
    if equity:
        balance_end = max(equity)
        if balance_end < fresh:
            balance_end = None
        else:
            equity_val = equity[balance_end]
            if equity_val > 0:
                liabilities = _instants(_usd(tree, tags["liabilities"])).get(balance_end)
                if liabilities is None:   # algunas empresas (KO, UAL, ADM) no publican «Liabilities»: total − patrimonio
                    total = _instants(_usd(tree, tags["liabilities_and_equity"])).get(balance_end)
                    liabilities = None if total is None else total - equity_val
                if liabilities is not None and liabilities >= 0:
                    liabilities_val = liabilities
                    ratio = liabilities / equity_val
    fcf, flow_end, ocf_val, capex_val = None, None, None, None
    ocf, capex = _freshest_flow(tree, tags["ocf"]), _freshest_flow(tree, tags["capex"])
    if ocf is not None and ocf[1] >= fresh:
        ocf_val = ocf[0]
        if capex is not None:
            capex_val = abs(capex[0])      # el signo de las compras de inmovilizado varía entre taxonomías
            fcf, flow_end = ocf_val - capex_val, ocf[1]
    extras = _solvency(tree, tags, fresh, equity_val, liabilities_val, ocf_val, capex_val, fcf)
    if ratio is None and fcf is None and all(v is None for v in extras.values()):
        return None
    end = flow_end or balance_end
    return Financials(ratio, fcf, date.fromisoformat(end) if end else None, **extras)


def _solvency(tree: dict, tags: dict, fresh: str, equity: Optional[float], liabilities: Optional[float],
              ocf: Optional[float], capex: Optional[float], fcf: Optional[float]) -> dict[str, Optional[float]]:
    """Cociente de solvencia y de calidad del flujo de caja. NO_LIMIT = sin deuda / sin intereses / sin deuda corriente."""
    out: dict[str, Optional[float]] = dict.fromkeys(
        ("debt_to_equity", "interest_coverage", "cash_to_short_debt", "ocf_to_debt", "capex_to_ocf", "fcf_to_assets", "net_buyback_pct",
         "roic", "loss_years", "fiscal_years", "revenue_drop_years", "revenue_years", "earnings_volatility"))
    debt, current = _debt(tree, tags, fresh, equity, liabilities)
    if debt is not None and equity is not None and equity > 0:
        out["debt_to_equity"] = debt / equity
    if debt is not None and ocf is not None:
        out["ocf_to_debt"] = NO_LIMIT if debt <= 0 else min(ocf / debt, NO_LIMIT)
    op, interest = _flow_value(tree, tags.get("op_income", ()), fresh), _flow_value(tree, tags.get("interest", ()), fresh)
    if debt is not None and debt <= 0:
        out["interest_coverage"] = NO_LIMIT
    elif op is not None and interest is not None:
        out["interest_coverage"] = NO_LIMIT if abs(interest) < 1e-9 else min(op / abs(interest), NO_LIMIT)
    cash = _instant_value(tree, tags.get("cash", ()), fresh)
    if cash is not None and current is not None:
        out["cash_to_short_debt"] = NO_LIMIT if current <= 0 else min(cash / current, NO_LIMIT)
    if ocf is not None and capex is not None:
        out["capex_to_ocf"] = NO_LIMIT if ocf <= 0 else capex / ocf      # sin flujo operativo positivo: falla cualquier máximo
    assets = _instant_value(tree, tags.get("assets", ()), fresh)
    if fcf is not None and assets is not None and assets > 0:
        out["fcf_to_assets"] = fcf / assets
    out["net_buyback_pct"] = _net_buyback_pct(tree, fresh)
    out["roic"] = _roic(tree, tags, fresh, equity, debt)
    out.update(_fiscal_profit(tree, tags, fresh))
    return out


FetchJson = Callable[[str], Awaitable[Optional[dict]]]


class EdgarFinancialsProvider:
    def __init__(
        self, contact: str, requests_per_second: float = 5.0, concurrency: int = 4,
        fetch_json: Optional[FetchJson] = None, today: Callable[[], date] = date.today,
    ) -> None:
        # las cabeceras HTTP solo admiten ASCII: «Ángel» -> «Angel»
        ascii_contact = unicodedata.normalize("NFKD", contact).encode("ascii", "ignore").decode().strip()
        self._user_agent = f"ScannerOpcionesApp {ascii_contact}"
        self._interval = 1.0 / requests_per_second
        self._sem = asyncio.Semaphore(concurrency)
        self._fetch_json = fetch_json or self._http_get
        self._today = today
        self._next_slot = 0.0
        self._pace_lock = asyncio.Lock()
        self._ciks: dict[str, int] = {}
        self._ciks_at: Optional[datetime] = None
        self._client: Any = None
        self._blocked = False

    async def _pace(self) -> None:
        """Como mucho `requests_per_second` peticiones por segundo, aunque haya varias a la vez."""
        async with self._pace_lock:
            wait = self._next_slot - time.monotonic()
            self._next_slot = max(self._next_slot, time.monotonic()) + self._interval
        if wait > 0:
            await asyncio.sleep(wait)

    async def _http_get(self, url: str) -> Optional[dict]:
        import httpx

        if self._client is None:
            self._client = httpx.AsyncClient(headers={"User-Agent": self._user_agent}, timeout=60)
        await self._pace()
        r = await self._client.get(url)
        if r.status_code == 404:
            return None
        if r.status_code in (403, 429):    # la SEC nos limita o bloquea: no se insiste
            self._blocked = True
            raise FinancialsError(f"SEC EDGAR respondió {r.status_code} (¿falta edgar.contact o demasiadas peticiones?)")
        r.raise_for_status()
        return r.json()

    async def _ticker_map(self) -> dict[str, int]:
        if self._ciks and self._ciks_at and datetime.now() - self._ciks_at < timedelta(hours=12):
            return self._ciks
        data = await self._fetch_json(TICKERS_URL)
        if not data:
            raise FinancialsError("SEC EDGAR no devolvió la lista de tickers")
        self._ciks = {v["ticker"].upper(): int(v["cik_str"]) for v in data.values()}
        self._ciks_at = datetime.now()
        return self._ciks

    def _cik(self, ticker: str) -> Optional[int]:
        t = ticker.upper()
        for candidate in (t, t.replace("-", "."), t.replace(".", "-")):
            if candidate in self._ciks:
                return self._ciks[candidate]
        return None

    async def get_financials(self, tickers: list[str]) -> dict[str, Financials]:
        try:
            await self._ticker_map()
        except FinancialsError:
            raise
        except Exception as exc:
            raise FinancialsError(f"SEC EDGAR: {type(exc).__name__}: {str(exc)[:200]}") from exc
        self._blocked = False
        today = self._today()
        out: dict[str, Financials] = {}

        async def one(ticker: str) -> None:
            cik = self._cik(ticker)
            if cik is None:
                out[ticker] = Financials()     # no cotiza en EE. UU. / sin CIK: se anota que se intentó
                return
            async with self._sem:
                if self._blocked:
                    return
                try:
                    data = await self._fetch_json(FACTS_URL.format(cik=cik))
                except FinancialsError:
                    raise
                except Exception as exc:
                    log.info("EDGAR: %s no disponible: %s", ticker, exc)
                    return
            out[ticker] = (parse_company_facts(data, today) if data else None) or Financials()

        results = await asyncio.gather(*(one(t) for t in tickers), return_exceptions=True)
        for r in results:
            if isinstance(r, FinancialsError):
                raise r
        return out

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None
