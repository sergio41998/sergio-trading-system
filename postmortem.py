"""
Postmortem automático — análisis de calidad de decisiones
Lee executed_orders.jsonl y evalúa proceso vs resultado.

Uso:
  python3.11 postmortem.py
"""

import json
import os
from datetime import datetime, timedelta
import yfinance as yf
from tabulate import tabulate

EXECUTED_FILE = "executed_orders.jsonl"
LOG_FILE      = "trading_decisions.jsonl"  # señales de TradingAgents


def load_executed_orders() -> list:
    if not os.path.exists(EXECUTED_FILE):
        print("No hay órdenes ejecutadas aún.")
        return []
    orders = []
    with open(EXECUTED_FILE) as f:
        for line in f:
            try:
                orders.append(json.loads(line))
            except:
                pass
    return orders


def get_price_after(ticker: str, entry_date: str, days: int) -> float:
    """Obtiene el precio N días después de la entrada."""
    try:
        start = datetime.fromisoformat(entry_date)
        end   = start + timedelta(days=days + 5)
        data  = yf.download(ticker, start=start.strftime("%Y-%m-%d"),
                            end=end.strftime("%Y-%m-%d"), progress=False)
        if len(data) >= days:
            return float(data["Close"].iloc[days])
        elif len(data) > 0:
            return float(data["Close"].iloc[-1])
    except:
        pass
    return 0.0


def analyze_decision_quality(order: dict) -> dict:
    """
    Evalúa la calidad de la decisión distinguiendo proceso de resultado.
    """
    ticker     = order["ticker"]
    entry_date = order["timestamp"][:10]
    entry_price = order["price"]
    action     = order["action"]

    # Precio actual y a 7/30 días
    price_7d  = get_price_after(ticker, entry_date, 7)
    price_30d = get_price_after(ticker, entry_date, 30)

    if entry_price and price_7d:
        ret_7d  = ((price_7d  - entry_price) / entry_price * 100) * (1 if action == "buy" else -1)
        ret_30d = ((price_30d - entry_price) / entry_price * 100) * (1 if action == "buy" else -1) if price_30d else None
    else:
        ret_7d = ret_30d = None

    # Evaluar proceso (independiente del resultado)
    process_score = 0
    process_notes = []

    if order.get("thesis"):
        process_score += 2
        process_notes.append("✅ Tesis documentada")
    else:
        process_notes.append("❌ Sin tesis documentada")

    if order.get("stop_loss_pct"):
        process_score += 2
        process_notes.append("✅ Stop loss definido")
    else:
        process_notes.append("❌ Sin stop loss")

    # La señal de TradingAgents coincidía?
    ta_signal = get_trading_agents_signal(ticker, entry_date)
    if ta_signal == "BUY" and action == "buy":
        process_score += 3
        process_notes.append("✅ Alineado con señal TradingAgents")
    elif ta_signal == "SELL" and action == "buy":
        process_notes.append("⚠️  Contradicción con señal TradingAgents")
    elif ta_signal:
        process_notes.append(f"ℹ️  Señal TradingAgents: {ta_signal}")

    # Clasificación del trade
    if ret_7d is not None:
        outcome = "ganador" if ret_7d > 0 else "perdedor"
    else:
        outcome = "pendiente"

    if process_score >= 5 and outcome == "ganador":
        classification = "✅ Buen proceso, buen resultado"
    elif process_score >= 5 and outcome == "perdedor":
        classification = "⚠️  Buen proceso, mal resultado (mala suerte)"
    elif process_score < 5 and outcome == "ganador":
        classification = "🍀 Mal proceso, buen resultado (buena suerte)"
    elif process_score < 5 and outcome == "perdedor":
        classification = "❌ Mal proceso, mal resultado"
    else:
        classification = "⏳ Pendiente de evaluación"

    return {
        "ticker":         ticker,
        "entry_date":     entry_date,
        "entry_price":    entry_price,
        "ret_7d":         ret_7d,
        "ret_30d":        ret_30d,
        "process_score":  process_score,
        "process_notes":  process_notes,
        "classification": classification,
        "outcome":        outcome,
    }


def get_trading_agents_signal(ticker: str, date: str) -> str:
    """Busca la señal de TradingAgents más cercana a la fecha de entrada."""
    if not os.path.exists(LOG_FILE):
        return None
    with open(LOG_FILE) as f:
        for line in reversed(f.readlines()):
            try:
                entry = json.loads(line)
                if entry.get("ticker") == ticker and entry.get("date", "") <= date:
                    return entry.get("signal")
            except:
                pass
    return None


def run_postmortem():
    print(f"\n📊 POSTMORTEM — Análisis de decisiones")
    print(f"   {datetime.now().strftime('%Y-%m-%d %H:%M')}\n")

    orders = load_executed_orders()
    if not orders:
        print("Sin órdenes ejecutadas para analizar.")
        return

    results = []
    for order in orders:
        print(f"  Analizando {order['ticker']}...")
        analysis = analyze_decision_quality(order)
        results.append(analysis)

    # Tabla resumen
    print(f"\n{'═'*60}")
    print("RESUMEN DE DECISIONES\n")

    rows = []
    for r in results:
        ret7  = f"{r['ret_7d']:.1f}%" if r['ret_7d'] is not None else "pendiente"
        ret30 = f"{r['ret_30d']:.1f}%" if r['ret_30d'] is not None else "pendiente"
        rows.append([
            r["ticker"],
            r["entry_date"],
            f"${r['entry_price']:.2f}",
            ret7,
            ret30,
            f"{r['process_score']}/7",
        ])

    print(tabulate(rows,
                   headers=["Ticker", "Fecha", "Entrada", "Ret 7d", "Ret 30d", "Proceso"],
                   tablefmt="rounded_outline"))

    # Clasificaciones
    print(f"\n{'─'*60}")
    print("CLASIFICACIÓN DE CALIDAD\n")
    for r in results:
        print(f"  {r['ticker']:6} → {r['classification']}")
        for note in r["process_notes"]:
            print(f"           {note}")
        print()

    # Estadísticas globales
    completed = [r for r in results if r["outcome"] != "pendiente"]
    if completed:
        win_rate = len([r for r in completed if r["outcome"] == "ganador"]) / len(completed) * 100
        avg_process = sum(r["process_score"] for r in results) / len(results)
        print(f"{'─'*60}")
        print(f"  Win rate: {win_rate:.0f}% ({len(completed)} trades completados)")
        print(f"  Score de proceso medio: {avg_process:.1f}/7")

        # Lección principal
        bad_process_winners = [r for r in completed if r["process_score"] < 5 and r["outcome"] == "ganador"]
        if bad_process_winners:
            print(f"\n  ⚠️  Atención: {len(bad_process_winners)} trade(s) ganaron con mal proceso.")
            print(f"     No confundas suerte con habilidad.")

    print(f"\n✅ Postmortem completado\n")


if __name__ == "__main__":
    run_postmortem()
