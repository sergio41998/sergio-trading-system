"""
eToro API Client — Fase 1
Sergio's personal trading system
"""

import os
import uuid
import json
import requests
from datetime import datetime
from tabulate import tabulate

BASE_URL = "https://public-api.etoro.com/api/v1"
MODE = "real"  # "demo" | "real"

API_KEY  = os.environ.get("ETORO_API_KEY",  "YOUR_API_KEY_HERE")
USER_KEY = os.environ.get("ETORO_USER_KEY", "YOUR_USER_KEY_HERE")

from config import INSTRUMENT_MAP

# Caché por sesión — yfinance solo se llama una vez por proceso
_eur_usd_cache: float | None = None


def get_eur_usd_rate() -> float:
    """
    Tipo de cambio EUR/USD en tiempo real vía yfinance.
    Caché de sesión: solo una llamada de red por proceso.
    Fallback a 1.12 si yfinance no responde.
    """
    global _eur_usd_cache
    if _eur_usd_cache:
        return _eur_usd_cache
    try:
        import yfinance as yf
        hist = yf.Ticker("EURUSD=X").history(period="1d")
        if not hist.empty:
            _eur_usd_cache = float(hist["Close"].iloc[-1])
            return _eur_usd_cache
    except Exception:
        pass
    return 1.12  # fallback conservador


def _headers():
    return {
        "x-api-key":    API_KEY,
        "x-user-key":   USER_KEY,
        "x-request-id": str(uuid.uuid4()),
        "Content-Type": "application/json",
    }

def get(endpoint, params=None):
    url = f"{BASE_URL}{endpoint}"
    resp = requests.get(url, headers=_headers(), params=params or {}, timeout=15)
    resp.raise_for_status()
    return resp.json()

def get_portfolio():
    return get(f"/trading/info/{MODE}/pnl")

def get_account_balance():
    data = get(f"/trading/info/{MODE}/pnl")
    cp = data.get("clientPortfolio", {})
    positions = cp.get("positions", [])
    mirrors   = cp.get("mirrors", [])
    total_invested = sum(p.get("amount", 0) for p in positions)
    total_pnl      = sum(p.get("unrealizedPnL", {}).get("pnL", 0) for p in positions)
    for m in mirrors:
        total_invested += sum(p.get("amount", 0) for p in m.get("positions", []))
        total_pnl      += sum(p.get("unrealizedPnL", {}).get("pnL", 0) for p in m.get("positions", []))
    return {
        "equity":        cp.get("credit", total_invested + total_pnl),
        "unrealizedPnL": total_pnl,
        "invested":      total_invested,
    }

def search_instrument(symbol: str) -> list:
    try:
        data = get("/instruments/search", params={"q": symbol, "limit": 5})
        return data.get("instruments", [])
    except:
        return []

def print_account(account):
    invested = account.get("invested", 0)
    pl       = account.get("unrealizedPnL", 0)
    pl_pct   = (pl / invested * 100) if invested else 0
    rate     = get_eur_usd_rate()
    inv_eur  = invested / rate
    pl_eur   = pl / rate
    print(f"\n{'─'*45}")
    print(f"  Modo         : {MODE.upper()}")
    print(f"  Invertido    : €{inv_eur:,.2f}  (${invested:,.2f} @ {rate:.4f})")
    print(f"  P&L abierto  : €{pl_eur:,.2f}  ({pl_pct:.2f}%)")
    print(f"{'─'*45}\n")

def print_portfolio(data):
    cp        = data.get("clientPortfolio", {})
    positions = cp.get("positions", [])
    mirrors   = cp.get("mirrors", [])

    # Agrupar por ticker
    grouped = {}
    for p in positions:
        iid    = p.get("instrumentID")
        ticker = INSTRUMENT_MAP.get(iid, f"ID:{iid}")
        pnl    = p.get("unrealizedPnL", {}).get("pnL", 0)
        amt    = p.get("amount", 0)
        if ticker not in grouped:
            grouped[ticker] = {"invested": 0, "pnl": 0}
        grouped[ticker]["invested"] += amt
        grouped[ticker]["pnl"]      += pnl

    if grouped:
        rows = []
        for ticker, v in grouped.items():
            amt = v["invested"]
            pl  = v["pnl"]
            pct = (pl / amt * 100) if amt else 0
            rows.append([ticker, f"${amt:.0f}", f"${pl:.2f}", f"{pct:.1f}%"])

        rate = get_eur_usd_rate()
        rows_eur = [
            [t, f"€{v['invested']/rate:.0f}", f"€{v['pnl']/rate:.2f}", f"{(v['pnl']/v['invested']*100) if v['invested'] else 0:.1f}%"]
            for t, v in grouped.items()
        ]
        rows_eur.sort(key=lambda r: float(r[2].replace("€","").replace("-","")), reverse=True)
        print(f"📌 Posiciones ({len(grouped)} tickers, {len(positions)} tramos) — USD→EUR @ {rate:.4f}:")
        print(tabulate(rows_eur,
                       headers=["Ticker", "Invertido", "P&L €", "P&L %"],
                       tablefmt="rounded_outline"))

    if mirrors:
        print(f"\n🔁 Copy portfolios: {len(mirrors)}")
        for m in mirrors:
            pl = sum(p.get("unrealizedPnL",{}).get("pnL",0) for p in m.get("positions",[]))
            print(f"  → {m.get('parentUsername','?'):20} | {len(m.get('positions',[]))} pos | P&L: ${pl:.2f}")

def main():
    print(f"\n🤖 eToro Client | Modo: {MODE.upper()} | {datetime.now().strftime('%Y-%m-%d %H:%M')}\n")
    try:
        data    = get_portfolio()
        account = get_account_balance()
        print_account(account)
        print_portfolio(data)
    except Exception as e:
        print(f"[ERROR] {e}")
    print("\n✅ OK\n")

if __name__ == "__main__":
    main()
