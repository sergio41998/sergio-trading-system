"""
Investment Advisor — Recomendación de inversión
Dado un importe, decide dónde invertirlo con máxima convicción.

Lógica:
1. Revisa si hay posiciones perdedoras que cerrar primero
2. Lee señales de TradingAgents (trading_decisions.jsonl)
3. Consulta el CVaR Gate (no invierte si riesgo es CRÍTICO)
4. Recomienda UNA sola posición con el importe completo
5. Te manda la recomendación por Telegram

Uso:
  python3.11 invest_advisor.py --amount 1000
  python3.11 invest_advisor.py --amount 500
"""

import os
import json
import argparse
import requests
from datetime import datetime, timedelta
from tabulate import tabulate

# ─── Config ────────────────────────────────────────────────────────────────────

TELEGRAM_TOKEN   = os.environ.get("TELEGRAM_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")

DECISIONS_LOG = "trading_decisions.jsonl"
CVAR_STATUS   = "cvar_status.json"

# Umbrales de decisión
MIN_SIGNAL_CONFIDENCE = "MEDIUM"   # mínimo MEDIUM para recomendar
LOSS_THRESHOLD_PCT    = -15.0      # cerrar si pérdida > 15% Y tesis rota
STOP_LOSS_PCT         = 7          # stop loss por defecto en nuevas entradas

# Tu portfolio actual con P&L conocido (actualiza mensualmente)
# Formato: ticker → (invertido_eur, pl_pct, tesis_intacta)
CURRENT_POSITIONS = {
    "VRT":   (412,  106.3,  True),
    "ASML":  (925,  43.4,   True),
    "UCTT":  (119,  226.6,  True),
    "AMD":   (159,  153.8,  True),
    "TSM":   (1599, 14.5,   True),
    "RHM":   (1171, -14.7,  True),   # en pérdidas pero tesis intacta (defensa EU)
    "SMFG":  (394,  38.3,   True),
    "NVDA":  (1782, 16.0,   True),   # múltiples tramos
    "PANW":  (475,  33.5,   True),
    "AVGO":  (157,  127.9,  True),
    "VST":   (861,  -10.1,  True),   # en pérdidas, tesis intacta (AI energy)
    "CRWD":  (506,  33.7,   True),
    "PLTR":  (509,  45.0,   True),   # múltiples tramos
    "GOOG":  (271,  279.3,  True),
    "KO":    (283,  17.5,   True),
    "UNH":   (231,  10.7,   True),
    "RTX":   (424,  19.5,   True),
    "MSFT":  (240,  -14.4,  True),
    "BWXT":  (152,  32.9,   True),
    "OKLO":  (15,   168.0,  True),
    "ERJ":   (29,   96.1,   True),
    "AIR":   (102,  27.8,   True),
    "GILD":  (119,  7.7,    True),
    "NOC":   (110,  4.4,    True),
    "CABK":  (60,   112.7,  True),
    "IBE":   (81,   45.3,   True),
}

# Tickers candidatos a nueva posición (no están en portfolio)
NEW_POSITION_CANDIDATES = ["AXON", "CORZ", "IREN", "BE", "LITE"]


# ─── Telegram ──────────────────────────────────────────────────────────────────

def send_telegram(message: str):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        return
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        requests.post(url, json={
            "chat_id":    TELEGRAM_CHAT_ID,
            "text":       message,
            "parse_mode": "HTML"
        }, timeout=10)
    except:
        pass


# ─── Leer señales TradingAgents ────────────────────────────────────────────────

def load_latest_signals() -> dict:
    """
    Lee las señales más recientes de trading_decisions.jsonl.
    Retorna dict {ticker: {signal, confidence, reasoning, date}}
    """
    signals = {}
    if not os.path.exists(DECISIONS_LOG):
        return signals

    # Leer todas las entradas y quedarse con la más reciente por ticker
    with open(DECISIONS_LOG) as f:
        for line in f:
            try:
                entry = json.loads(line)
                ticker = entry.get("ticker")
                if ticker:
                    # Sobrescribe con la entrada más reciente
                    signals[ticker] = entry
            except:
                pass
    return signals


def get_signal_score(signal: str, confidence: str) -> int:
    """Convierte señal y confianza a score numérico."""
    signal_scores     = {"BUY": 3, "HOLD": 1, "SELL": -2, "ERROR": 0}
    confidence_scores = {"HIGH": 2, "MEDIUM": 1, "LOW": 0, "N/A": 0}
    return signal_scores.get(signal, 0) + confidence_scores.get(confidence, 0)


# ─── CVaR Gate status ──────────────────────────────────────────────────────────

def get_cvar_status() -> dict:
    if not os.path.exists(CVAR_STATUS):
        return {"level": "DESCONOCIDO", "triggered": False, "orders_action": "normal"}
    with open(CVAR_STATUS) as f:
        return json.load(f)


# ─── Análisis de posiciones perdedoras ─────────────────────────────────────────

def find_positions_to_close(signals: dict) -> list:
    """
    Identifica posiciones candidatas a cerrar.
    Criterio: pérdida > umbral Y señal SELL o tesis rota.
    """
    to_close = []
    for ticker, (invested, pl_pct, tesis_intacta) in CURRENT_POSITIONS.items():
        signal_data = signals.get(ticker, {})
        signal      = signal_data.get("signal", "HOLD")
        confidence  = signal_data.get("confidence", "LOW")

        # Candidata a cierre si:
        # 1. Pérdida significativa Y señal SELL
        # 2. O tesis explícitamente rota
        should_close = False
        reason       = ""

        if pl_pct < LOSS_THRESHOLD_PCT and signal == "SELL":
            should_close = True
            reason = f"Pérdida {pl_pct:.1f}% + señal SELL"
        elif not tesis_intacta and pl_pct < 0:
            should_close = True
            reason = f"Tesis rota + pérdida {pl_pct:.1f}%"
        elif pl_pct < -20 and signal == "SELL" and confidence == "HIGH":
            should_close = True
            reason = f"Pérdida severa {pl_pct:.1f}% + SELL HIGH confidence"

        if should_close:
            capital_recovered = invested * (1 + pl_pct / 100)
            to_close.append({
                "ticker":            ticker,
                "pl_pct":            pl_pct,
                "invested":          invested,
                "capital_recovered": capital_recovered,
                "loss_eur":          invested - capital_recovered,
                "reason":            reason,
            })

    return sorted(to_close, key=lambda x: x["pl_pct"])


# ─── Selección de mejor oportunidad ────────────────────────────────────────────

def find_best_opportunity(signals: dict, amount_eur: float, cvar_status: dict) -> dict:
    """
    Encuentra la mejor oportunidad de inversión para el importe dado.
    Combina señales de TradingAgents con análisis del portfolio actual.
    """
    candidates = []

    # Candidatos 1: Ampliar posiciones existentes con señal BUY
    for ticker, (invested, pl_pct, tesis_intacta) in CURRENT_POSITIONS.items():
        signal_data = signals.get(ticker, {})
        signal      = signal_data.get("signal", "HOLD")
        confidence  = signal_data.get("confidence", "LOW")
        reasoning   = signal_data.get("reasoning", "")
        score       = get_signal_score(signal, confidence)

        if signal == "BUY" and confidence in ["HIGH", "MEDIUM"] and tesis_intacta:
            # Bonus si está en pérdidas (ampliar en debilidad)
            if pl_pct < 0:
                score += 1
                type_label = "AMPLIAR (en pérdidas — dollar-cost average)"
            elif pl_pct < 20:
                type_label = "AMPLIAR (posición pequeña)"
            else:
                type_label = "AMPLIAR (posición ganadora)"

            candidates.append({
                "ticker":      ticker,
                "type":        "ampliar",
                "type_label":  type_label,
                "signal":      signal,
                "confidence":  confidence,
                "score":       score,
                "pl_pct":      pl_pct,
                "invested":    invested,
                "reasoning":   reasoning[:150],
                "amount_eur":  amount_eur,
            })

    # Candidatos 2: Nueva posición con señal BUY
    for ticker in NEW_POSITION_CANDIDATES:
        signal_data = signals.get(ticker, {})
        signal      = signal_data.get("signal", "HOLD")
        confidence  = signal_data.get("confidence", "LOW")
        reasoning   = signal_data.get("reasoning", "")
        score       = get_signal_score(signal, confidence)

        if signal == "BUY" and confidence in ["HIGH", "MEDIUM"]:
            candidates.append({
                "ticker":      ticker,
                "type":        "nueva",
                "type_label":  "NUEVA POSICIÓN",
                "signal":      signal,
                "confidence":  confidence,
                "score":       score,
                "pl_pct":      0,
                "invested":    0,
                "reasoning":   reasoning[:150],
                "amount_eur":  amount_eur,
            })

    # Si CVaR está en ALTO o CRÍTICO, reducir importe
    cvar_level = cvar_status.get("level", "BAJO")
    amount_adjusted = amount_eur
    cvar_note = ""

    if cvar_level == "ALTO":
        amount_adjusted = amount_eur * 0.5
        cvar_note = f"⚠️ CVaR ALTO — importe reducido al 50% (€{amount_adjusted:.0f})"
    elif cvar_level == "CRÍTICO":
        amount_adjusted = 0
        cvar_note = "🔴 CVaR CRÍTICO — no se recomienda invertir ahora"

    if not candidates:
        return {
            "found":          False,
            "cvar_note":      cvar_note,
            "amount_adjusted": amount_adjusted,
        }

    # Ordenar por score y seleccionar el mejor
    candidates.sort(key=lambda x: x["score"], reverse=True)
    best = candidates[0]
    best["amount_adjusted"] = amount_adjusted
    best["cvar_note"]       = cvar_note
    best["found"]           = True
    best["all_candidates"]  = candidates[:3]  # top 3 para contexto

    return best


# ─── Generar recomendación ─────────────────────────────────────────────────────

def generate_recommendation(amount_eur: float) -> dict:
    """
    Genera la recomendación completa de inversión.
    """
    print(f"\n💼 Investment Advisor | €{amount_eur:.0f} disponibles")
    print(f"   {datetime.now().strftime('%Y-%m-%d %H:%M')}\n")

    # 1. Cargar señales
    signals = load_latest_signals()
    if not signals:
        print("  ⚠️  Sin señales de TradingAgents. Ejecuta primero trading_agents_etoro.py")
        print("  Usando análisis básico del portfolio...")

    # 2. CVaR status
    cvar_status = get_cvar_status()
    print(f"  🛡️  CVaR Gate: {cvar_status.get('level', 'DESCONOCIDO')}")

    # 3. Posiciones a cerrar
    to_close = find_positions_to_close(signals)
    if to_close:
        print(f"\n  🔴 Posiciones candidatas a cerrar ({len(to_close)}):")
        for p in to_close:
            print(f"    {p['ticker']:6} | {p['pl_pct']:.1f}% | {p['reason']}")
            print(f"           Recuperarías: €{p['capital_recovered']:.0f} (pérdida: €{p['loss_eur']:.0f})")

    # 4. Mejor oportunidad
    opportunity = find_best_opportunity(signals, amount_eur, cvar_status)

    return {
        "amount_original": amount_eur,
        "signals":         signals,
        "cvar_status":     cvar_status,
        "to_close":        to_close,
        "opportunity":     opportunity,
    }


# ─── Display y Telegram ────────────────────────────────────────────────────────

def display_and_notify(rec: dict):
    """Muestra la recomendación y la envía por Telegram."""
    amount    = rec["amount_original"]
    opp       = rec["opportunity"]
    to_close  = rec["to_close"]
    cvar      = rec["cvar_status"]

    print(f"\n{'═'*55}")
    print(f"  RECOMENDACIÓN PARA €{amount:.0f}")
    print(f"{'═'*55}")

    # Paso 1: ¿cerrar algo primero?
    close_section = ""
    if to_close:
        print(f"\n  PASO 1 — ANTES DE INVERTIR, CONSIDERA CERRAR:")
        rows = []
        for p in to_close:
            rows.append([p["ticker"], f"{p['pl_pct']:.1f}%",
                        f"€{p['capital_recovered']:.0f}", p["reason"]])
        print(tabulate(rows,
                      headers=["Ticker", "P&L", "Recuperas", "Razón"],
                      tablefmt="rounded_outline"))
        close_section = "\n🔴 <b>ANTES DE INVERTIR — CONSIDERA CERRAR:</b>\n"
        for p in to_close:
            close_section += f"  • {p['ticker']}: {p['pl_pct']:.1f}% | {p['reason']}\n"
    else:
        print(f"\n  ✅ Sin posiciones candidatas a cerrar")

    # Paso 2: ¿dónde invertir?
    if not opp.get("found"):
        print(f"\n  PASO 2 — INVERSIÓN:")
        if opp.get("cvar_note"):
            print(f"  {opp['cvar_note']}")
        else:
            print(f"  Sin señales BUY claras ahora mismo.")
            print(f"  Recomendación: mantener el capital en cash hasta el próximo análisis.")

        msg = (
            f"💼 <b>Investment Advisor — €{amount:.0f}</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"{close_section}\n"
            f"💰 Sin señales BUY claras — mantener cash\n"
            f"{opp.get('cvar_note', '')}\n"
            f"🕐 {datetime.now().strftime('%Y-%m-%d %H:%M')}"
        )
        send_telegram(msg)
        return

    ticker      = opp["ticker"]
    type_label  = opp["type_label"]
    signal      = opp["signal"]
    confidence  = opp["confidence"]
    reasoning   = opp["reasoning"]
    amount_adj  = opp["amount_adjusted"]
    cvar_note   = opp.get("cvar_note", "")
    pl_pct      = opp.get("pl_pct", 0)

    conf_icons = {"HIGH": "●●●", "MEDIUM": "●●○", "LOW": "●○○"}
    conf_icon  = conf_icons.get(confidence, "?")

    print(f"\n  PASO 2 — INVERTIR:")
    print(f"\n  {'─'*50}")
    print(f"  🎯 {ticker} — {type_label}")
    print(f"  Señal: {signal} | Confianza: {conf_icon} {confidence}")
    if pl_pct != 0:
        print(f"  Posición actual: {pl_pct:+.1f}%")
    print(f"  Importe recomendado: €{amount_adj:.0f}")
    if amount_adj != amount:
        print(f"  (reducido de €{amount:.0f} por CVaR {cvar['level']})")
    print(f"  Stop loss: {STOP_LOSS_PCT}% automático")
    print(f"\n  Razonamiento: {reasoning}")
    if cvar_note:
        print(f"\n  {cvar_note}")

    # Top 3 alternativas
    alternatives = opp.get("all_candidates", [])[1:3]
    if alternatives:
        print(f"\n  Alternativas consideradas:")
        for alt in alternatives:
            print(f"    • {alt['ticker']:6} | {alt['signal']:5} {alt['confidence']:6} | {alt['type_label']}")

    print(f"\n{'═'*55}")

    # Telegram
    alt_str = ""
    for alt in alternatives:
        alt_str += f"  • {alt['ticker']}: {alt['signal']} {alt['confidence']}\n"

    msg = (
        f"💼 <b>Investment Advisor — €{amount:.0f}</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"{close_section}\n"
        f"🎯 <b>RECOMENDACIÓN: {ticker}</b>\n"
        f"Acción: {type_label}\n"
        f"Señal: {signal} | Confianza: {confidence}\n"
        f"Importe: <b>€{amount_adj:.0f}</b>\n"
        f"Stop loss: {STOP_LOSS_PCT}%\n\n"
        f"📝 {reasoning}\n\n"
        f"{'⚠️ ' + cvar_note if cvar_note else ''}\n"
        f"📊 Alternativas:\n{alt_str}"
        f"\n🕐 {datetime.now().strftime('%Y-%m-%d %H:%M')}"
    )
    send_telegram(msg)
    print(f"  📱 Recomendación enviada por Telegram")


# ─── Main ───────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Investment Advisor")
    parser.add_argument("--amount", type=float, required=True,
                       help="Importe a invertir en EUR (ej: --amount 1000)")
    args = parser.parse_args()

    # Cargar .env
    env_file = os.path.join(os.path.dirname(__file__), ".env")
    if os.path.exists(env_file):
        with open(env_file) as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    key, val = line.split("=", 1)
                    os.environ.setdefault(key.strip(), val.strip())

    rec = generate_recommendation(args.amount)
    display_and_notify(rec)
