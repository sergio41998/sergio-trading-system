"""
Smart Money Tracker — Señales de hedge funds y Congreso
Sergio's trading system

Fuentes:
  - SEC EDGAR 13F (oficial, gratuito, sin key) — holdings trimestrales de hedge funds
  - Senate/House Stock Watcher (S3 público, gratuito, sin key) — trades del Congreso
  - Finnhub (opcional, FINNHUB_API_KEY en .env) — confirmación por ticker

Uso:
  python3.11 smart_money.py              # tabla completa en consola
  python3.11 smart_money.py --no-cache   # forzar refresh de APIs
  python3.11 smart_money.py --telegram   # + enviar resumen a Telegram
  python3.11 smart_money.py --json       # output JSON raw

Coste: €0 — sin LLM, solo APIs públicas

CAVEAT: Datos con retraso 13F ~45d / Congreso hasta 45d.
  Señal de confirmación de tesis, no de timing de entrada.
"""

import os
import re
import json
import time
import argparse
import requests
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta
from tabulate import tabulate

# ─── Config ───────────────────────────────────────────────────────────────────

SEC_BASE     = "https://data.sec.gov"        # submissions JSON
SEC_ARCHIVES = "https://www.sec.gov"         # filing documents (Archives/)
SEC_HEADERS  = {
    "User-Agent":      "sergio-trading sergio.drutas@gmail.com",
    "Accept-Encoding": "gzip, deflate",
    "Accept":          "application/json, text/html, */*",
}

TELEGRAM_TOKEN   = os.environ.get("TELEGRAM_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")
FINNHUB_KEY      = os.environ.get("FINNHUB_API_KEY", "")

CACHE_FILE         = "smart_money_cache.json"
CACHE_HOURS        = 24
MIN_CONGRESS_TRADE = 50_000   # USD — threshold inferior del rango de importe
CONGRESS_DAYS_BACK = 90       # días hacia atrás para trades del Congreso
MIN_SCORE          = 4.0      # score mínimo para aparecer en briefing/consola
MAX_DISPLAY_ROWS   = 10       # filas máximas por sección en consola

# ─── Fondos a seguir ──────────────────────────────────────────────────────────
# CIK obtenidos de EDGAR. Lista ampliable — añadir entradas a este array.

FUND_UNIVERSE = [
    {
        "name":  "Druckenmiller",
        "cik":   "0001536411",
        "emoji": "🐉",
        "note":  "",
    },
    {
        "name":  "Burry/Scion",
        "cik":   "0001649339",
        "emoji": "🐻",
        # Scion reporta posiciones pequeñas y frecuentemente usa puts. El 13F
        # no refleja cobertura neta; interpretar señales con cautela adicional.
        "note":  "puede incluir puts/posiciones pequeñas — interpretar con cautela",
    },
    {
        "name":  "Coatue",
        "cik":   "0001135730",
        "emoji": "🦅",
        "note":  "",
    },
    {
        "name":  "Tiger Global",
        "cik":   "0001167483",
        "emoji": "🐯",
        "note":  "",
    },
    {
        "name":  "Whale Rock",
        "cik":   "0001387322",
        "emoji": "🐋",
        "note":  "",
    },
    {
        "name":  "Lone Pine",
        "cik":   "0001061165",
        "emoji": "🌲",
        "note":  "",
    },
]

# ─── Thesis universe (ref: opportunity_scout.py) ──────────────────────────────

THESIS_MAP = {
    "ai_infrastructure": [
        "NVDA", "AMD", "TSM", "ASML", "AVGO", "UCTT", "MRVL", "ARM",
        "LRCX", "AMAT", "KLAC", "MU", "QCOM", "SMCI", "ONTO", "ENTG",
    ],
    "cybersecurity": [
        "PANW", "CRWD", "S", "CYBR", "ZS", "FTNT", "OKTA", "NET",
        "QLYS", "VRNS", "TENB",
    ],
    "defense_europe": [
        "RHM", "RTX", "NOC", "BWXT", "LDOS", "LHX", "BAH", "KTOS",
        "SAIC", "DRS", "AXON", "CACI",
    ],
    "energy_ai": [
        "VST", "VRT", "OKLO", "CEG", "ETN", "NRG", "CCJ", "SMR",
        "FSLR", "NEE", "PWR",
    ],
    "space_economy": [
        "ERJ", "RKLB", "IRDM", "ASTS", "GSAT", "VSAT", "LUNR",
    ],
    "ai_software_gov": [
        "PLTR", "MSFT", "GOOG", "SNOW", "DDOG", "BBAI", "PATH",
        "AI", "MDB", "ESTC",
    ],
}

ALL_THESIS_TICKERS = {t for tickers in THESIS_MAP.values() for t in tickers}

PORTFOLIO_TICKERS = {
    "NVDA", "AMD", "TSM", "ASML", "AVGO", "UCTT", "PANW", "CRWD",
    "RHM", "RTX", "NOC", "BWXT", "VST", "VRT", "ERJ", "PLTR",
    "GOOG", "MSFT", "AMZN", "OKLO",
}


def _get_ticker_thesis(ticker: str) -> str | None:
    for thesis, tickers in THESIS_MAP.items():
        if ticker in tickers:
            return thesis
    return None


# ─── Nombre → Ticker lookup ───────────────────────────────────────────────────
# Clave: nombre normalizado (UPPER) tal como aparece en nameOfIssuer del 13F.
# Las claves más largas/específicas tienen prioridad en el matching por substring.

TICKER_NAME_MAP = {
    # AI Infrastructure
    "NVIDIA CORPORATION": "NVDA",
    "NVIDIA CORP":        "NVDA",
    "NVIDIA":             "NVDA",
    "ADVANCED MICRO DEVICES": "AMD",
    "ADVANCED MICRO":     "AMD",
    "TAIWAN SEMICONDUCTOR MFG": "TSM",
    "TAIWAN SEMICONDUCTOR": "TSM",
    "TAIWAN SEMI":        "TSM",
    "TSMC":               "TSM",
    "ASML HOLDING":       "ASML",
    "ASML":               "ASML",
    "BROADCOM INC":       "AVGO",
    "BROADCOM":           "AVGO",
    "MARVELL TECHNOLOGY": "MRVL",
    "MARVELL TECH":       "MRVL",
    "ARM HOLDINGS":       "ARM",
    "LAM RESEARCH":       "LRCX",
    "APPLIED MATERIALS":  "AMAT",
    "KLA CORPORATION":    "KLAC",
    "KLA CORP":           "KLAC",
    "MICRON TECHNOLOGY":  "MU",
    "QUALCOMM":           "QCOM",
    "SUPER MICRO COMPUTER": "SMCI",
    "SUPER MICRO":        "SMCI",
    "ENTEGRIS":           "ENTG",
    "ONTO INNOVATION":    "ONTO",
    # Cybersecurity
    "PALO ALTO NETWORKS": "PANW",
    "CROWDSTRIKE HOLDINGS": "CRWD",
    "CROWDSTRIKE":        "CRWD",
    "SENTINELONE":        "S",
    "CYBERARK SOFTWARE":  "CYBR",
    "ZSCALER":            "ZS",
    "FORTINET":           "FTNT",
    "OKTA":               "OKTA",
    "CLOUDFLARE":         "NET",
    "QUALYS":             "QLYS",
    "VARONIS SYSTEMS":    "VRNS",
    "TENABLE HOLDINGS":   "TENB",
    # Defense
    "RTX CORP":           "RTX",
    "RAYTHEON TECHNOLOGIES": "RTX",
    "RAYTHEON":           "RTX",
    "NORTHROP GRUMMAN":   "NOC",
    "BWX TECHNOLOGIES":   "BWXT",
    "RHEINMETALL":        "RHM",
    "LEIDOS HOLDINGS":    "LDOS",
    "LEIDOS":             "LDOS",
    "L3HARRIS TECHNOLOGIES": "LHX",
    "L3HARRIS":           "LHX",
    "BOOZ ALLEN HAMILTON": "BAH",
    "BOOZ ALLEN":         "BAH",
    "KRATOS DEFENSE":     "KTOS",
    "KRATOS":             "KTOS",
    "SCIENCE APPLICATIONS": "SAIC",
    "AXON ENTERPRISE":    "AXON",
    "CACI INTERNATIONAL": "CACI",
    # Energy AI
    "VISTRA CORP":        "VST",
    "VISTRA":             "VST",
    "VERTIV HOLDINGS":    "VRT",
    "VERTIV":             "VRT",
    "OKLO INC":           "OKLO",
    "OKLO":               "OKLO",
    "CONSTELLATION ENERGY": "CEG",
    "EATON CORP":         "ETN",
    "EATON":              "ETN",
    "NRG ENERGY":         "NRG",
    "CAMECO CORP":        "CCJ",
    "CAMECO":             "CCJ",
    "FIRST SOLAR":        "FSLR",
    "NEXTERA ENERGY":     "NEE",
    "QUANTA SERVICES":    "PWR",
    # Space
    "EMBRAER":            "ERJ",
    "ROCKET LAB USA":     "RKLB",
    "ROCKET LAB":         "RKLB",
    "IRIDIUM COMMUNICATIONS": "IRDM",
    "IRIDIUM":            "IRDM",
    "AST SPACEMOBILE":    "ASTS",
    # AI Software
    "PALANTIR TECHNOLOGIES": "PLTR",
    "PALANTIR":           "PLTR",
    "MICROSOFT CORP":     "MSFT",
    "MICROSOFT":          "MSFT",
    "ALPHABET INC":       "GOOG",
    "ALPHABET":           "GOOG",
    "SNOWFLAKE INC":      "SNOW",
    "SNOWFLAKE":          "SNOW",
    "DATADOG INC":        "DDOG",
    "DATADOG":            "DDOG",
    "MONGODB INC":        "MDB",
    "MONGODB":            "MDB",
    "BIG BEAR AI":        "BBAI",
    "UIPATH":             "PATH",
    # Other major tech (frecuentes en portfolios tech)
    "AMAZON.COM INC":     "AMZN",
    "AMAZON.COM":         "AMZN",
    "AMAZON":             "AMZN",
    "APPLE INC":          "AAPL",
    "APPLE":              "AAPL",
    "META PLATFORMS":     "META",
    "META":               "META",
    "TESLA INC":          "TSLA",
    "TESLA":              "TSLA",
    "NETFLIX INC":        "NFLX",
    "NETFLIX":            "NFLX",
    "UBER TECHNOLOGIES":  "UBER",
    "UBER":               "UBER",
    "AIRBNB INC":         "ABNB",
    "AIRBNB":             "ABNB",
    "SHOPIFY INC":        "SHOP",
    "SHOPIFY":            "SHOP",
    "SALESFORCE INC":     "CRM",
    "SALESFORCE":         "CRM",
    "SERVICENOW INC":     "NOW",
    "SERVICENOW":         "NOW",
    "WORKDAY INC":        "WDAY",
    "WORKDAY":            "WDAY",
    "COINBASE GLOBAL":    "COIN",
    "COINBASE":           "COIN",
    "INTUITIVE SURGICAL": "ISRG",
    "MERCADOLIBRE":       "MELI",
    "ASTERA LABS":        "ALAB",
    "ARISTA NETWORKS":    "ANET",
    "PURE STORAGE":       "PSTG",
    "GITLAB INC":         "GTLB",
    "GITLAB":             "GTLB",
    # Otras tech frecuentes en fondos growth
    "ROKU INC":           "ROKU",
    "ROKU":               "ROKU",
    "SEA LTD":            "SE",
    "SEA LIMITED":        "SE",
    "STMICROELECTRONICS": "STM",
    "TWILIO INC":         "TWLO",
    "TWILIO":             "TWLO",
    "ROBLOX CORP":        "RBLX",
    "ROBLOX":             "RBLX",
    "UNITY SOFTWARE":     "U",
    "CONFLUENT":          "CFLT",
    "HASHICORP":          "HCP",
    "COUPANG INC":        "CPNG",
    "COUPANG":            "CPNG",
    "GRAB HOLDINGS":      "GRAB",
    "NUBANK":             "NU",
    "NU HOLDINGS":        "NU",
    "TOAST INC":          "TOST",
    "TOAST":              "TOST",
    "DUOLINGO":           "DUOL",
    "HUBSPOT":            "HUBS",
    "BILL HOLDINGS":      "BILL",
    "CELONIS":            "CLON",
}

# Ordenar por longitud descendente para que el matching substring prefiera la clave más específica
_NAME_MAP_SORTED = sorted(TICKER_NAME_MAP.keys(), key=len, reverse=True)


# ─── Cache ────────────────────────────────────────────────────────────────────

def _load_cache() -> dict:
    if not os.path.exists(CACHE_FILE):
        return {}
    try:
        with open(CACHE_FILE) as f:
            cache = json.load(f)
        cached_at = datetime.fromisoformat(cache.get("_cached_at", "2000-01-01"))
        if datetime.now() - cached_at > timedelta(hours=CACHE_HOURS):
            return {}
        return cache
    except Exception:
        return {}


def _save_cache(data: dict):
    data["_cached_at"] = datetime.now().isoformat()
    with open(CACHE_FILE, "w") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


# ─── SEC EDGAR — helpers HTTP ─────────────────────────────────────────────────

def _sec_get(url: str, timeout: int = 20) -> requests.Response:
    resp = requests.get(url, headers=SEC_HEADERS, timeout=timeout)
    resp.raise_for_status()
    return resp


_13F_NS = "http://www.sec.gov/edgar/document/thirteenf/informationtable"


# ─── SEC EDGAR — 13F parsing ─────────────────────────────────────────────────

def _get_13f_accessions(cik: str) -> list[tuple[str, str]]:
    """
    Retorna los 2 accession numbers más recientes de 13F-HR del fondo,
    uno por trimestre (prefiere enmiendas 13F-HR/A sobre el original).
    Returns [(accession, report_date), ...] orden más reciente primero.
    """
    cik_pad = cik.lstrip("0").zfill(10)
    data = _sec_get(f"{SEC_BASE}/submissions/CIK{cik_pad}.json").json()

    recent        = data.get("filings", {}).get("recent", {})
    forms         = recent.get("form", [])
    accessions    = recent.get("accessionNumber", [])
    report_dates  = recent.get("reportDate", [])
    filing_dates  = recent.get("filingDate", [])

    # Agrupar por trimestre (reportDate), preferir la enmienda más reciente
    by_quarter: dict[str, tuple[str, str]] = {}
    for i, form in enumerate(forms):
        if form not in ("13F-HR", "13F-HR/A"):
            continue
        if i >= len(accessions):
            continue
        rdate = report_dates[i] if i < len(report_dates) else ""
        fdate = filing_dates[i]  if i < len(filing_dates) else ""
        acc   = accessions[i]
        if rdate not in by_quarter or fdate > by_quarter[rdate][1]:
            by_quarter[rdate] = (acc, fdate)

    sorted_quarters = sorted(by_quarter.keys(), reverse=True)[:2]
    return [(by_quarter[q][0], q) for q in sorted_quarters]


def _get_infotable_url(cik: str, accession: str) -> str | None:
    """
    Parsea el índice HTML del filing y retorna la URL del infotable XML.
    El infotable es el .xml que no tiene prefijo xslForm13F y no es primary_doc.
    """
    cik_num    = cik.lstrip("0")
    acc_nodash = accession.replace("-", "")
    index_url  = f"{SEC_ARCHIVES}/Archives/edgar/data/{cik_num}/{acc_nodash}/{accession}-index.htm"
    try:
        html = _sec_get(index_url).text
        links = re.findall(r'href="(/Archives/edgar/data/[^"]+)"', html)
        candidates = []
        for link in links:
            if "xslForm13F" in link:
                continue
            fname = link.rsplit("/", 1)[-1].lower()
            if fname == "primary_doc.xml" or not fname.endswith(".xml"):
                continue
            candidates.append(link)
        if candidates:
            return f"{SEC_ARCHIVES}{candidates[0]}"
    except Exception:
        pass
    return None


def _parse_infotable_xml(xml_text: str) -> list[dict]:
    """
    Parsea el XML de la infotable 13F usando Clark-notation para namespaces.
    Soporta tanto el formato actual (con namespace) como el antiguo (sin él).
    """
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return []

    # Detectar namespace del root tag (puede ser "" en filings antiguos)
    ns = ""
    if root.tag.startswith("{"):
        ns = root.tag.split("}")[0][1:]

    def _t(tag: str) -> str:
        return f"{{{ns}}}{tag}" if ns else tag

    def _text(elem, tag: str) -> str:
        child = elem.find(_t(tag))
        return (child.text or "").strip() if child is not None else ""

    raw: dict[str, dict] = {}  # cusip → aggregated holding
    for row in root.iter(_t("infoTable")):
        name    = _text(row, "nameOfIssuer").upper()
        cusip   = _text(row, "cusip")
        value_k = int(_text(row, "value") or "0")
        put_call = _text(row, "putCall").upper()

        shrs    = row.find(_t("shrsOrPrnAmt"))
        shares   = int(_text(shrs, "sshPrnamt") or "0") if shrs is not None else 0
        shr_type = _text(shrs, "sshPrnamtType") or "SH" if shrs is not None else "SH"

        if value_k <= 0 or not name or not cusip:
            continue

        key = f"{cusip}|{put_call}"  # separate equities from options on same CUSIP
        if key in raw:
            # Agregar sub-cuentas del mismo fondo (filers consolidados como Coatue/Tiger Global)
            raw[key]["shares"]  += shares
            raw[key]["value_k"] += value_k
        else:
            raw[key] = {
                "name":     name,
                "cusip":    cusip,
                "value_k":  value_k,
                "shares":   shares,
                "shr_type": shr_type,
                "put_call": put_call,
            }
    return list(raw.values())


def _fetch_holdings(cik: str, accession: str) -> list[dict]:
    """Descarga y parsea el infotable de un filing 13F."""
    url = _get_infotable_url(cik, accession)
    if not url:
        return []
    resp = requests.get(url, headers=SEC_HEADERS, timeout=30)
    resp.raise_for_status()
    return _parse_infotable_xml(resp.text)


def _diff_holdings(current: list[dict], previous: list[dict]) -> list[dict]:
    """
    Añade campo 'change' a cada holding comparando con el trimestre anterior.
    Ignora opciones (put_call != ""). Incluye posiciones cerradas (SOLD).
    """
    prev_map = {h["cusip"]: h for h in previous if not h.get("put_call")}
    result   = []

    curr_cusips = set()
    for h in current:
        if h.get("put_call"):
            continue
        cusip = h["cusip"]
        curr_cusips.add(cusip)

        if cusip not in prev_map:
            change = "NEW"
        else:
            prev_s = prev_map[cusip]["shares"]
            curr_s = h["shares"]
            if curr_s > prev_s * 1.10:
                change = "INCREASED"
            elif curr_s < prev_s * 0.90:
                change = "DECREASED"
            else:
                change = "UNCHANGED"
        result.append({**h, "change": change})

    # Posiciones cerradas — en anterior pero no en actual
    for cusip, h in prev_map.items():
        if cusip not in curr_cusips:
            result.append({**h, "change": "SOLD", "value_k": 0, "shares": 0})

    return result


def _resolve_ticker(name: str) -> str | None:
    """
    Mapea nameOfIssuer del 13F a ticker. Lookup directo, luego substring.
    """
    name = name.upper().strip()
    if name in TICKER_NAME_MAP:
        return TICKER_NAME_MAP[name]
    for key in _NAME_MAP_SORTED:
        if len(key) >= 6 and key in name:
            return TICKER_NAME_MAP[key]
    return None


# ─── Fetch completo de fondos ─────────────────────────────────────────────────

def _fetch_all_funds(fund_filter: str | None = None) -> dict[str, dict]:
    """
    Descarga los 13F de todos los fondos de FUND_UNIVERSE.
    Retorna {fund_name: {ticker: {value_k, shares, change, report_date, note}}}.
    """
    all_data: dict[str, dict] = {}

    funds = FUND_UNIVERSE
    if fund_filter:
        funds = [f for f in FUND_UNIVERSE if fund_filter.lower() in f["name"].lower()]
        if not funds:
            print(f"  ⚠️  Fondo '{fund_filter}' no encontrado. Cargando todos.")
            funds = FUND_UNIVERSE

    for fund in funds:
        fname = fund["name"]
        cik   = fund["cik"]
        note  = fund.get("note", "")

        try:
            print(f"  → {fund['emoji']} {fname}: consultando EDGAR...")
            accessions = _get_13f_accessions(cik)
            time.sleep(0.15)  # ser buen ciudadano con la API de SEC

            if not accessions:
                print(f"    ⚠️  Sin 13F-HR encontrado")
                all_data[fname] = {}
                continue

            curr_acc, curr_date = accessions[0]
            prev_acc, _         = accessions[1] if len(accessions) > 1 else (None, None)

            print(f"    Q: {curr_date} | acc: {curr_acc}")

            curr_holdings = _fetch_holdings(cik, curr_acc)
            time.sleep(0.15)
            prev_holdings = _fetch_holdings(cik, prev_acc) if prev_acc else []
            if prev_acc:
                time.sleep(0.15)

            diff = _diff_holdings(curr_holdings, prev_holdings)
            print(f"    {len(curr_holdings)} holdings | {len(prev_holdings)} anteriores")

            fund_tickers: dict[str, dict] = {}
            unresolved_large: list[dict]  = []

            for h in diff:
                ticker = _resolve_ticker(h["name"])
                if ticker:
                    # Si el ticker aparece varias veces (clases de acciones), tomar la de mayor valor
                    if ticker not in fund_tickers or h["value_k"] > fund_tickers[ticker]["value_k"]:
                        fund_tickers[ticker] = {
                            "value_k":    h["value_k"],
                            "shares":     h["shares"],
                            "change":     h["change"],
                            "name":       h["name"],
                            "report_date": curr_date,
                            "note":       note,
                        }
                elif h["value_k"] >= 50_000:  # >$50M sin ticker identificado
                    unresolved_large.append(h)

            resolved = len(fund_tickers)
            unres    = len(unresolved_large)
            print(f"    ✅ {resolved} tickers resueltos | {unres} grandes sin resolver")

            all_data[fname] = fund_tickers

        except Exception as e:
            print(f"    ❌ Error en {fname}: {e}")
            all_data[fname] = {}

    return all_data


# ─── Congressional Trading ────────────────────────────────────────────────────

def _parse_amount_str(amount_str: str) -> int:
    """Extrae el extremo inferior del rango de importe (e.g. '$50,001 - $100,000' → 50001)."""
    try:
        first_part = amount_str.split("-")[0]
        digits     = re.sub(r"[^0-9]", "", first_part)
        return int(digits) if digits else 0
    except Exception:
        return 0


def _parse_date_flexible(date_str: str) -> datetime | None:
    """Parsea fechas en formato YYYY-MM-DD o MM/DD/YYYY."""
    for fmt in ("%Y-%m-%d", "%m/%d/%Y"):
        try:
            return datetime.strptime(date_str[:10], fmt)
        except ValueError:
            continue
    return None


def _fetch_congress_trades() -> dict[str, list[dict]]:
    """
    Descarga trades recientes del Congreso.
    Fuente 1: Senate Stock Watcher (S3 público).
    Fuente 2: House Stock Watcher (S3 público).
    Fuente 3: Finnhub por ticker (si FINNHUB_API_KEY disponible).
    """
    result: dict[str, list[dict]] = {}
    cutoff = datetime.now() - timedelta(days=CONGRESS_DAYS_BACK)

    def _process_trades(trades: list, source: str, name_key: str):
        added = 0
        for trade in trades:
            ticker = (trade.get("ticker") or "").strip().upper()
            if not ticker or ticker in ("N/A", ""):
                continue
            txn_str  = trade.get("transaction_date") or trade.get("disclosure_date") or ""
            txn_date = _parse_date_flexible(txn_str)
            if not txn_date or txn_date < cutoff:
                continue
            if _parse_amount_str(trade.get("amount", "")) < MIN_CONGRESS_TRADE:
                continue
            entry = {
                "source":     source,
                "name":       trade.get(name_key, ""),
                "type":       trade.get("type", ""),
                "date":       txn_str[:10],
                "amount_str": trade.get("amount", ""),
            }
            result.setdefault(ticker, []).append(entry)
            added += 1
        return added

    # URLs de cada fuente con fallbacks (los S3 buckets originales están 403 desde 2025)
    SENATE_URLS = [
        "https://senate-stock-watcher-data.s3-us-west-2.amazonaws.com/aggregate/all_transactions.json",
        "https://raw.githubusercontent.com/Ryhav/SenateStockWatcher/master/transactions.json",
    ]
    HOUSE_URLS = [
        "https://house-stock-watcher-data.s3-us-west-2.amazonaws.com/data/all_transactions.json",
        "https://raw.githubusercontent.com/house-stock-watcher/house-stock-watcher-data/master/data/all_transactions.json",
    ]

    # Fuente 1: Senate
    print("  → Congreso (Senate)...")
    senate_ok = False
    for url in SENATE_URLS:
        try:
            resp = requests.get(url, timeout=25)
            resp.raise_for_status()
            n    = _process_trades(resp.json(), "senate", "senator")
            print(f"    ✅ Senate: {n} trades relevantes  [{url.split('/')[2]}]")
            senate_ok = True
            break
        except Exception as e:
            print(f"    ⚠️  {url.split('/')[2]}: {e}")
    if not senate_ok:
        print("    ℹ️  Senate data no disponible — continúa sin ella")

    # Fuente 2: House
    print("  → Congreso (House)...")
    house_ok = False
    for url in HOUSE_URLS:
        try:
            resp = requests.get(url, timeout=30)
            resp.raise_for_status()
            n    = _process_trades(resp.json(), "house", "representative")
            print(f"    ✅ House: {n} trades añadidos  [{url.split('/')[2]}]")
            house_ok = True
            break
        except Exception as e:
            print(f"    ⚠️  {url.split('/')[2]}: {e}")
    if not house_ok:
        print("    ℹ️  House data no disponible — continúa sin ella")

    # Fuente 3: Finnhub (confirmación por ticker — requiere key)
    if FINNHUB_KEY:
        added = 0
        for ticker in ALL_THESIS_TICKERS | PORTFOLIO_TICKERS:
            try:
                resp = requests.get(
                    f"https://finnhub.io/api/v1/stock/congressional-trading?symbol={ticker}&token={FINNHUB_KEY}",
                    timeout=8,
                )
                if resp.status_code != 200:
                    continue
                for trade in resp.json().get("data", []):
                    txn_date = _parse_date_flexible(trade.get("transactionDate", ""))
                    if not txn_date or txn_date < cutoff:
                        continue
                    entry = {
                        "source":     "finnhub",
                        "name":       trade.get("name", ""),
                        "type":       trade.get("transactionCode", ""),
                        "date":       (trade.get("transactionDate") or "")[:10],
                        "amount_str": f"${trade.get('amount', 0):,}",
                    }
                    result.setdefault(ticker, []).append(entry)
                    added += 1
            except Exception:
                pass
        if added:
            print(f"    ✅ Finnhub: {added} trades adicionales")

    return result


# ─── Scoring ──────────────────────────────────────────────────────────────────

def _compute_signals(
    fund_data: dict[str, dict],
    congress_data: dict[str, list],
) -> dict[str, dict]:
    """
    Agrega holdings de fondos + trades del Congreso en señales por ticker.
    Retorna {ticker: signal_dict}.
    """
    # Agrupar por ticker a través de todos los fondos
    ticker_to_funds: dict[str, dict[str, dict]] = {}
    for fund_name, holdings in fund_data.items():
        for ticker, h in holdings.items():
            ticker_to_funds.setdefault(ticker, {})[fund_name] = h

    signals: dict[str, dict] = {}

    for ticker, fund_holdings in ticker_to_funds.items():
        thesis       = _get_ticker_thesis(ticker)
        in_portfolio = ticker in PORTFOLIO_TICKERS

        score         = 0.0
        funds_buying  = []
        funds_selling = []

        for fund_name, h in fund_holdings.items():
            change = h.get("change", "UNCHANGED")
            if change == "NEW":
                score += 3.0
                funds_buying.append(f"{fund_name}(NEW)")
            elif change == "INCREASED":
                score += 2.0
                funds_buying.append(f"{fund_name}(+)")
            elif change == "DECREASED":
                score -= 1.0
                funds_selling.append(f"{fund_name}(-)")
            elif change == "SOLD":
                score -= 2.0
                funds_selling.append(f"{fund_name}(SOLD)")
            else:  # UNCHANGED — confirma convicción, bonus menor
                score += 0.5

        # Bonus por consenso: cada fondo adicional comprando
        n_buying = len(funds_buying)
        if n_buying >= 2:
            score += (n_buying - 1) * 1.0

        if thesis:
            score += 2.0

        congress_entries = congress_data.get(ticker, [])
        congress_buys    = [
            t for t in congress_entries
            if "purchase" in t.get("type", "").lower() or "buy" in t.get("type", "").lower()
        ]
        has_congress = len(congress_buys) > 0
        if has_congress:
            score += 1.5

        # Señal dominante.
        # SALIDA_SEÑALADA: posición propia con presión vendedora y score bajo.
        # Tickers ya en EN MIS TESIS (score ≥ MIN_SCORE) no necesitan SALIDA —
        # la columna "Fondos vendiendo" ya muestra esa información.
        if in_portfolio and funds_selling and score < MIN_SCORE:
            signal = "SALIDA_SEÑALADA"
        elif thesis:
            signal = "CONFIRMA_TESIS"
        elif score >= MIN_SCORE:
            signal = "IDEA_NUEVA"
        else:
            signal = "RUIDO"

        report_dates = [
            h.get("report_date", "") for h in fund_holdings.values() if h.get("report_date")
        ]

        signals[ticker] = {
            "score":          round(score, 1),
            "thesis":         thesis,
            "in_portfolio":   in_portfolio,
            "funds_buying":   funds_buying,
            "funds_selling":  funds_selling,
            "congress":       has_congress,
            "congress_count": len(congress_buys),
            "congress_sample": congress_entries[:2],
            "signal":         signal,
            "report_date":    max(report_dates) if report_dates else "",
        }

    return signals


# ─── API pública ──────────────────────────────────────────────────────────────

def get_smart_money_signals(use_cache: bool = True) -> dict[str, dict]:
    """
    Retorna señales smart money por ticker.
    Consumible por trading_agents_etoro.py como input adicional.

    Returns:
        {
          ticker: {
            score: float,
            thesis: str | None,
            in_portfolio: bool,
            funds_buying: [str],    # e.g. ["Coatue(NEW)", "Tiger Global(+)"]
            funds_selling: [str],
            congress: bool,
            signal: "CONFIRMA_TESIS" | "IDEA_NUEVA" | "SALIDA_SEÑALADA" | "RUIDO",
            report_date: str,       # YYYY-MM-DD del último 13F
          }
        }
    """
    if use_cache:
        cache = _load_cache()
        if cache.get("signals"):
            return cache["signals"]

    print("\n🏛️  Smart Money Tracker — cargando datos...\n")

    fund_data    = _fetch_all_funds()
    congress_data = _fetch_congress_trades()
    signals      = _compute_signals(fund_data, congress_data)

    _save_cache({
        "fund_data":     fund_data,
        "congress_data": congress_data,
        "signals":       signals,
    })

    return signals


def get_smart_money_section() -> str:
    """
    Devuelve sección compacta para el Morning Briefing (Telegram HTML).
    Retorna "" si no hay datos o falla — no rompe el briefing.
    """
    try:
        signals = get_smart_money_signals(use_cache=True)
        if not signals:
            return ""

        confirma = sorted(
            [(t, s) for t, s in signals.items()
             if s["signal"] == "CONFIRMA_TESIS" and s["score"] >= MIN_SCORE],
            key=lambda x: -x[1]["score"],
        )[:4]

        nuevas = sorted(
            [(t, s) for t, s in signals.items()
             if s["signal"] == "IDEA_NUEVA" and s["score"] >= MIN_SCORE],
            key=lambda x: -x[1]["score"],
        )[:3]

        salidas = sorted(
            [(t, s) for t, s in signals.items()
             if s["signal"] == "SALIDA_SEÑALADA" and s["in_portfolio"]],
            key=lambda x: x[1]["score"],
        )[:3]

        if not confirma and not nuevas and not salidas:
            return ""

        lines = ["🏛️ <b>Smart Money</b>"]

        if confirma:
            lines.append("<b>Confirmando tesis:</b>")
            for ticker, s in confirma:
                funds_str  = ", ".join(s["funds_buying"][:2]) or "—"
                cong_str   = " 🏛️" if s["congress"] else ""
                lines.append(f"  {ticker} ({s['score']:.0f}pts) — {funds_str}{cong_str}")

        if nuevas:
            lines.append("<b>Ideas nuevas:</b>")
            for ticker, s in nuevas:
                funds_str = ", ".join(s["funds_buying"][:2]) or "—"
                lines.append(f"  {ticker} ({s['score']:.0f}pts) — {funds_str}")

        if salidas:
            lines.append("<b>⚠️ Salidas en tu portfolio:</b>")
            for ticker, s in salidas:
                funds_str = ", ".join(s["funds_selling"][:2]) or "—"
                lines.append(f"  {ticker} — {funds_str}")

        latest = max(
            (s.get("report_date", "") for s in signals.values()),
            default="",
        )
        lines.append(f"<i>Retraso 13F ~45d | Congreso hasta 45d | Q: {latest}</i>")

        return "\n".join(lines)

    except Exception as e:
        print(f"  ⚠️  Smart Money no disponible: {e}")
        return ""


# ─── Telegram ─────────────────────────────────────────────────────────────────

def _send_telegram(message: str) -> bool:
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        print("  ⚠️  TELEGRAM_TOKEN o TELEGRAM_CHAT_ID no configurados")
        return False
    try:
        resp = requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
            json={"chat_id": TELEGRAM_CHAT_ID, "text": message, "parse_mode": "HTML"},
            timeout=10,
        )
        return resp.status_code == 200
    except Exception as e:
        print(f"  ⚠️  Telegram error: {e}")
        return False


# ─── Consola ──────────────────────────────────────────────────────────────────

def _build_console_output(signals: dict[str, dict]) -> str:
    lines = [
        "",
        f"🏛️  SMART MONEY TRACKER — {datetime.now().strftime('%Y-%m-%d')}",
        "⚠️  Datos con retraso 13F ~45d / Congreso hasta 45d",
        "    Señal de confirmación de tesis, no de timing.\n",
    ]

    fund_notes = [(f["emoji"], f["name"], f["note"]) for f in FUND_UNIVERSE if f.get("note")]
    if fund_notes:
        for emoji, name, note in fund_notes:
            lines.append(f"  {emoji} {name}: {note}")
        lines.append("")

    # ── EN MIS TESIS ──────────────────────────────────────────────────────────
    in_thesis = sorted(
        [(t, s) for t, s in signals.items()
         if s["thesis"] and s["signal"] != "SALIDA_SEÑALADA" and s["score"] >= MIN_SCORE],
        key=lambda x: -x[1]["score"],
    )[:MAX_DISPLAY_ROWS]

    if in_thesis:
        lines.append("── EN MIS TESIS " + "─" * 56)
        rows = []
        for ticker, s in in_thesis:
            buying   = ", ".join(s["funds_buying"][:3])  or "—"
            selling  = ", ".join(s["funds_selling"][:2]) or "—"
            cong_str = "✓" if s["congress"] else "—"
            port_str = "✓" if s["in_portfolio"] else "—"
            rows.append([
                ticker, f"{s['score']:.1f}",
                (s["thesis"] or "—").replace("_", " "),
                buying, selling, cong_str, port_str,
            ])
        headers = ["Ticker", "Score", "Tesis", "Fondos comprando", "Fondos vendiendo", "Cong.", "Port."]
        lines.append(tabulate(rows, headers=headers, tablefmt="simple"))
        lines.append("")

    # ── IDEAS NUEVAS ──────────────────────────────────────────────────────────
    nuevas = sorted(
        [(t, s) for t, s in signals.items()
         if s["signal"] == "IDEA_NUEVA" and s["score"] >= MIN_SCORE],
        key=lambda x: -x[1]["score"],
    )[:MAX_DISPLAY_ROWS]

    if nuevas:
        lines.append("── IDEAS NUEVAS (fuera de mis tesis) " + "─" * 35)
        rows = []
        for ticker, s in nuevas:
            buying   = ", ".join(s["funds_buying"][:3]) or "—"
            cong_str = "✓" if s["congress"] else "—"
            rows.append([ticker, f"{s['score']:.1f}", buying, cong_str])
        lines.append(tabulate(rows, headers=["Ticker", "Score", "Fondos comprando", "Congreso"], tablefmt="simple"))
        lines.append("")

    # ── SALIDAS SEÑALADAS ─────────────────────────────────────────────────────
    # Solo tickers que Sergio tiene en portfolio
    salidas = sorted(
        [(t, s) for t, s in signals.items()
         if s["signal"] == "SALIDA_SEÑALADA" and s["in_portfolio"]],
        key=lambda x: x[1]["score"],
    )

    if salidas:
        lines.append("── ⚠️  SMART MONEY SALIENDO (posiciones tuyas) " + "─" * 25)
        rows = []
        for ticker, s in salidas:
            selling = ", ".join(s["funds_selling"][:3]) or "—"
            rows.append([ticker, f"{s['score']:.1f}", selling])
        lines.append(tabulate(rows, headers=["Ticker", "Score", "Fondos vendiendo"], tablefmt="simple"))
        lines.append("")

    if not in_thesis and not nuevas and not salidas:
        lines.append("  Sin señales con score ≥ 4.0 en este ciclo.")
        lines.append("")

    total    = len(signals)
    relevant = len([s for s in signals.values() if s["score"] >= MIN_SCORE])
    lines.append(f"  {total} tickers monitorizados | {relevant} con score ≥ {MIN_SCORE}")

    return "\n".join(lines)


# ─── __main__ ─────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    _env = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
    if os.path.exists(_env):
        with open(_env) as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    os.environ.setdefault(k.strip(), v.strip())

    # Reload env vars después de cargar .env
    TELEGRAM_TOKEN   = os.environ.get("TELEGRAM_TOKEN", "")
    TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")
    FINNHUB_KEY      = os.environ.get("FINNHUB_API_KEY", "")

    parser = argparse.ArgumentParser(description="Smart Money Tracker")
    parser.add_argument("--no-cache", action="store_true", help="Forzar refresh de APIs")
    parser.add_argument("--telegram", action="store_true", help="Enviar resumen a Telegram")
    parser.add_argument("--json",     action="store_true", help="Output JSON raw")
    parser.add_argument("--fund",     type=str, default=None, help="Filtrar por fondo (ej: Coatue)")
    args = parser.parse_args()

    use_cache = not args.no_cache

    if args.fund and use_cache:
        # Con filtro de fondo, no podemos usar el caché agregado — forzar refresh
        cache = _load_cache()
        if cache.get("fund_data") and args.fund.lower() in {k.lower() for k in cache["fund_data"]}:
            fund_data = {k: v for k, v in cache["fund_data"].items()
                         if args.fund.lower() in k.lower()}
            congress_data = cache.get("congress_data", {})
            signals = _compute_signals(fund_data, congress_data)
        else:
            fund_data = _fetch_all_funds(fund_filter=args.fund)
            congress_data = _load_cache().get("congress_data") or _fetch_congress_trades()
            signals = _compute_signals(fund_data, congress_data)
    else:
        signals = get_smart_money_signals(use_cache=use_cache)

    if args.json:
        print(json.dumps(signals, indent=2, ensure_ascii=False))
    else:
        print(_build_console_output(signals))

    if args.telegram:
        section = get_smart_money_section()
        if section:
            sent = _send_telegram(section)
            print(f"\n  📱 Telegram: {'✅ enviado' if sent else '❌ falló'}")
        else:
            print("\n  📱 Telegram: sin contenido para enviar")
