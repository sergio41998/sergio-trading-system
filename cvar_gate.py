"""
CVaR Gate + Morning Briefing — Sergio's trading system
Versión completa con los 5 puntos + extras

Qué incluye:
1. Contexto macro (VIX, SPY, EUR/USD)
2. Comparación histórica CVaR (hoy vs ayer vs semana)
3. P&L del día anterior del portfolio
4. Earnings de la semana de tus posiciones
5. Modo de mercado (Normal / Corrección / Crisis)
+ Correlación de portfolio
+ Resumen ejecutivo en lenguaje natural

Uso:
  python3.11 cvar_gate.py            # briefing completo
  python3.11 cvar_gate.py --test     # prueba Telegram
  python3.11 cvar_gate.py --status   # estado actual
  python3.11 cvar_gate.py --monitor  # loop cada hora
"""

import os
import json
import argparse
import requests
import numpy as np
from datetime import datetime, timedelta

# ─── Config ────────────────────────────────────────────────────────────────────

CVAR_THRESHOLD  = 0.04
CVAR_CONFIDENCE = 0.99
LOOKBACK_DAYS   = 60
NAV_EUR         = 15000

TELEGRAM_TOKEN   = os.environ.get("TELEGRAM_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")
STATUS_FILE      = "cvar_status.json"
HISTORY_FILE     = "cvar_history.jsonl"

PORTFOLIO = {
    "VRT":  0.095, "ASML": 0.088, "AMD":  0.082, "TSM":  0.075,
    "RHM":  0.065, "NVDA": 0.062, "SMFG": 0.058, "PANW": 0.052,
    "CRWD": 0.048, "PLTR": 0.045, "AVGO": 0.042, "UCTT": 0.038,
    "VST":  0.035, "BWXT": 0.032, "OKLO": 0.028, "GOOG": 0.025,
    "MSFT": 0.022, "AMZN": 0.020, "RTX":  0.018, "KO":   0.015,
}

BETA_MAP = {
    "NVDA": 2.2, "PLTR": 2.0, "OKLO": 2.0, "UCTT": 1.8, "AMD": 2.0,
    "VRT":  1.6, "TSM":  1.5, "ASML": 1.5, "SMFG": 1.3, "PANW": 1.4,
    "CRWD": 1.6, "AVGO": 1.3, "VST":  1.4, "BWXT": 1.0, "GOOG": 1.1,
    "MSFT": 1.0, "AMZN": 1.2, "RTX":  0.9, "KO":   0.6, "RHM":  1.2,
}

YF_TICKER_MAP = {
    "ASML": "ASML.AS", "RHM": "RHM.DE", "SMFG": "SMFG",
}


# ─── Portfolio real desde eToro API ────────────────────────────────────────────

def build_portfolio_from_etoro() -> tuple:
    """
    Construye pesos reales del portfolio desde la API de eToro.
    Retorna (weights_dict, nav_eur, source_label).
    Si falla, retorna (None, None, "pesos manuales (fallback)").
    """
    try:
        from etoro_client import get_portfolio as _get_portfolio, get_account_balance, get_eur_usd_rate, INSTRUMENT_MAP

        data    = _get_portfolio()
        account = get_account_balance()

        cp        = data.get("clientPortfolio", {})
        positions = cp.get("positions", [])

        amounts = {}
        for p in positions:
            ticker = INSTRUMENT_MAP.get(p.get("instrumentID"))
            if ticker:
                amounts[ticker] = amounts.get(ticker, 0) + p.get("amount", 0)

        total = sum(amounts.values())
        if total == 0:
            return None, None, "pesos manuales (fallback — sin posiciones)"

        weights = {t: amt / total for t, amt in amounts.items()}

        # eToro reporta en USD — convertir a EUR con tipo de cambio real
        eur_usd = get_eur_usd_rate()
        nav_usd = account.get("invested", 0) + account.get("unrealizedPnL", 0)
        nav_eur = round(nav_usd / eur_usd, 0) if nav_usd else NAV_EUR

        return weights, nav_eur, f"portfolio real ({len(weights)} tickers, €{nav_eur:,.0f} @ {eur_usd:.4f})"

    except Exception as e:
        print(f"  ⚠️  No se pudo cargar portfolio real: {e}")
        print(f"  → Usando pesos manuales como fallback")
        return None, None, "pesos manuales (fallback)"


# ─── Prediction Markets section (opcional) ────────────────────────────────────

def get_prediction_section() -> str:
    """
    Importa señales de prediction_signal.py y devuelve una sección compacta
    para el Morning Briefing. Retorna "" si falla — no rompe el briefing.
    """
    try:
        from prediction_signal import get_prediction_signals, build_prediction_message  # noqa: F401
        signals = get_prediction_signals(use_cache=True)
        if not signals:
            return ""

        active = [s for s in signals.values() if s.get("source") != "unavailable"]
        if not active:
            return ""

        lines   = ["🔮 <b>Prediction Markets</b>"]
        sources = set()
        for s in active:
            lines.append(s["telegram_line"])
            src = s.get("source", "")
            if src:
                sources.add(src.capitalize())

        nd_count = len(signals) - len(active)
        src_str  = " · ".join(sorted(sources))
        footer   = f"<i>{src_str}"
        if nd_count:
            footer += f" | {nd_count} sin mercado activo"
        footer  += "</i>"
        lines.append(footer)

        return "\n".join(lines)

    except Exception as e:
        print(f"  ⚠️  Prediction Markets no disponible: {e}")
        return ""


# ─── Smart Money section (solo lunes) ─────────────────────────────────────────

def get_smart_money_section_for_briefing() -> str:
    """
    Retorna la sección 🏛️ Smart Money para el Morning Briefing.
    Solo se incluye los LUNES (datos trimestrales — no tiene sentido repetir a diario).
    Retorna "" cualquier otro día o si falla — no rompe el briefing.
    """
    if datetime.now().weekday() != 0:  # 0 = lunes
        return ""
    try:
        from smart_money import get_smart_money_section
        return get_smart_money_section()
    except Exception as e:
        print(f"  ⚠️  Smart Money no disponible: {e}")
        return ""


# ─── Telegram ──────────────────────────────────────────────────────────────────

def send_telegram(message: str) -> bool:
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        return False
    try:
        url  = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        resp = requests.post(url, json={
            "chat_id": TELEGRAM_CHAT_ID, "text": message, "parse_mode": "HTML"
        }, timeout=10)
        return resp.status_code == 200
    except:
        return False

def test_telegram():
    ok = send_telegram(
        "🤖 <b>Morning Briefing conectado</b>\n"
        "━━━━━━━━━━━━━━━━━\n"
        "Sistema de briefing diario activo.\n"
        f"🕐 {datetime.now().strftime('%Y-%m-%d %H:%M')}"
    )
    print("✅ Telegram OK" if ok else "❌ Telegram falló")
    return ok


# ─── Datos de mercado ───────────────────────────────────────────────────────────

def get_market_data() -> dict:
    """Descarga datos de portfolio + macro."""
    import yfinance as yf
    import pandas as pd

    end   = datetime.today()
    start = end - timedelta(days=LOOKBACK_DAYS + 10)

    # Tickers a descargar: portfolio + macro
    macro_tickers = {"^VIX": "VIX", "SPY": "SPY", "EURUSD=X": "EURUSD", "^TNX": "TNX"}
    all_tickers   = list(PORTFOLIO.keys()) + list(macro_tickers.keys())
    yf_map        = {**{t: YF_TICKER_MAP.get(t, t) for t in PORTFOLIO.keys()},
                     **{t: t for t in macro_tickers.keys()}}

    print(f"  Descargando {len(all_tickers)} tickers...")

    data = yf.download(
        [yf_map[t] for t in all_tickers],
        start=start.strftime("%Y-%m-%d"),
        end=end.strftime("%Y-%m-%d"),
        progress=False, auto_adjust=True
    )

    closes = data["Close"] if "Close" in data else data
    closes.columns = [str(c) for c in closes.columns]

    # Renombrar de vuelta a nombres originales
    rename = {v: k for k, v in yf_map.items()}
    closes = closes.rename(columns=rename)
    closes = closes.dropna(how="all")

    print(f"  ✅ {len(closes)} días de datos")
    return closes


def compute_portfolio_returns(closes, portfolio=None) -> tuple:
    """Retornos diarios del portfolio ponderado."""
    port = portfolio or PORTFOLIO
    portfolio_tickers = [t for t in port.keys() if t in closes.columns]
    total_w  = sum(port[t] for t in portfolio_tickers)
    weights  = np.array([port[t] / total_w for t in portfolio_tickers])
    ret_df   = closes[portfolio_tickers].pct_change().dropna()
    port_ret = ret_df.values @ weights
    return port_ret, ret_df


# ─── Punto 1: Contexto macro ───────────────────────────────────────────────────

def get_macro_context(closes) -> dict:
    """VIX, SPY variación, EUR/USD, yield 10Y."""
    ctx = {}
    today = closes.index[-1]

    for key, col in [("vix", "^VIX"), ("spy", "SPY"),
                     ("eurusd", "EURUSD=X"), ("tnx", "^TNX")]:
        col_name = col.replace("^", "").replace("=X", "")
        # Buscar columna en closes
        found = None
        for c in closes.columns:
            if col_name.lower() in str(c).lower() or col == c:
                found = c
                break
        if found and found in closes.columns:
            series = closes[found].dropna()
            if len(series) >= 2:
                ctx[key]          = float(series.iloc[-1])
                ctx[f"{key}_chg"] = float((series.iloc[-1] / series.iloc[-2] - 1) * 100)

    return ctx


def macro_to_text(ctx: dict) -> str:
    vix    = ctx.get("vix", 0)
    spy_ch = ctx.get("spy_chg", 0)
    eurusd = ctx.get("eurusd", 0)
    tnx    = ctx.get("tnx", 0)

    vix_label = "😰 Miedo extremo" if vix > 30 else "⚠️ Elevado" if vix > 20 else "😌 Bajo"
    spy_icon  = "📈" if spy_ch >= 0 else "📉"

    lines = []
    if vix:    lines.append(f"VIX: {vix:.1f} {vix_label}")
    if spy_ch: lines.append(f"SPY ayer: {spy_icon} {spy_ch:+.2f}%")
    if eurusd: lines.append(f"EUR/USD: {eurusd:.4f}")
    if tnx:    lines.append(f"Yield 10Y: {tnx:.2f}%")
    return "\n".join(lines) if lines else "No disponible"


# ─── Punto 2: Comparación histórica CVaR ──────────────────────────────────────

def load_cvar_history() -> list:
    history = []
    if os.path.exists(HISTORY_FILE):
        with open(HISTORY_FILE) as f:
            for line in f:
                try: history.append(json.loads(line))
                except: pass
    return history

def save_cvar_history(cvar_pct: float, level: str):
    entry = {
        "date":     datetime.now().strftime("%Y-%m-%d"),
        "cvar_pct": round(cvar_pct, 4),
        "level":    level,
    }
    with open(HISTORY_FILE, "a") as f:
        f.write(json.dumps(entry) + "\n")

def get_cvar_trend(current_cvar: float) -> str:
    history = load_cvar_history()
    if not history:
        return "Sin historial previo"

    yesterday = history[-1]["cvar_pct"] if history else None
    week_ago  = history[-5]["cvar_pct"] if len(history) >= 5 else None

    lines = []
    if yesterday:
        diff  = current_cvar - yesterday
        icon  = "📈" if diff > 0 else "📉"
        lines.append(f"Ayer: {yesterday*100:.2f}% {icon} ({diff*100:+.2f}%)")
    if week_ago:
        diff  = current_cvar - week_ago
        icon  = "📈" if diff > 0 else "📉"
        lines.append(f"Hace 5d: {week_ago*100:.2f}% {icon} ({diff*100:+.2f}%)")

    return "\n".join(lines) if lines else "Sin historial"


# ─── Punto 3: P&L del día anterior ────────────────────────────────────────────

def get_yesterday_pnl(closes, portfolio=None, nav_eur=None) -> dict:
    """P&L estimado del portfolio en el día anterior."""
    port = portfolio or PORTFOLIO
    nav  = nav_eur   or NAV_EUR
    portfolio_tickers = [t for t in port.keys() if t in closes.columns]
    if not portfolio_tickers or len(closes) < 2:
        return {}

    yesterday_ret = {}
    for t in portfolio_tickers:
        series = closes[t].dropna()
        if len(series) >= 2:
            ret = (series.iloc[-1] / series.iloc[-2] - 1) * 100
            yesterday_ret[t] = ret

    # P&L ponderado
    total_w   = sum(port[t] for t in portfolio_tickers)
    port_ret  = sum(yesterday_ret.get(t, 0) * port[t] / total_w
                    for t in portfolio_tickers)
    port_eur  = port_ret / 100 * nav

    # Top movers
    sorted_ret = sorted(yesterday_ret.items(), key=lambda x: abs(x[1]), reverse=True)

    return {
        "pct":         port_ret,
        "eur":         port_eur,
        "top_movers":  sorted_ret[:4],
        "best":        sorted_ret[0] if sorted_ret else None,
        "worst":       sorted_ret[-1] if sorted_ret else None,
    }


def pnl_to_text(pnl: dict) -> str:
    if not pnl:
        return "No disponible"

    pct  = pnl.get("pct", 0)
    eur  = pnl.get("eur", 0)
    icon = "📈" if pct >= 0 else "📉"

    lines = [f"Portfolio ayer: {icon} {pct:+.2f}% (≈€{eur:+.0f})"]

    movers = pnl.get("top_movers", [])
    if movers:
        lines.append("Top movers:")
        for t, r in movers[:3]:
            mv_icon = "🟢" if r >= 0 else "🔴"
            lines.append(f"  {mv_icon} {t}: {r:+.2f}%")

    return "\n".join(lines)


# ─── Punto 4: Earnings de la semana ───────────────────────────────────────────

def get_upcoming_earnings(portfolio=None) -> list:
    """Earnings de tus posiciones en los próximos 7 días via yfinance."""
    import yfinance as yf

    port     = portfolio or PORTFOLIO
    upcoming = []
    today    = datetime.today().date()
    week_out = today + timedelta(days=7)

    for ticker in list(port.keys()):
        try:
            yf_ticker = YF_TICKER_MAP.get(ticker, ticker)
            tk        = yf.Ticker(yf_ticker)
            cal       = tk.calendar

            if cal is not None and hasattr(cal, 'get'):
                earn_dates = cal.get("Earnings Date")
                if earn_dates and len(earn_dates) > 0:
                    earn_date = earn_dates[0]
                    if hasattr(earn_date, 'date'):
                        earn_date = earn_date.date()
                    if today <= earn_date <= week_out:
                        days_left = (earn_date - today).days
                        upcoming.append({
                            "ticker":    ticker,
                            "date":      str(earn_date),
                            "days_left": days_left,
                        })
        except:
            pass

    return sorted(upcoming, key=lambda x: x["days_left"])


def earnings_to_text(earnings: list) -> str:
    if not earnings:
        return "Sin earnings esta semana en tus posiciones"
    lines = []
    for e in earnings:
        days = e["days_left"]
        when = "HOY" if days == 0 else "mañana" if days == 1 else f"en {days}d"
        lines.append(f"  ⚡ {e['ticker']}: {when} ({e['date']})")
    return "\n".join(lines)


# ─── Punto 5: Modo de mercado ──────────────────────────────────────────────────

def get_market_mode(macro: dict, cvar_pct: float, port_ret_30d: float = None) -> dict:
    """
    Determina el modo de mercado basado en múltiples señales.
    Normal / Corrección / Crisis
    """
    vix     = macro.get("vix", 15)
    spy_chg = macro.get("spy_chg", 0)

    # Señales negativas
    signals_crisis     = 0
    signals_correction = 0

    if vix > 30:               signals_crisis += 2
    elif vix > 22:             signals_correction += 1

    if spy_chg < -2:           signals_crisis += 1
    elif spy_chg < -1:         signals_correction += 1

    if cvar_pct > 0.06:        signals_crisis += 2
    elif cvar_pct > 0.04:      signals_correction += 1

    # Determinar modo
    if signals_crisis >= 3:
        mode  = "CRISIS"
        color = "🔴"
        desc  = "Modo tormenta activado. Proteger capital, no abrir nuevas posiciones."
        rebal = "Umbrales rebalanceo: Reducir si caída >20%, Cerrar si caída >30%"
    elif signals_correction >= 2:
        mode  = "CORRECCIÓN"
        color = "🟠"
        desc  = "Mercado en corrección. Entradas solo en soportes clave, tamaños reducidos."
        rebal = "Umbrales rebalanceo: Revisar tesis si caída >15%"
    else:
        mode  = "NORMAL"
        color = "🟢"
        desc  = "Mercado en modo normal. Sistema operando según plan mensual."
        rebal = "Umbrales rebalanceo: Estándar — revisar si drift >5%"

    return {
        "mode":  mode,
        "color": color,
        "desc":  desc,
        "rebal": rebal,
    }


# ─── CVaR cálculo ──────────────────────────────────────────────────────────────

def compute_cvar(returns: np.ndarray) -> dict:
    var      = np.percentile(returns, (1 - CVAR_CONFIDENCE) * 100)
    cvar     = returns[returns <= var].mean()
    vol      = returns.std() * np.sqrt(252)
    sharpe   = (returns.mean() * 252) / (returns.std() * np.sqrt(252) + 1e-10)
    max_dd   = min(returns)
    ret_30d  = returns[-30:].sum() * 100 if len(returns) >= 30 else 0
    return {
        "cvar":     abs(cvar),
        "var":      abs(var),
        "vol":      vol,
        "sharpe":   sharpe,
        "max_dd":   abs(max_dd),
        "ret_30d":  ret_30d,
        "n_days":   len(returns),
    }

def evaluate_risk(metrics: dict, nav_eur=None) -> dict:
    nav      = nav_eur or NAV_EUR
    cvar     = metrics["cvar"]
    cvar_eur = cvar * nav

    if cvar < CVAR_THRESHOLD * 0.5:
        level, color, action, orders = "BAJO",     "🟢", "Sistema nominal. Operar según plan.", "normal"
    elif cvar < CVAR_THRESHOLD:
        level, color, action, orders = "MODERADO", "🟡", "Riesgo elevado. Revisar posiciones beta alto.", "normal"
    elif cvar < CVAR_THRESHOLD * 1.5:
        level, color, action, orders = "ALTO",     "🟠", "CVaR supera umbral. Reducir nuevas entradas al 50%.", "reduce_50"
    else:
        level, color, action, orders = "CRÍTICO",  "🔴", "Pausar todas las órdenes. Revisar stop-losses.", "pause"

    return {
        "level":         level,
        "color":         color,
        "action":        action,
        "orders_action": orders,
        "cvar_pct":      cvar * 100,
        "cvar_eur":      cvar_eur,
        "triggered":     cvar >= CVAR_THRESHOLD,
    }

def save_gate_status(evaluation: dict):
    status = {
        "timestamp":     datetime.now().isoformat(),
        "level":         evaluation["level"],
        "orders_action": evaluation["orders_action"],
        "triggered":     bool(evaluation["triggered"]),
        "cvar_pct":      float(evaluation["cvar_pct"]),
    }
    with open(STATUS_FILE, "w") as f:
        json.dump(status, f, indent=2)

def load_gate_status() -> dict:
    if not os.path.exists(STATUS_FILE):
        return {"orders_action": "normal", "triggered": False, "level": "DESCONOCIDO"}
    with open(STATUS_FILE) as f:
        return json.load(f)


# ─── Scout section (Mejora 1b) ─────────────────────────────────────────────────

def get_scout_section() -> str:
    scout_log = "scout_opportunities.jsonl"
    if not os.path.exists(scout_log):
        return ""

    entries = []
    with open(scout_log) as f:
        for line in f:
            try:
                entries.append(json.loads(line))
            except Exception:
                pass
    if not entries:
        return ""

    latest   = entries[-1]
    age_days = (datetime.now() - datetime.fromisoformat(latest["date"])).days
    if age_days > 7:
        return ""

    try:
        from etoro_client import get_portfolio as _gp
        from config import INSTRUMENT_MAP as _IM
        data = _gp()
        cp   = data.get("clientPortfolio", {})
        in_portfolio = {_IM[p["instrumentID"]]
                        for p in cp.get("positions", [])
                        if p.get("instrumentID") in _IM}
    except Exception:
        in_portfolio = set()

    candidates = []
    for thesis_key, thesis_candidates in latest["results"].items():
        for c in thesis_candidates:
            if c["score"] >= 85 and c["ticker"] not in in_portfolio:
                candidates.append((c["ticker"], c["score"], thesis_key))

    candidates.sort(key=lambda x: x[1], reverse=True)
    if not candidates:
        return ""

    lines = ["🔍 <b>Descubrimientos Scout este ciclo</b>"]
    for ticker, score, thesis in candidates[:5]:
        lines.append(f"  {ticker} ({score}) · {thesis}")
    lines.append(f"<i>Log: {latest['date'][:10]} — actualiza: python3.11 opportunity_scout.py</i>")
    return "\n".join(lines)


# ─── Modo defensivo notice (Mejora 2) ──────────────────────────────────────────

def get_defensive_mode_notice(cvar_pct: float, vix: float) -> str:
    try:
        from opportunity_scout import DEFENSIVE_MODE_OVERRIDE, DEFENSIVE_THRESHOLDS
        if DEFENSIVE_MODE_OVERRIDE is True:
            return "⚠️ <b>Modo defensivo ACTIVO</b> — motivo: override manual"
        if DEFENSIVE_MODE_OVERRIDE is False:
            return ""
        reasons = []
        if cvar_pct > DEFENSIVE_THRESHOLDS["cvar_pct"]:
            reasons.append(f"CVaR {cvar_pct:.1f}%")
        if vix > DEFENSIVE_THRESHOLDS["vix"]:
            reasons.append(f"VIX {vix:.1f}")
        if reasons:
            return f"⚠️ <b>Modo defensivo ACTIVO</b> — motivo: {' y '.join(reasons)}"
    except Exception as e:
        print(f"  ⚠️  Modo defensivo no disponible: {e}")
    return ""


# ─── Resumen ejecutivo ─────────────────────────────────────────────────────────

def build_executive_summary(evaluation: dict, market_mode: dict,
                             pnl: dict, macro: dict) -> str:
    """Una sola frase que resume todo."""
    mode   = market_mode["mode"]
    level  = evaluation["level"]
    pnl_p  = pnl.get("pct", 0)
    vix    = macro.get("vix", 15)

    if level == "CRÍTICO" or mode == "CRISIS":
        return "⛔ No operar hoy. Mercado en modo tormenta, riesgo crítico."
    elif level == "ALTO" and mode == "CORRECCIÓN":
        return "🛑 Sesión defensiva. Reducir exposición, no ampliar posiciones."
    elif level == "ALTO":
        return "⚠️ Operar con cautela. Reducir nuevas entradas al 50%."
    elif pnl_p < -1.5:
        return "📉 Portfolio tuvo mal día ayer. Revisar tesis antes de operar."
    elif level == "BAJO" and mode == "NORMAL" and pnl_p > 0:
        return "✅ Condiciones favorables. Operar según plan mensual."
    else:
        return "🟡 Condiciones mixtas. Seguir plan, sin cambios urgentes."


# ─── Mensaje Telegram completo ─────────────────────────────────────────────────

def build_full_message(metrics: dict, evaluation: dict, macro: dict,
                       pnl: dict, earnings: list, market_mode: dict,
                       cvar_trend: str, portfolio_source: str = "",
                       prediction_section: str = "",
                       smart_money_section: str = "",
                       scout_section: str = "",
                       defensive_notice: str = "") -> str:

    summary  = build_executive_summary(evaluation, market_mode, pnl, macro)
    date_str = datetime.now().strftime("%A %d %b, %H:%M").capitalize()

    # Encabezado
    msg = (
        f"📊 <b>Morning Briefing — {date_str}</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"<b>{summary}</b>\n\n"
    )

    # Modo de mercado
    msg += (
        f"{market_mode['color']} <b>Modo mercado: {market_mode['mode']}</b>\n"
        f"{market_mode['desc']}\n\n"
    )

    # Modo defensivo (Mejora 2)
    if defensive_notice:
        msg += defensive_notice + "\n\n"

    # CVaR
    triggered_str = " ⚠️ GATE ACTIVADO" if evaluation["triggered"] else ""
    msg += (
        f"🛡️ <b>CVaR Gate: {evaluation['level']}{triggered_str}</b>\n"
        f"CVaR-99: {evaluation['cvar_pct']:.2f}% (umbral {CVAR_THRESHOLD*100:.0f}%)\n"
        f"Worst-case día: €{evaluation['cvar_eur']:.0f}\n"
        f"Sharpe: {metrics['sharpe']:.2f} | Vol anual: {metrics['vol']*100:.1f}%\n"
        f"Tendencia:\n{cvar_trend}\n\n"
    )

    # P&L ayer
    msg += f"💰 <b>P&L ayer</b>\n{pnl_to_text(pnl)}\n\n"

    # Macro
    msg += f"🌍 <b>Macro</b>\n{macro_to_text(macro)}\n\n"

    # Prediction Markets
    if prediction_section:
        msg += prediction_section + "\n\n"

    # Scout (Mejora 1b)
    if scout_section:
        msg += scout_section + "\n\n"

    # Smart Money (solo lunes)
    if smart_money_section:
        msg += smart_money_section + "\n\n"

    # Earnings
    msg += f"📅 <b>Earnings próximos 7 días</b>\n{earnings_to_text(earnings)}\n\n"

    # Recomendación del día
    msg += (
        f"🎯 <b>Acción recomendada</b>\n"
        f"{evaluation['action']}\n"
        f"{market_mode['rebal']}\n"
    )

    if portfolio_source:
        msg += f"\n<i>📡 Datos: {portfolio_source}</i>"

    return msg


# ─── Main ───────────────────────────────────────────────────────────────────────

def run_full_briefing(silent: bool = False) -> dict:
    if not silent:
        print(f"\n📊 Morning Briefing | {datetime.now().strftime('%Y-%m-%d %H:%M')}\n")

    try:
        # Portfolio real (con fallback a pesos manuales)
        real_portfolio, real_nav, portfolio_source = build_portfolio_from_etoro()
        portfolio = real_portfolio or PORTFOLIO
        nav_eur   = real_nav       or NAV_EUR
        if not silent:
            print(f"  📡 {portfolio_source}")

        # Datos de mercado
        closes = get_market_data()
        port_ret, ret_df = compute_portfolio_returns(closes, portfolio)

        # CVaR
        metrics    = compute_cvar(port_ret)
        evaluation = evaluate_risk(metrics, nav_eur)
        save_gate_status(evaluation)

        # Historial CVaR
        cvar_trend = get_cvar_trend(evaluation["cvar_pct"] / 100)
        save_cvar_history(evaluation["cvar_pct"] / 100, evaluation["level"])

        # Macro
        macro = get_macro_context(closes)

        # P&L ayer
        pnl = get_yesterday_pnl(closes, portfolio, nav_eur)

        # Earnings
        if not silent:
            print("  Buscando earnings próximos...")
        earnings = get_upcoming_earnings(portfolio)

        # Modo de mercado
        market_mode = get_market_mode(macro, evaluation["cvar_pct"] / 100)

        # Prediction Markets (best-effort, no rompe el briefing si falla)
        if not silent:
            print("  Obteniendo señales prediction markets...")
        prediction_section = get_prediction_section()

        # Smart Money — solo lunes, usa caché 24h
        smart_money_section = ""
        if datetime.now().weekday() == 0:
            if not silent:
                print("  Obteniendo señales smart money (lunes)...")
            smart_money_section = get_smart_money_section_for_briefing()

        # Mostrar en consola
        if not silent:
            print(f"\n  {market_mode['color']} Modo: {market_mode['mode']}")
            print(f"  {evaluation['color']} CVaR: {evaluation['cvar_pct']:.2f}% — {evaluation['level']}")
            if pnl:
                icon = "📈" if pnl.get("pct", 0) >= 0 else "📉"
                print(f"  {icon} P&L ayer: {pnl.get('pct', 0):+.2f}% (≈€{pnl.get('eur', 0):+.0f})")
            if earnings:
                print(f"  ⚡ Earnings esta semana: {', '.join(e['ticker'] for e in earnings)}")
            print(f"\n  🎯 {evaluation['action']}")

        # Scout section (Mejora 1b) y aviso modo defensivo (Mejora 2)
        scout_section    = get_scout_section()
        defensive_notice = get_defensive_mode_notice(
            evaluation["cvar_pct"], macro.get("vix", 0)
        )

        # Enviar Telegram
        msg = build_full_message(metrics, evaluation, macro, pnl,
                                 earnings, market_mode, cvar_trend, portfolio_source,
                                 prediction_section, smart_money_section,
                                 scout_section, defensive_notice)
        sent = send_telegram(msg)

        if not silent:
            status = "✅ enviado" if sent else "❌ falló"
            print(f"\n  📱 Telegram: {status}")

        return evaluation

    except Exception as e:
        error = f"❌ Error: {e}"
        print(f"\n  {error}")
        send_telegram(f"⚠️ <b>Morning Briefing error</b>\n{error}")
        return {"level": "ERROR", "orders_action": "normal", "triggered": False}


def run_monitor_loop():
    import time
    print(f"\n📊 Morning Briefing Monitor | cada hora")
    while True:
        try:
            run_full_briefing(silent=True)
            status = load_gate_status()
            print(f"  [{datetime.now().strftime('%H:%M')}] {status['level']} | {status['orders_action']}")
        except KeyboardInterrupt:
            print("\n⏹️  Monitor parado.")
            break
        except Exception as e:
            print(f"  [ERROR] {e}")
        time.sleep(3600)


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

    parser = argparse.ArgumentParser()
    parser.add_argument("--monitor", action="store_true")
    parser.add_argument("--test",    action="store_true")
    parser.add_argument("--status",  action="store_true")
    args = parser.parse_args()

    if args.test:
        test_telegram()
    elif args.status:
        s = load_gate_status()
        print(f"\n🛡️  Status: {s.get('level')} | {s.get('orders_action')} | CVaR {s.get('cvar_pct',0):.2f}%\n")
    elif args.monitor:
        run_monitor_loop()
    else:
        run_full_briefing()
