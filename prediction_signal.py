"""
Prediction Signal — Capa de señales de mercados de predicción
Sergio's trading system

Lee probabilidades de Kalshi (CFTC-regulated, endpoint público sin auth)
y Polymarket Gamma API (búsqueda) + CLOB API (precios en tiempo real).
Solo lectura — sin wallet, sin trading.

Legal: endpoints públicos de datos de mercado. No operativa.
Relevante desde Alemania: Polymarket bloquea operativa, no lectura de datos.

Uso:
  python3.11 prediction_signal.py             # señales en consola
  python3.11 prediction_signal.py --telegram  # + enviar a Telegram
  python3.11 prediction_signal.py --no-cache  # forzar refresh de APIs
"""

import os
import json
import requests
import argparse
from datetime import datetime, timedelta

# ─── Config ────────────────────────────────────────────────────────────────────

KALSHI_BASE      = "https://api.elections.kalshi.com/trade-api/v2"
POLYMARKET_GAMMA = "https://gamma-api.polymarket.com"
POLYMARKET_CLOB  = "https://clob.polymarket.com"

TELEGRAM_TOKEN   = os.environ.get("TELEGRAM_TOKEN",   "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")

CACHE_FILE  = "prediction_cache.json"
CACHE_HOURS = 1

# Umbrales de baja confianza
KALSHI_MIN_OI            = 500      # open interest (contratos)
POLYMARKET_MIN_LIQUIDITY = 5_000    # USD


# ─── Universe de eventos ────────────────────────────────────────────────────────
#
# kalshi_series: ticker de serie Kalshi para búsqueda directa (más precisa).
# kalshi_keywords: términos de texto para filtrar dentro de esa serie.
# poly_keywords: términos para buscar client-side en el pool de Gamma.
#   Ajustados a los mercados que REALMENTE existen (verificado 2026-05-31).
# sector_impacts: impacto si la probabilidad del evento es ALTA (≥50%).
#   Si la prob es baja (<50%) el impacto se invierte automáticamente.

EVENT_UNIVERSE = [
    {
        "key":           "fed_rates_high_dec_2026",
        "description":   "Tipos Fed ≥3.50% en diciembre 2026",
        "kalshi_series":  "KXFED",
        "kalshi_keywords": ["above 3.50", "Dec 9", "2026"],
        "poly_keywords":  ["fed rate cut", "rate cuts 2026", "federal reserve cut"],
        "sector_impacts": {
            "ai_infrastructure": "riesgo",
            "cybersecurity":     "riesgo",
            "ai_software_gov":   "riesgo",
            "defense_europe":    "neutral",
            "energy_ai":         "riesgo",
            "space_economy":     "riesgo",
        },
        "rationale": "Tipos altos →  compresión múltiplos growth/tech",
    },
    {
        "key":           "us_recession_2026",
        "description":   "Recesión en EEUU en 2026",
        "kalshi_series":  "KXRECSSNBER",
        "kalshi_keywords": ["recession", "2026"],
        "poly_keywords":  ["us recession", "recession 2026", "us recession by end"],
        "sector_impacts": {
            "ai_infrastructure": "riesgo",
            "cybersecurity":     "riesgo",
            "ai_software_gov":   "riesgo",
            "defense_europe":    "neutral",
            "energy_ai":         "riesgo",
            "space_economy":     "riesgo",
        },
        "rationale": "Recesión → compresión múltiplos y capex tech",
    },
    {
        "key":           "nato_conflict_escalation",
        "description":   "Escalada conflicto OTAN/Rusia en 2026",
        "kalshi_series":  None,
        "kalshi_keywords": ["NATO Russia", "Russia invade NATO", "NATO conflict"],
        "poly_keywords":  ["nato", "russia invade", "nato country", "troops ukraine"],
        "sector_impacts": {
            "defense_europe":    "favorable",
            "ai_software_gov":   "favorable",
            "cybersecurity":     "favorable",
            "ai_infrastructure": "neutral",
            "energy_ai":         "neutral",
            "space_economy":     "neutral",
        },
        "rationale": "Escalada → aceleración gasto defensa (RHM, RTX, PLTR)",
    },
    {
        "key":           "us_chip_export_restrictions",
        "description":   "Nuevas restricciones export chips US→China",
        "kalshi_series":  None,
        "kalshi_keywords": ["chip export ban", "semiconductor export China", "nvidia ban"],
        "poly_keywords":  ["chip export", "semiconductor", "nvidia ban", "export control",
                           "huawei", "h20", "chips act china"],
        "sector_impacts": {
            "ai_infrastructure": "riesgo",
            "cybersecurity":     "neutral",
            "ai_software_gov":   "neutral",
            "defense_europe":    "neutral",
            "energy_ai":         "neutral",
            "space_economy":     "neutral",
        },
        "rationale": "Restricciones → impacto NVDA/TSM/ASML revenues",
    },
    {
        "key":           "us_cpi_above_3pct",
        "description":   "CPI EEUU supera 3% anualizado",
        "kalshi_series":  None,
        "kalshi_keywords": ["CPI annual", "inflation above 3"],
        "poly_keywords":  ["cpi", "consumer price", "inflation above", "inflation rate"],
        "sector_impacts": {
            "ai_infrastructure": "riesgo",
            "cybersecurity":     "riesgo",
            "ai_software_gov":   "riesgo",
            "defense_europe":    "neutral",
            "energy_ai":         "favorable",
            "space_economy":     "riesgo",
        },
        "rationale": "CPI alto → Fed hawkish → comprime múltiplos growth",
    },
    {
        "key":           "us_china_tariffs_tech",
        "description":   "EEUU escala aranceles tech vs China",
        "kalshi_series":  None,
        "kalshi_keywords": ["China tariffs", "US China trade war", "tech tariff"],
        "poly_keywords":  ["tariff", "trade war", "china tariff", "import tax",
                           "trump tariff china"],
        "sector_impacts": {
            "ai_infrastructure": "riesgo",
            "cybersecurity":     "neutral",
            "ai_software_gov":   "neutral",
            "defense_europe":    "neutral",
            "energy_ai":         "neutral",
            "space_economy":     "riesgo",
        },
        "rationale": "Aranceles → supply chain semis y revenues Asia",
    },
]


# ─── Cache ─────────────────────────────────────────────────────────────────────

def _load_cache() -> dict:
    if not os.path.exists(CACHE_FILE):
        return {}
    try:
        with open(CACHE_FILE) as f:
            data = json.load(f)
        if datetime.now() - datetime.fromisoformat(data.get("_cached_at", "2000-01-01")) \
                > timedelta(hours=CACHE_HOURS):
            return {}
        return data
    except Exception:
        return {}

def _save_cache(signals: dict):
    to_save = {**signals, "_cached_at": datetime.now().isoformat()}
    try:
        with open(CACHE_FILE, "w") as f:
            json.dump(to_save, f, indent=2, ensure_ascii=False)
    except Exception:
        pass


# ─── Kalshi — público, sin auth ─────────────────────────────────────────────────

def _search_kalshi(event: dict) -> dict | None:
    """
    Busca en Kalshi sin autenticación.
    Si el evento tiene kalshi_series, busca dentro de esa serie directamente.
    Usa last_price_dollars o midpoint bid/ask (rango 0.0-1.0).
    """
    series   = event.get("kalshi_series")
    keywords = event.get("kalshi_keywords", [])

    params = {"status": "open", "limit": 200}
    if series:
        params["series_ticker"] = series

    markets = []
    cursor  = None
    for _ in range(3):   # hasta 3 páginas = 600 mercados
        if cursor:
            params["cursor"] = cursor
        try:
            resp = requests.get(f"{KALSHI_BASE}/markets", params=params, timeout=10)
            if resp.status_code != 200:
                return None
            data   = resp.json()
            chunk  = data.get("markets", [])
            markets.extend(chunk)
            cursor = data.get("cursor")
            if not cursor or len(chunk) < 200:
                break
        except Exception:
            return None

    candidates = []
    for m in markets:
        title = (m.get("title", "") + " " + m.get("yes_sub_title", "")).strip()
        score = sum(1 for kw in keywords if kw.lower() in title.lower())
        if score == 0:
            continue

        yes_ask = float(m.get("yes_ask_dollars") or 0)
        yes_bid = float(m.get("yes_bid_dollars") or 0)
        last    = float(m.get("last_price_dollars") or 0)

        if yes_ask > 0 and yes_bid > 0:
            prob = round((yes_ask + yes_bid) / 2, 3)
        elif last > 0:
            prob = round(last, 3)
        else:
            continue   # sin precio válido

        oi = float(m.get("open_interest_fp") or m.get("volume_fp") or 0)

        candidates.append({
            "title":    title,
            "prob":     prob,
            "volume":   oi,
            "source":   "kalshi",
            "low_conf": oi < KALSHI_MIN_OI,
            "kw_score": score,
        })

    if not candidates:
        return None

    candidates.sort(key=lambda x: (x["kw_score"], x["volume"]), reverse=True)
    return candidates[0]


# ─── Polymarket — Gamma (búsqueda) + CLOB (precios) ────────────────────────────

def _fetch_polymarket_pool(max_pages: int = 5) -> list:
    """
    Descarga hasta max_pages×100 mercados activos de Gamma para búsqueda client-side.
    La API ignora parámetros de texto — hay que filtrar localmente.
    """
    pool = []
    for offset in range(0, max_pages * 100, 100):
        try:
            resp = requests.get(
                f"{POLYMARKET_GAMMA}/markets",
                params={"active": "true", "closed": "false",
                        "limit": 100, "offset": offset},
                timeout=15,
            )
            if resp.status_code != 200:
                break
            chunk = resp.json() if isinstance(resp.json(), list) else []
            pool.extend(chunk)
            if len(chunk) < 100:
                break
        except Exception:
            break
    return pool

def _get_clob_price(yes_token_id: str) -> float | None:
    """
    Lee el precio mid del mercado en tiempo real desde el CLOB de Polymarket.
    Más preciso que outcomePrices de Gamma para decisiones de trading.
    """
    try:
        resp = requests.get(
            f"{POLYMARKET_CLOB}/midpoint",
            params={"token_id": yes_token_id},
            timeout=5,
        )
        if resp.status_code == 200:
            mid = resp.json().get("mid")
            if mid is not None:
                return round(float(mid), 3)
    except Exception:
        pass
    return None

def _title_score(title: str, keywords: list) -> int:
    t = title.lower()
    return sum(1 for kw in keywords if kw.lower() in t)

def _search_in_pool(pool: list, keywords: list) -> dict | None:
    """
    Filtra pool por keywords, elige el mercado de mayor liquidez.
    Enriquece el precio con CLOB midpoint (más preciso que Gamma outcomePrices).
    """
    candidates = []
    for m in pool:
        title = m.get("question", "")
        score = _title_score(title, keywords)
        if score == 0:
            continue

        liquidity = float(m.get("liquidityNum", 0) or m.get("liquidity", 0) or 0)

        # Precio inicial desde Gamma outcomePrices
        prices_raw = m.get("outcomePrices", [])
        if isinstance(prices_raw, str):
            try:
                prices_raw = json.loads(prices_raw)
            except Exception:
                prices_raw = []

        outcomes = m.get("outcomes", [])
        if isinstance(outcomes, str):
            try:
                outcomes = json.loads(outcomes)
            except Exception:
                outcomes = []

        yes_idx = next(
            (i for i, o in enumerate(outcomes) if str(o).lower() in ("yes", "sí")),
            0,
        )

        if not prices_raw or yes_idx >= len(prices_raw):
            continue
        try:
            gamma_prob = round(float(prices_raw[yes_idx]), 3)
        except (ValueError, TypeError):
            continue

        # Token YES para CLOB
        clob_tokens = m.get("clobTokenIds", [])
        if isinstance(clob_tokens, str):
            try:
                clob_tokens = json.loads(clob_tokens)
            except Exception:
                clob_tokens = []
        yes_token = clob_tokens[yes_idx] if yes_idx < len(clob_tokens) else None

        candidates.append({
            "title":     title,
            "prob":      gamma_prob,   # se sobreescribe abajo con CLOB
            "volume":    liquidity,
            "source":    "polymarket",
            "low_conf":  liquidity < POLYMARKET_MIN_LIQUIDITY,
            "kw_score":  score,
            "yes_token": yes_token,
        })

    if not candidates:
        return None

    candidates.sort(key=lambda x: (x["kw_score"], x["volume"]), reverse=True)
    best = candidates[0]

    # Enriquecer precio con CLOB (tiempo real)
    if best.get("yes_token"):
        clob_prob = _get_clob_price(best["yes_token"])
        if clob_prob is not None:
            best["prob"]        = clob_prob
            best["price_source"] = "clob"
        else:
            best["price_source"] = "gamma"
    else:
        best["price_source"] = "gamma"

    return best


# ─── Señal por evento ───────────────────────────────────────────────────────────

def _build_signal(event: dict, kalshi_r, poly_r) -> dict:
    """
    Combina resultados de ambas fuentes y construye la señal estructurada.
    Si el evento tiene kalshi_series, Kalshi tiene prioridad (búsqueda directa = más precisa).
    Sin series, gana el mayor volumen/liquidez.
    """
    if kalshi_r and poly_r:
        # Series-based Kalshi search is semantically precise; prefer it over pool keyword match
        result = kalshi_r if event.get("kalshi_series") else (
            kalshi_r if kalshi_r["volume"] >= poly_r["volume"] else poly_r
        )
    else:
        result = kalshi_r or poly_r

    if not result:
        return {
            "key":            event["key"],
            "description":    event["description"],
            "probability":    None,
            "source":         "unavailable",
            "market_title":   None,
            "volume":         0,
            "low_conf":       True,
            "sector_impacts": event["sector_impacts"],
            "rationale":      event["rationale"],
            "telegram_line":  f"⬜ {event['description']}: N/D",
        }

    prob     = result["prob"]
    source   = result["source"]
    title    = result["title"]
    low_conf = result["low_conf"]

    favorable_n = sum(1 for v in event["sector_impacts"].values() if v == "favorable")
    risk_n      = sum(1 for v in event["sector_impacts"].values() if v == "riesgo")
    net_impact  = "favorable" if favorable_n > risk_n else "riesgo"

    direction = net_impact if prob >= 0.5 else ("favorable" if net_impact == "riesgo" else "riesgo")

    if 0.4 <= prob < 0.6:
        icon = "🟡"
    elif direction == "favorable":
        icon = "🟢"
    else:
        icon = "🔴"

    arrow     = "↑" if direction == "favorable" else "↓"
    conf_flag = " ⚠️" if low_conf else ""
    prob_pct  = f"{prob * 100:.0f}%"

    affected     = [s.replace("_", "/") for s, v in event["sector_impacts"].items()
                    if v != "neutral"][:2]
    affected_str = "+".join(affected) if affected else "portfolio"

    telegram_line = (
        f"{icon} {event['description']}: {prob_pct} "
        f"({arrow} {direction} {affected_str}){conf_flag}"
    )

    return {
        "key":            event["key"],
        "description":    event["description"],
        "probability":    prob,
        "source":         source,
        "price_source":   result.get("price_source", source),
        "market_title":   title,
        "volume":         result["volume"],
        "low_conf":       low_conf,
        "sector_impacts": event["sector_impacts"],
        "rationale":      event["rationale"],
        "telegram_line":  telegram_line,
    }


# ─── API pública ────────────────────────────────────────────────────────────────

def get_prediction_signals(use_cache: bool = True) -> dict:
    """
    Retorna dict {event_key: signal_dict} para todos los eventos del universe.
    Consumible por cvar_gate, invest_advisor, etc.
    """
    if use_cache:
        cache = _load_cache()
        if cache:
            print("  📦 Usando caché de predicciones (< 1h)")
            return {k: v for k, v in cache.items() if not k.startswith("_")}

    print("  Descargando pool Polymarket (hasta 500 mercados)...")
    poly_pool = _fetch_polymarket_pool(max_pages=5)
    print(f"  {len(poly_pool)} mercados en pool")

    signals = {}
    for event in EVENT_UNIVERSE:
        print(f"  Buscando: {event['description']}...")
        kalshi_r = _search_kalshi(event)
        poly_r   = _search_in_pool(poly_pool, event["poly_keywords"])
        signals[event["key"]] = _build_signal(event, kalshi_r, poly_r)

    _save_cache(signals)
    return signals


# ─── Telegram ──────────────────────────────────────────────────────────────────

def send_telegram(message: str) -> bool:
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        return False
    try:
        resp = requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
            json={"chat_id": TELEGRAM_CHAT_ID, "text": message, "parse_mode": "HTML"},
            timeout=10,
        )
        return resp.status_code == 200
    except Exception:
        return False

def build_prediction_message(signals: dict) -> str:
    date_str = datetime.now().strftime("%d %b %Y, %H:%M")
    msg = (
        f"🔮 <b>Prediction Markets — {date_str}</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━\n\n"
    )
    source_counts: dict = {}
    for s in signals.values():
        src = s.get("source", "unavailable")
        source_counts[src] = source_counts.get(src, 0) + 1
        msg += s["telegram_line"] + "\n"

    sources_str = " · ".join(
        f"{src.capitalize()} ({n})" for src, n in source_counts.items() if n > 0
    )
    msg += f"\n<i>Fuentes: {sources_str}</i>"
    if any(s.get("low_conf") for s in signals.values()):
        msg += "\n<i>⚠️ = volumen bajo, señal poco fiable</i>"
    return msg


# ─── Consola ───────────────────────────────────────────────────────────────────

def print_signals(signals: dict):
    print(f"\n{'═'*65}")
    print(f"  PREDICTION MARKETS — {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print(f"{'═'*65}\n")

    for s in signals.values():
        prob_str  = f"{s['probability'] * 100:.0f}%" if s["probability"] is not None else "N/D"
        src       = s.get("source", "N/D").upper()
        psrc      = s.get("price_source", src).upper()
        title     = s.get("market_title") or "—"
        low_conf  = "  ⚠️ bajo vol." if s.get("low_conf") else ""
        vol       = s.get("volume", 0) or 0
        vol_label = "OI contr." if s.get("source") == "kalshi" else "USD liq."
        vol_str   = f"{vol:,.0f}" if vol >= 1 else "0"

        # Mostrar fuente de precio si difiere de la fuente del mercado
        price_tag = f" (precio: {psrc})" if psrc != src else ""

        print(f"  {s['telegram_line']}")
        print(f"    Mercado : {title}")
        print(f"    Fuente  : {src}{price_tag} | Vol: {vol_str} {vol_label}{low_conf}")
        print(f"    Tesis   : {s['rationale']}")
        print()

    available = [s for s in signals.values() if s["source"] != "unavailable"]
    low       = [s for s in available if s.get("low_conf")]
    nd        = [s for s in signals.values() if s["source"] == "unavailable"]
    print(f"{'─'*65}")
    print(f"  {len(available)}/{len(signals)} eventos con datos "
          f"| {len(low)} bajo volumen | {len(nd)} sin mercado activo")
    if nd:
        print(f"  Sin mercado: {', '.join(s['description'] for s in nd)}")
    print()


# ─── Main ──────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    env_file = os.path.join(os.path.dirname(__file__), ".env")
    if os.path.exists(env_file):
        with open(env_file) as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    os.environ.setdefault(k.strip(), v.strip())

    TELEGRAM_TOKEN   = os.environ.get("TELEGRAM_TOKEN",   "")
    TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")

    parser = argparse.ArgumentParser(description="Prediction Signal")
    parser.add_argument("--telegram", action="store_true", help="Enviar a Telegram")
    parser.add_argument("--no-cache", action="store_true", help="Forzar refresh de APIs")
    args = parser.parse_args()

    print(f"\n🔮 Prediction Signal | {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print(f"  Kalshi: {KALSHI_BASE}")
    print(f"  Polymarket: Gamma + CLOB")
    print()

    signals = get_prediction_signals(use_cache=not args.no_cache)
    print_signals(signals)

    if args.telegram:
        msg  = build_prediction_message(signals)
        sent = send_telegram(msg)
        print(f"  📱 Telegram: {'✅ enviado' if sent else '❌ falló'}\n")
