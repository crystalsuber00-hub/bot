"""Tradable universe: SPDR sector ETFs (for the sector scan) and liquid US names per sector.

These are today's large, liquid names, so any backtest over them carries survivorship bias
(companies that shrank or were delisted aren't here). Edit the lists freely.
"""
from __future__ import annotations

BENCHMARK = "SPY"

SECTOR_ETFS = {
    "Energy": "XLE",
    "Technology": "XLK",
    "Financials": "XLF",
    "Health Care": "XLV",
    "Industrials": "XLI",
    "Consumer Discretionary": "XLY",
    "Consumer Staples": "XLP",
    "Utilities": "XLU",
    "Materials": "XLB",
    "Real Estate": "XLRE",
    "Communication Services": "XLC",
}

SECTOR_STOCKS = {
    # Energy plus the grid/nuclear/power names the article folded in
    "Energy": ["XOM", "CVX", "COP", "OXY", "SLB", "HAL", "EOG", "MPC", "PSX", "VLO", "KMI", "WMB",
               "OKE", "EQT", "ENB", "DVN", "FANG", "BKR", "CEG", "VST", "CCJ", "UEC", "DNN",
               "GEV", "PWR", "VRT", "ETN", "NEE"],
    "Technology": ["AAPL", "MSFT", "NVDA", "AVGO", "ORCL", "CRM", "AMD", "ADBE", "CSCO", "ACN",
                   "IBM", "INTU", "QCOM", "TXN", "AMAT", "MU", "LRCX", "KLAC", "ANET", "PANW",
                   "NOW", "SNPS", "CDNS", "ADI"],
    "Financials": ["JPM", "BAC", "WFC", "GS", "MS", "C", "SCHW", "BLK", "AXP", "SPGI", "CB",
                   "PGR", "ICE", "CME", "USB", "PNC", "TFC", "COF", "AIG", "MET"],
    "Health Care": ["LLY", "UNH", "JNJ", "ABBV", "MRK", "TMO", "ABT", "DHR", "PFE", "AMGN",
                    "ISRG", "SYK", "BSX", "MDT", "VRTX", "REGN", "GILD", "BMY", "ELV", "CI",
                    "ZTS", "HCA"],
    "Industrials": ["GE", "CAT", "RTX", "HON", "UNP", "BA", "DE", "LMT", "UPS", "ADP", "WM",
                    "GD", "NOC", "ITW", "EMR", "CSX", "NSC", "FDX", "PH", "TT", "JCI", "CARR"],
    "Consumer Discretionary": ["AMZN", "TSLA", "HD", "MCD", "LOW", "BKNG", "NKE", "SBUX", "TJX",
                               "CMG", "ORLY", "AZO", "MAR", "HLT", "ROST", "GM", "F", "YUM",
                               "DHI", "LEN"],
    "Consumer Staples": ["PG", "COST", "WMT", "KO", "PEP", "PM", "MO", "MDLZ", "CL", "KMB",
                         "GIS", "KHC", "STZ", "SYY", "KR", "HSY", "KDP", "ADM", "TGT", "EL"],
    "Utilities": ["NEE", "SO", "DUK", "D", "AEP", "SRE", "EXC", "XEL", "PEG", "ED", "WEC",
                  "EIX", "ETR", "AWK", "DTE", "PPL", "CNP", "FE", "AES", "NRG"],
    "Materials": ["LIN", "SHW", "APD", "ECL", "FCX", "NEM", "DOW", "DD", "NUE", "VMC", "MLM",
                  "PPG", "IFF", "ALB", "CF", "MOS", "STLD", "IP", "PKG", "BALL"],
    "Real Estate": ["PLD", "AMT", "EQIX", "CCI", "PSA", "O", "SPG", "WELL", "DLR", "VICI",
                    "AVB", "EQR", "EXR", "SBAC", "WY", "ARE", "INVH", "MAA", "IRM", "CBRE"],
    "Communication Services": ["GOOGL", "META", "NFLX", "DIS", "CMCSA", "T", "VZ", "TMUS",
                               "CHTR", "EA", "TTWO", "WBD", "OMC", "LYV", "MTCH"],
}


def all_symbols() -> list[str]:
    out = {BENCHMARK, *SECTOR_ETFS.values()}
    for names in SECTOR_STOCKS.values():
        out.update(names)
    return sorted(out)
