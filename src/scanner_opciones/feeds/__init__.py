"""Fuentes de contratos: orígenes que traen contratos ya concretos (ticker, strike, vencimiento, patas, prima) en lugar de tickers.

Un perfil de escaneo parte de tickers y busca contratos con sus filtros; una fuente de contratos (`ContractFeed`) no filtra: sus contratos se
valoran con los datos de hoy y se comparan con los perfiles de escaneo (`community/fit.py::profile_fits`)."""
