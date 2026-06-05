"""
from earnings_blackout import is_in_blackout, get_blackout_status
Componente 1 + 2: Monitor de precios + Órdenes condicionales
Sistema de decisión humana + ejecución automática para Sergio

Uso:
  python3.11 price_monitor.py

Edita orders.json cada semana con tus niveles de entrada.
El monitor ejecuta automáticamente cuando el precio toca el trigger.
"""

import os
import json
import uuid
import time
import requests
import smtplib
from datetime import datetime
from email.mime.text import MIMEText

# ─── Config ────────────────────────────────────────────────────────────────────

BASE_URL = "https://public-api.etoro.com/api/v1"
MODE     = "real"  # Cambia a "demo" para probar sin riesgo

API_KEY  = os.environ.get("ETORO_API_KEY",  "")
USER_KEY = os.environ.get("ETORO_USER_KEY", "")

# Telegram (opcional) — rellena con tu bot token y chat_id
TELEGRAM_TOKEN   = os.environ.get("TELEGRAM_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")

ORDERS_FILE   = "orders.json"
EXECUTED_FILE = "executed_orders.jsonl"
CHECK_INTERVAL = 60  # segundos entre cada chequeo de precios

from config import INSTRUMENT_MAP
REVERSE_MAP = {v: k for k, v in INSTRUMENT_MAP.items()}


# ─── HTTP helpers ───────────────────────────────────────────────────────────────

def _headers():
    return {
        "x-api-key":    API_KEY,
        "x-user-key":   USER_KEY,
        "x-request-id": str(uuid.uuid4()),
        "Content-Type": "application/json",
    }

def api_get(endpoint, params=None):
    url = f"{BASE_URL}{endpoint}"
    resp = requests.get(url, headers=_headers(), params=params or {}, timeout=15)
    resp.raise_for_status()
    return resp.json()

def api_post(endpoint, payload):
    url = f"{BASE_URL}{endpoint}"
    resp = requests.post(url, headers=_headers(), json=payload, timeout=15)
    resp.raise_for_status()
    return resp.json()


# ─── Componente 1: Monitor de precios ──────────────────────────────────────────

def get_current_prices(instrument_ids: list) -> dict:
    """
    Obtiene precios actuales de múltiples instrumentos.
    Retorna dict {instrumentId: precio_actual}
    Fuente primaria: eToro API (posiciones abiertas).
    Fuente secundaria: yfinance para tickers sin posición aún.
    """
    prices = {}
    try:
        data = api_get(f"/trading/info/{MODE}/pnl")
        cp   = data.get("clientPortfolio", {})

        for p in cp.get("positions", []):
            iid = p.get("instrumentID")
            if iid in instrument_ids:
                close = p.get("unrealizedPnL", {}).get("closeRate", 0)
                prices[iid] = close

        for m in cp.get("mirrors", []):
            for p in m.get("positions", []):
                iid = p.get("instrumentID")
                if iid in instrument_ids:
                    close = p.get("unrealizedPnL", {}).get("closeRate", 0)
                    if close:
                        prices[iid] = close

    except Exception as e:
        print(f"  [ERROR] Precios eToro: {e}")

    # Tickers sin precio desde eToro (posición no abierta aún) → yfinance
    missing_ids = [iid for iid in instrument_ids if not prices.get(iid)]
    if missing_ids:
        missing_map = {iid: INSTRUMENT_MAP[iid] for iid in missing_ids if iid in INSTRUMENT_MAP}
        if missing_map:
            try:
                import yfinance as yf
                yf_symbols = list(missing_map.values())
                raw    = yf.download(yf_symbols, period="1d", progress=False, auto_adjust=True)
                closes = raw["Close"] if "Close" in raw else raw
                for iid, ticker in missing_map.items():
                    try:
                        col   = closes[ticker] if hasattr(closes, "columns") and ticker in closes.columns else closes
                        price = float(col.dropna().iloc[-1])
                        if price:
                            prices[iid] = price
                            print(f"    {ticker}: ${price:.2f} (yfinance — nueva posición)")
                    except Exception:
                        pass
            except Exception as e:
                print(f"  [WARN] yfinance precios: {e}")

    return prices


# ─── Componente 2: Órdenes condicionales ───────────────────────────────────────

def load_orders() -> list:
    """Carga las órdenes condicionales desde orders.json"""
    if not os.path.exists(ORDERS_FILE):
        create_default_orders()
    with open(ORDERS_FILE) as f:
        return json.load(f).get("orders", [])

def create_default_orders():
    """Crea un orders.json de ejemplo con tus posiciones actuales."""
    default = {
        "_instrucciones": "Edita este archivo cada semana con tus niveles. El monitor ejecuta cuando el precio toca el trigger.",
        "_campos": {
            "ticker":        "Símbolo del activo",
            "action":        "buy | sell",
            "trigger_price": "Precio que activa la orden",
            "trigger_type":  "below (compra en caída) | above (compra en ruptura)",
            "amount_eur":    "Importe en EUR a invertir",
            "stop_loss_pct": "Stop loss automático en % (ej: 5 = 5%)",
            "thesis":        "Por qué haces esta operación (para el postmortem)",
            "active":        "true = activa, false = desactivada"
        },
        "orders": [
            {
                "ticker":        "NVDA",
                "action":        "buy",
                "trigger_price": 208.0,
                "trigger_type":  "below",
                "amount_eur":    400,
                "stop_loss_pct": 7,
                "thesis":        "Entrada post-earnings en soporte 10EMA. Tesis AI infrastructure intacta.",
                "active":        True
            },
            {
                "ticker":        "VST",
                "action":        "buy",
                "trigger_price": 58.0,
                "trigger_type":  "below",
                "amount_eur":    300,
                "stop_loss_pct": 8,
                "thesis":        "Ampliar posición en soporte. Tesis energía para AI datacenters intacta.",
                "active":        True
            },
            {
                "ticker":        "PLTR",
                "action":        "buy",
                "trigger_price": 115.0,
                "trigger_type":  "below",
                "amount_eur":    300,
                "stop_loss_pct": 8,
                "thesis":        "Tercer tramo en consolidación. Tesis gobierno + enterprise AI.",
                "active":        False
            }
        ]
    }
    with open(ORDERS_FILE, "w") as f:
        json.dump(default, f, indent=2, ensure_ascii=False)
    print(f"✅ Creado {ORDERS_FILE} — edítalo con tus niveles de entrada.")

def check_trigger(order: dict, current_price: float) -> bool:
    """Comprueba si el precio ha tocado el nivel de trigger."""
    trigger = order.get("trigger_price", 0)
    ttype   = order.get("trigger_type", "below")
    if ttype == "below":
        return current_price <= trigger
    elif ttype == "above":
        return current_price >= trigger
    return False


# ─── Componente 3: Motor de ejecución ──────────────────────────────────────────

def execute_order(order: dict, current_price: float) -> dict:
    """
    Ejecuta una orden en eToro API.
    En MODE=demo simula la ejecución sin capital real.
    """
    ticker      = order["ticker"]
    instrument_id = REVERSE_MAP.get(ticker)
    amount_eur  = order["amount_eur"]
    stop_pct    = order.get("stop_loss_pct", 5) / 100
    action      = order["action"]

    if not instrument_id:
        return {"error": f"Ticker {ticker} no encontrado en mapa de instrumentos"}

    # Convertir EUR a USD con tipo de cambio real
    from etoro_client import get_eur_usd_rate
    amount_usd = amount_eur * get_eur_usd_rate()

    if MODE == "demo":
        result = {
            "simulated":   True,
            "ticker":      ticker,
            "action":      action,
            "amount_eur":  amount_eur,
            "price":       current_price,
            "timestamp":   datetime.now().isoformat(),
        }
        print(f"  [DEMO] Simulando {action.upper()} {ticker} €{amount_eur} @ ${current_price:.2f}")
        return result

    # Ejecución real
    is_buy  = action == "buy"
    payload = {
        "InstrumentID":  instrument_id,
        "IsBuy":         is_buy,
        "Leverage":      1,
        "Amount":        amount_usd,
        "IsNoStopLoss":  False,
        "StopLossRate":  current_price * (1 - stop_pct) if is_buy else current_price * (1 + stop_pct),
        "IsNoTakeProfit": True,
    }

    try:
        result = api_post(f"/trading/execution/{MODE}/market-open-orders", payload)
        result["ticker"]     = ticker
        result["amount_eur"] = amount_eur
        result["price"]      = current_price
        result["timestamp"]  = datetime.now().isoformat()
        return result
    except Exception as e:
        return {"error": str(e), "ticker": ticker}

def log_execution(order: dict, result: dict, current_price: float):
    """Guarda la ejecución en el log para postmortem."""
    entry = {
        "timestamp":   datetime.now().isoformat(),
        "ticker":      order["ticker"],
        "action":      order["action"],
        "trigger":     order["trigger_price"],
        "price":       current_price,
        "amount_eur":  order["amount_eur"],
        "thesis":      order.get("thesis", ""),
        "result":      result,
        "mode":        MODE,
    }
    with open(EXECUTED_FILE, "a") as f:
        f.write(json.dumps(entry) + "\n")


# ─── Componente 4: Notificaciones ──────────────────────────────────────────────

def send_telegram(message: str):
    """Envía notificación por Telegram."""
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        return
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        requests.post(url, json={
            "chat_id": TELEGRAM_CHAT_ID,
            "text":    message,
            "parse_mode": "HTML"
        }, timeout=10)
    except Exception as e:
        print(f"  [WARN] Telegram: {e}")

def notify_execution(order: dict, result: dict, current_price: float):
    """Notifica ejecución de orden."""
    ticker  = order["ticker"]
    action  = order["action"].upper()
    amount  = order["amount_eur"]
    trigger = order["trigger_price"]
    thesis  = order.get("thesis", "")

    msg = (
        f"🤖 <b>ORDEN EJECUTADA</b>\n"
        f"━━━━━━━━━━━━━━━━━\n"
        f"📌 {action} {ticker}\n"
        f"💰 Importe: €{amount}\n"
        f"🎯 Trigger: ${trigger:.2f}\n"
        f"📊 Precio ejecución: ${current_price:.2f}\n"
        f"📝 Tesis: {thesis}\n"
        f"🕐 {datetime.now().strftime('%Y-%m-%d %H:%M')}\n"
        f"{'[DEMO]' if MODE == 'demo' else '[REAL]'}"
    )
    print(f"\n{'═'*45}")
    print(msg.replace("<b>", "").replace("</b>", "").replace("━", "─"))
    print(f"{'═'*45}\n")
    send_telegram(msg)

def notify_alert(ticker: str, message: str):
    """Envía alerta sin ejecutar orden."""
    msg = f"⚠️ <b>ALERTA {ticker}</b>\n{message}\n🕐 {datetime.now().strftime('%H:%M')}"
    print(f"\n⚠️  ALERTA {ticker}: {message}")
    send_telegram(msg)


# ─── Devil's Advocate — mejora de decisión humana ──────────────────────────────

def devil_advocate_check(order: dict) -> bool:
    """
    Antes de ejecutar, muestra los argumentos en contra.
    El usuario debe confirmar manualmente (solo en modo interactivo).
    En producción automatizada se salta este paso.
    """
    ticker = order["ticker"]
    print(f"\n🔴 DEVIL'S ADVOCATE — {ticker}")
    print("─" * 40)
    print("Antes de ejecutar, considera:")
    print(f"  1. ¿El mercado bajista podría llevar {ticker} otro 10-15% más abajo?")
    print(f"  2. ¿La tesis original sigue válida o el sector ha cambiado?")
    print(f"  3. ¿Tienes suficiente liquidez para aguantar drawdown sin vender?")
    print(f"\n  Tesis original: {order.get('thesis', 'No especificada')}")
    print("─" * 40)
    return True  # En automático siempre procede — el check es informativo


# ─── Loop principal ─────────────────────────────────────────────────────────────

def run_monitor():
    print(f"\n🤖 Price Monitor | Modo: {MODE.upper()} | {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print(f"   Intervalo: {CHECK_INTERVAL}s | Log: {EXECUTED_FILE}\n")

    # Crear orders.json si no existe
    if not os.path.exists(ORDERS_FILE):
        create_default_orders()
        print(f"📝 Edita {ORDERS_FILE} con tus niveles y relanza el monitor.\n")
        return

    executed_this_session = set()

    while True:
        try:
            orders = [o for o in load_orders() if o.get("active")]
            if not orders:
                print(f"  [{datetime.now().strftime('%H:%M')}] Sin órdenes activas.")
                time.sleep(CHECK_INTERVAL)
                continue

            # Obtener IDs de instrumentos necesarios
            needed_ids = [REVERSE_MAP.get(o["ticker"]) for o in orders if REVERSE_MAP.get(o["ticker"])]
            prices     = get_current_prices(needed_ids)

            print(f"  [{datetime.now().strftime('%H:%M')}] Chequeando {len(orders)} órdenes activas...")

            for order in orders:
                ticker        = order["ticker"]
                instrument_id = REVERSE_MAP.get(ticker)
                order_key     = f"{ticker}_{order['trigger_price']}"

                if order_key in executed_this_session:
                    continue

                # Earnings blackout: saltar órdenes cerca de earnings
                if is_in_blackout(ticker):
                    status = get_blackout_status(ticker)
                    print(f"    {ticker}: 🔴 BLACKOUT — {status['reason']} (orden saltada)")
                    continue

                current_price = prices.get(instrument_id)
                if not current_price:
                    continue

                trigger = order["trigger_price"]
                ttype   = order.get("trigger_type", "below")
                symbol  = "≤" if ttype == "below" else "≥"
                print(f"    {ticker}: ${current_price:.2f} (trigger {symbol}${trigger:.2f})")

                if check_trigger(order, current_price):
                    devil_advocate_check(order)
                    result = execute_order(order, current_price)

                    if "error" not in result:
                        log_execution(order, result, current_price)
                        notify_execution(order, result, current_price)
                        executed_this_session.add(order_key)

                        # Desactivar la orden después de ejecutar
                        all_orders = load_orders()
                        for o in all_orders:
                            if o["ticker"] == ticker and o["trigger_price"] == trigger:
                                o["active"] = False
                        with open(ORDERS_FILE) as f:
                            data = json.load(f)
                        data["orders"] = all_orders
                        with open(ORDERS_FILE, "w") as f:
                            json.dump(data, f, indent=2, ensure_ascii=False)
                    else:
                        notify_alert(ticker, f"Error al ejecutar: {result['error']}")

        except KeyboardInterrupt:
            print("\n\n⏹️  Monitor parado.")
            break
        except Exception as e:
            print(f"  [ERROR] {e}")

        time.sleep(CHECK_INTERVAL)


if __name__ == "__main__":
    run_monitor()
