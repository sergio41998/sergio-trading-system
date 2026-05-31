"""
Earnings Blackout — Protección automática alrededor de earnings
Sergio's trading system

Qué hace:
- Comprueba las fechas de earnings de los tickers vía yfinance
- Define una ventana de blackout (T-2 a T+3 por defecto)
- Durante el blackout, el monitor NO ejecuta órdenes de ese ticker
- Evita entrar justo antes del evento binario más arriesgado

Uso como módulo (desde price_monitor.py):
    from earnings_blackout import is_in_blackout, get_blackout_status

    if is_in_blackout("PANW"):
        # saltar esta orden
        continue

Uso standalone (para ver el calendario):
    python3.11 earnings_blackout.py
    python3.11 earnings_blackout.py --check PANW
"""

import os
import json
import argparse
from datetime import datetime, timedelta

# ─── Config ────────────────────────────────────────────────────────────────────

DAYS_BEFORE = 2   # T-2: desactivar órdenes 2 días antes de earnings
DAYS_AFTER  = 3   # T+3: reactivar 3 días después (tras digestión del mercado)

CACHE_FILE  = "earnings_cache.json"
CACHE_HOURS = 24  # refrescar calendario cada 24h


# ─── Obtener fechas de earnings ────────────────────────────────────────────────

def get_earnings_date(ticker: str) -> str:
    """
    Obtiene la próxima fecha de earnings de un ticker vía yfinance.
    Retorna fecha ISO (YYYY-MM-DD) o None.
    """
    import yfinance as yf

    # Mapeo de tickers europeos
    yf_map = {"ASML": "ASML.AS", "RHM": "RHM.DE"}
    yf_ticker = yf_map.get(ticker, ticker)

    try:
        tk  = yf.Ticker(yf_ticker)
        cal = tk.calendar

        if cal is not None and isinstance(cal, dict):
            earn_dates = cal.get("Earnings Date")
            if earn_dates and len(earn_dates) > 0:
                earn_date = earn_dates[0]
                if hasattr(earn_date, "isoformat"):
                    return earn_date.isoformat()[:10]
                return str(earn_date)[:10]
    except Exception:
        pass
    return None


def load_cache() -> dict:
    """Carga el caché de fechas de earnings."""
    if not os.path.exists(CACHE_FILE):
        return {}
    try:
        with open(CACHE_FILE) as f:
            cache = json.load(f)
        # Comprobar si el caché es reciente
        cached_at = datetime.fromisoformat(cache.get("_cached_at", "2000-01-01"))
        if datetime.now() - cached_at > timedelta(hours=CACHE_HOURS):
            return {}  # caché expirado
        return cache
    except Exception:
        return {}


def save_cache(earnings_dates: dict):
    """Guarda el caché de fechas de earnings."""
    earnings_dates["_cached_at"] = datetime.now().isoformat()
    with open(CACHE_FILE, "w") as f:
        json.dump(earnings_dates, f, indent=2)


def refresh_earnings_calendar(tickers: list) -> dict:
    """
    Refresca el calendario de earnings para una lista de tickers.
    Usa caché para no consultar yfinance repetidamente.
    """
    cache = load_cache()
    if cache:
        # Devolver caché si todos los tickers están presentes
        if all(t in cache for t in tickers):
            return cache

    print("  Actualizando calendario de earnings...")
    earnings = {}
    for ticker in tickers:
        date = get_earnings_date(ticker)
        earnings[ticker] = date
        if date:
            print(f"    {ticker}: {date}")

    save_cache(earnings)
    return earnings


# ─── Lógica de blackout ────────────────────────────────────────────────────────

def is_in_blackout(ticker: str, earnings_date: str = None) -> bool:
    """
    Comprueba si un ticker está en ventana de blackout de earnings.
    Retorna True si NO se debe operar (T-2 a T+3).
    """
    if earnings_date is None:
        earnings_date = get_earnings_date(ticker)

    if not earnings_date:
        return False  # sin earnings conocido = no blackout

    try:
        earn = datetime.fromisoformat(earnings_date).date()
        today = datetime.today().date()

        blackout_start = earn - timedelta(days=DAYS_BEFORE)
        blackout_end   = earn + timedelta(days=DAYS_AFTER)

        return blackout_start <= today <= blackout_end
    except Exception:
        return False


def get_blackout_status(ticker: str, earnings_date: str = None) -> dict:
    """
    Retorna el estado detallado de blackout de un ticker.
    """
    if earnings_date is None:
        earnings_date = get_earnings_date(ticker)

    if not earnings_date:
        return {"in_blackout": False, "reason": "Sin earnings conocido"}

    try:
        earn  = datetime.fromisoformat(earnings_date).date()
        today = datetime.today().date()
        days_until = (earn - today).days

        in_blackout = is_in_blackout(ticker, earnings_date)

        if in_blackout:
            if days_until > 0:
                reason = f"Earnings en {days_until}d ({earnings_date}) — blackout pre-earnings"
            elif days_until == 0:
                reason = f"Earnings HOY ({earnings_date}) — blackout"
            else:
                reason = f"Earnings hace {abs(days_until)}d — blackout post-earnings (digestión)"
        else:
            if days_until > 0:
                reason = f"Earnings en {days_until}d — fuera de ventana blackout"
            else:
                reason = f"Earnings pasados hace {abs(days_until)}d — operativa normal"

        return {
            "in_blackout":   in_blackout,
            "earnings_date": earnings_date,
            "days_until":    days_until,
            "reason":        reason,
        }
    except Exception as e:
        return {"in_blackout": False, "reason": f"Error: {e}"}


# ─── Standalone ────────────────────────────────────────────────────────────────

def show_calendar(tickers: list):
    """Muestra el calendario de earnings y estado de blackout."""
    print(f"\n📅 Earnings Blackout — {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print(f"   Ventana: T-{DAYS_BEFORE} a T+{DAYS_AFTER}\n")

    earnings = refresh_earnings_calendar(tickers)

    in_blackout = []
    upcoming    = []

    for ticker in tickers:
        if ticker.startswith("_"):
            continue
        status = get_blackout_status(ticker, earnings.get(ticker))
        icon = "🔴" if status["in_blackout"] else "🟢"
        print(f"  {icon} {ticker:6} — {status['reason']}")

        if status["in_blackout"]:
            in_blackout.append(ticker)
        elif status.get("days_until", 999) <= 14 and status.get("days_until", -1) >= 0:
            upcoming.append((ticker, status["days_until"]))

    print()
    if in_blackout:
        print(f"  🔴 En blackout (no operar): {', '.join(in_blackout)}")
    if upcoming:
        upcoming.sort(key=lambda x: x[1])
        print(f"  ⏳ Próximos earnings: " + ", ".join(f"{t} ({d}d)" for t, d in upcoming))
    if not in_blackout and not upcoming:
        print(f"  ✅ Sin earnings próximos — operativa normal")
    print()


if __name__ == "__main__":
    # Cargar .env
    env_file = os.path.join(os.path.dirname(__file__), ".env")
    if os.path.exists(env_file):
        with open(env_file) as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    os.environ.setdefault(k.strip(), v.strip())

    parser = argparse.ArgumentParser(description="Earnings Blackout")
    parser.add_argument("--check", type=str, help="Comprobar un ticker específico")
    args = parser.parse_args()

    if args.check:
        status = get_blackout_status(args.check.upper())
        icon = "🔴 BLACKOUT" if status["in_blackout"] else "🟢 OPERATIVA NORMAL"
        print(f"\n{icon} — {args.check.upper()}")
        print(f"  {status['reason']}\n")
    else:
        # Portfolio + watchlist por defecto
        default_tickers = [
            "NVDA", "PLTR", "ASML", "TSM", "AMD", "VRT", "CRWD",
            "PANW", "AVGO", "RHM", "VST", "MU", "DDOG", "FSLR", "AXON"
        ]
        show_calendar(default_tickers)
