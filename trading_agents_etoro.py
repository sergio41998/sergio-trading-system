"""
TradingAgents + eToro — Fase 2
Análisis multi-agente LLM sobre las posiciones de Sergio.

Instalación:
  pip install tradingagents-framework anthropic requests tabulate

Uso:
  export ETORO_API_KEY="..."
  export ETORO_USER_KEY="..."
  export ANTHROPIC_API_KEY="..."
  python trading_agents_etoro.py

Paper trading: los agentes generan señales pero NO ejecutan nada.
Log persistente en: trading_decisions.jsonl
"""

import os
import json
import uuid
from datetime import datetime, date

# ─── eToro client (importado desde Fase 1) ──────────────────────────────────────
# Asegúrate de tener etoro_client.py en el mismo directorio
from etoro_client import get_portfolio, get_account_balance, search_instrument

# ─── TradingAgents ──────────────────────────────────────────────────────────────
try:
    from tradingagents.graph.trading_graph import TradingAgentsGraph
    from tradingagents.default_config import DEFAULT_CONFIG
    AGENTS_AVAILABLE = True
except ImportError:
    AGENTS_AVAILABLE = True
    print("⚠️  TradingAgents no instalado. Ejecuta: pip install tradingagents-framework")

# ─── Config ────────────────────────────────────────────────────────────────────

# Tu cartera high-conviction en eToro
PORTFOLIO_TICKERS = [
    "NVDA", "PLTR", "ASML", "TSM", "MSFT",
    "GOOG", "AMZN", "CVX", "SMCI"
]

# Tickers candidatos a nuevas posiciones (próximas entradas según análisis previo)
WATCHLIST_TICKERS = ["MU", "DDOG", "FSLR", "ASTS", "AXON"]

LOG_FILE = "trading_decisions.jsonl"


# ─── TradingAgents config ───────────────────────────────────────────────────────

def build_agent_config() -> dict:
    config = DEFAULT_CONFIG.copy()
    config["llm_provider"]    = "anthropic"
    config["deep_think_llm"]  = "claude-sonnet-4-6"
    config["quick_think_llm"] = "claude-sonnet-4-6"
    config["max_debate_rounds"] = 1     # 1 ronda = más rápido y barato
    config["checkpoint_enabled"] = True  # Aprende de decisiones pasadas
    return config


# ─── Signal generator ───────────────────────────────────────────────────────────

def analyze_ticker(ticker: str, graph: "TradingAgentsGraph") -> dict:
    """
    Lanza el multi-agente sobre un ticker y devuelve la señal estructurada.
    Retorna: {ticker, date, signal, confidence, reasoning, raw}
    """
    analysis_date = date.today().isoformat()
    print(f"  🔍 Analizando {ticker} ({analysis_date})...")

    try:
        state, decision = graph.propagate(ticker, analysis_date)
        signal = extract_signal(decision)
        return {
            "ticker":     ticker,
            "date":       analysis_date,
            "signal":     signal["action"],        # BUY | SELL | HOLD
            "confidence": signal["confidence"],    # HIGH | MEDIUM | LOW
            "reasoning":  signal["reasoning"],
            "raw":        str(decision)[:500],
        }
    except Exception as e:
        return {
            "ticker":     ticker,
            "date":       analysis_date,
            "signal":     "ERROR",
            "confidence": "N/A",
            "reasoning":  str(e),
            "raw":        "",
        }


def extract_signal(decision_text: str) -> dict:
    """
    Extrae señal estructurada del output de TradingAgents.
    TradingAgents devuelve texto con BUY/SELL/HOLD y razonamiento.
    """
    text = str(decision_text).upper()

    if "BUY" in text or "STRONG BUY" in text:
        action = "BUY"
    elif "SELL" in text or "STRONG SELL" in text:
        action = "SELL"
    else:
        action = "HOLD"

    if "HIGH CONFIDENCE" in text or "STRONG" in text:
        confidence = "HIGH"
    elif "LOW CONFIDENCE" in text or "UNCERTAIN" in text:
        confidence = "LOW"
    else:
        confidence = "MEDIUM"

    # Extrae primeras 300 chars del razonamiento
    lines = str(decision_text).split("\n")
    reasoning = " ".join(l.strip() for l in lines[:5] if l.strip())[:300]

    return {"action": action, "confidence": confidence, "reasoning": reasoning}


# ─── Logging ────────────────────────────────────────────────────────────────────

def log_decision(result: dict):
    """Append a JSONL log para backtesting posterior."""
    with open(LOG_FILE, "a") as f:
        f.write(json.dumps(result) + "\n")


def print_signal(r: dict):
    icons = {"BUY": "🟢", "SELL": "🔴", "HOLD": "⚪", "ERROR": "⚠️"}
    conf_icons = {"HIGH": "●●●", "MEDIUM": "●●○", "LOW": "●○○", "N/A": "---"}
    icon = icons.get(r["signal"], "?")
    conf = conf_icons.get(r["confidence"], "?")
    print(f"\n  {icon} {r['ticker']:6} | {r['signal']:5} | {conf} | {r['reasoning'][:80]}...")


# ─── Portfolio reconciliation ────────────────────────────────────────────────────

def reconcile_with_portfolio(signals: list, portfolio: dict) -> list:
    """
    Cruza señales con posiciones reales de eToro.
    Añade contexto: ¿ya tengo posición? ¿qué tamaño?
    """
    positions = {p.get("instrumentTicker"): p for p in portfolio.get("positions", [])}

    enriched = []
    for s in signals:
        ticker = s["ticker"]
        pos = positions.get(ticker)
        s["has_position"]  = pos is not None
        s["invested_eur"]  = pos.get("invested", 0) if pos else 0
        s["pl_pct"]        = pos.get("netProfitPercent", 0) if pos else None

        # Lógica de recomendación cruzada
        if s["signal"] == "BUY" and not s["has_position"]:
            s["action"] = "ABRIR POSICIÓN"
        elif s["signal"] == "BUY" and s["has_position"]:
            s["action"] = "MANTENER / AMPLIAR"
        elif s["signal"] == "SELL" and s["has_position"]:
            s["action"] = "CONSIDERAR CIERRE"
        elif s["signal"] == "HOLD":
            s["action"] = "MANTENER"
        else:
            s["action"] = "MONITOREAR"

        enriched.append(s)

    return enriched


# ─── Main ───────────────────────────────────────────────────────────────────────

def main():
    print(f"\n🤖 TradingAgents + eToro | {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print(f"   Tickers a analizar: {PORTFOLIO_TICKERS + WATCHLIST_TICKERS}\n")

    if not AGENTS_AVAILABLE:
        print("Instala TradingAgents primero:\n  pip install tradingagents-framework")
        return

    # Inicializar grafo de agentes
    config = build_agent_config()
    graph  = TradingAgentsGraph(debug=False, config=config)
    print("✅ TradingAgents inicializado con Claude Sonnet 4\n")

    # Leer portfolio actual de eToro
    try:
        portfolio = get_portfolio()
        account   = get_account_balance()
        equity    = account.get("equity", 0)
        print(f"📊 Portfolio eToro cargado | Equity: €{equity:,.2f}\n")
    except Exception as e:
        print(f"⚠️  No se pudo conectar a eToro API: {e}")
        portfolio = {"positions": []}

    # Analizar todos los tickers
    all_tickers = PORTFOLIO_TICKERS + WATCHLIST_TICKERS
    signals = []

    print("─" * 60)
    print("Análisis en curso (puede tardar 2-3 min por ticker)...\n")

    for ticker in all_tickers:
        result = analyze_ticker(ticker, graph)
        signals.append(result)
        log_decision(result)
        print_signal(result)

    # Reconciliar con portfolio real
    enriched = reconcile_with_portfolio(signals, portfolio)

    # Resumen final
    print("\n" + "═" * 60)
    print("SEÑALES FINALES\n")
    for r in sorted(enriched, key=lambda x: x["signal"]):
        pos_info = f"€{r['invested_eur']:.0f} ({r['pl_pct']:.1f}%)" if r["has_position"] else "sin posición"
        print(f"  {r['ticker']:8} → {r['action']:25} | {pos_info}")

    print(f"\n📝 Log guardado en: {LOG_FILE}")
    print(f"   {len(signals)} análisis completados\n")
    print("✅ Fase 2 OK. Siguiente: Agent Portfolio real (Fase 3)\n")


if __name__ == "__main__":
    main()
