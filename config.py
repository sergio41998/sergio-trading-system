"""
Configuración central del sistema de trading de Sergio.
Importar desde aquí en lugar de duplicar en cada script.
"""

# Mapa de instrument IDs de eToro → tickers internos del sistema
# Fuente única de verdad — no duplicar en otros scripts
INSTRUMENT_MAP = {
    1001: "AAPL",   1002: "GOOG",   1003: "META",   1004: "MSFT",
    1005: "AMZN",   1024: "KO",     1032: "UNH",    1033: "RTX",
    1137: "NVDA",   1234: "AIR",    1320: "CABK",   1330: "IBE",
    1465: "GILD",   1545: "NOC",    1832: "AMD",    2040: "IAG",
    2312: "LNVGY",  2587: "RHM",    4124: "PANW",   4236: "AVGO",
    4363: "VST",    4481: "TSM",    4509: "ASML",   5506: "CRWD",
    7991: "PLTR",   8687: "SMFG",   8867: "VRT",    8956: "BWXT",
    9006: "ERJ",    9045: "UCTT",   9956: "OKLO",
}
