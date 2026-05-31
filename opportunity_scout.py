"""
Opportunity Scout — Buscador de nuevas ideas de inversión
Busca activamente acciones alineadas con las tesis de Sergio.

Qué hace:
1. Para cada tesis del portfolio, busca candidatos relacionados
2. Filtra por criterios de calidad (liquidez, momentum, valoración)
3. Analiza los mejores con yfinance
4. Envía el top 3-5 por Telegram con razonamiento
5. Genera señales para añadir a trading_agents_etoro.py

Uso:
  python3.11 opportunity_scout.py              # análisis completo
  python3.11 opportunity_scout.py --thesis ai  # solo tesis AI
  python3.11 opportunity_scout.py --quick      # top 3 sin análisis profundo

Coste: ~€0 (sin LLM calls, solo yfinance)
"""

import os
import json
import argparse
import requests
import numpy as np
from datetime import datetime, timedelta
from tabulate import tabulate

# ─── Config ────────────────────────────────────────────────────────────────────

TELEGRAM_TOKEN   = os.environ.get("TELEGRAM_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")

SCOUT_LOG = "scout_opportunities.jsonl"

# Fallback estático — se usa si la API de eToro no está disponible
CURRENT_PORTFOLIO = {
    "NVDA","AMD","TSM","ASML","AVGO","UCTT","PANW","CRWD","RHM","RTX",
    "NOC","BWXT","VST","VRT","ERJ","PLTR","GOOG","MSFT","AMZN","KO",
    "SMFG","GILD","AIR","IBE","CABK","IAG","LNVGY","UNH","OKLO","AAPL"
}


def load_portfolio_tickers_from_etoro() -> set:
    """
    Obtiene tickers actuales del portfolio desde eToro API.
    Retorna set de tickers, o CURRENT_PORTFOLIO como fallback.
    """
    try:
        from etoro_client import get_portfolio
        from config import INSTRUMENT_MAP
        data    = get_portfolio()
        cp      = data.get("clientPortfolio", {})
        tickers = {INSTRUMENT_MAP[p["instrumentID"]]
                   for p in cp.get("positions", [])
                   if p.get("instrumentID") in INSTRUMENT_MAP}
        if tickers:
            print(f"  📡 Portfolio real: {len(tickers)} tickers cargados")
            return tickers
    except Exception as e:
        print(f"  ⚠️  Usando portfolio manual como fallback: {e}")
    return CURRENT_PORTFOLIO

# Criterios de calidad mínimos
MIN_MARKET_CAP_B  = 1.0    # mínimo $1B market cap
MIN_AVG_VOLUME    = 500000 # mínimo 500k shares/día
MAX_BETA          = 4.0    # máximo beta (excluir micro-caps volátiles)
MIN_BETA          = 0.3    # mínimo beta (queremos growth, no utilities puras)

# ─── Tesis del portfolio de Sergio ─────────────────────────────────────────────

THESIS_UNIVERSE = {
    "ai_infrastructure": {
        "nombre":      "AI Infrastructure — Semiconductores",
        "descripcion": "Empresas que fabrican chips, equipos de litografía, o componentes críticos para AI",
        "posiciones":  ["NVDA", "AMD", "TSM", "ASML", "AVGO", "UCTT"],
        "candidatos":  ["MRVL", "ARM", "LRCX", "AMAT", "KLAC", "ONTO",
                        "WOLF", "AIXI", "SMCI", "MU", "INTC", "QCOM",
                        "CRUS", "MPWR", "ENTG", "ACLS"],
        "emoji":       "🧠",
    },
    "cybersecurity": {
        "nombre":      "Ciberseguridad",
        "descripcion": "Empresas de seguridad en cloud, endpoints y gobierno",
        "posiciones":  ["PANW", "CRWD"],
        "candidatos":  ["S", "CYBR", "ZS", "FTNT", "OKTA", "NET",
                        "QLYS", "VRNS", "TENB", "RPD", "SAIL"],
        "emoji":       "🔒",
    },
    "defense_europe": {
        "nombre":      "Defensa — Europa y Rearmament",
        "descripcion": "Defensa europea, rearmamento OTAN, tecnología militar",
        "posiciones":  ["RHM", "RTX", "NOC", "BWXT"],
        "candidatos":  ["LDOS", "HWM", "LHX", "BAH", "SAIC", "KTOS",
                        "AXON", "CACI", "PLTR", "DRS", "ACHR"],
        "emoji":       "🛡️",
    },
    "energy_ai": {
        "nombre":      "Energía para AI Datacenters",
        "descripcion": "Nuclear, gas, infraestructura eléctrica para datacenters",
        "posiciones":  ["VST", "VRT", "OKLO"],
        "candidatos":  ["CEG", "ETN", "NRG", "PWR", "FSLR", "NEE",
                        "PCG", "DUK", "SO", "AEE", "NUE", "SMR",
                        "BWXT", "CCJ", "UEC", "NXE"],
        "emoji":       "⚡",
    },
    "space_economy": {
        "nombre":      "Economía Espacial — Nueva Frontera",
        "descripcion": "Lanzadores, satélites, comunicaciones espaciales, IPO SpaceX play",
        "posiciones":  ["ERJ"],
        "candidatos":  ["RKLB", "IRDM", "ASTS", "TE", "MAXR", "SPIR",
                        "MNTS", "LUNR", "RDW", "ATRO", "SATL",
                        "GILT", "GSAT", "VSAT"],
        "emoji":       "🚀",
    },
    "ai_software_gov": {
        "nombre":      "AI Software — Gobierno y Empresa",
        "descripcion": "Software AI para gobierno, defensa y enterprise",
        "posiciones":  ["PLTR", "MSFT", "GOOG"],
        "candidatos":  ["BBAI", "SOUN", "AI", "PATH", "AMBA",
                        "LNVGY", "SNOW", "MDB", "DDOG", "ESTC",
                        "TEM", "RXRX", "ABSI"],
        "emoji":       "🤖",
    },
}


# ─── Telegram ──────────────────────────────────────────────────────────────────

def send_telegram(message: str) -> bool:
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        return False
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        resp = requests.post(url, json={
            "chat_id": TELEGRAM_CHAT_ID, "text": message, "parse_mode": "HTML"
        }, timeout=10)
        return resp.status_code == 200
    except:
        return False


# ─── Análisis de candidatos ────────────────────────────────────────────────────

def analyze_candidate(ticker: str) -> dict:
    """
    Analiza un ticker candidato con yfinance.
    Retorna métricas de calidad y puntuación.
    """
    import yfinance as yf

    try:
        tk   = yf.Ticker(ticker)
        info = tk.info

        # Métricas básicas
        market_cap   = info.get("marketCap", 0) / 1e9         # en $B
        avg_volume   = info.get("averageVolume", 0)
        beta         = info.get("beta", 1.0) or 1.0
        pe_fwd       = info.get("forwardPE", None)
        revenue_gr   = info.get("revenueGrowth", None)         # YoY
        gross_margin = info.get("grossMargins", None)
        price        = info.get("currentPrice", 0) or info.get("regularMarketPrice", 0)
        name         = info.get("shortName", ticker)
        sector       = info.get("sector", "Unknown")

        # Datos de precio
        hist = tk.history(period="6mo")
        if len(hist) < 20:
            return None

        ret_1m  = (hist["Close"].iloc[-1] / hist["Close"].iloc[-21] - 1) * 100 if len(hist) >= 21 else None
        ret_3m  = (hist["Close"].iloc[-1] / hist["Close"].iloc[-63] - 1) * 100 if len(hist) >= 63 else None
        ret_6m  = (hist["Close"].iloc[-1] / hist["Close"].iloc[0]   - 1) * 100
        vol_ann = hist["Close"].pct_change().std() * np.sqrt(252) * 100

        # Filtros de calidad
        if market_cap < MIN_MARKET_CAP_B:
            return None
        if avg_volume < MIN_AVG_VOLUME:
            return None
        if beta > MAX_BETA or beta < MIN_BETA:
            return None

        # Score de oportunidad (0-100)
        score = 50  # base

        # Momentum (peso 30%)
        if ret_1m is not None:
            if ret_1m > 10:   score += 15
            elif ret_1m > 5:  score += 8
            elif ret_1m < -15: score -= 10
            elif ret_1m < -5: score -= 5

        if ret_3m is not None:
            if ret_3m > 20:   score += 10
            elif ret_3m > 10: score += 5
            elif ret_3m < -20: score -= 8

        # Crecimiento (peso 25%)
        if revenue_gr:
            if revenue_gr > 0.3:   score += 15
            elif revenue_gr > 0.15: score += 8
            elif revenue_gr < 0:    score -= 10

        # Márgenes (peso 20%)
        if gross_margin:
            if gross_margin > 0.6:   score += 10
            elif gross_margin > 0.4: score += 5
            elif gross_margin < 0.2: score -= 5

        # Tamaño (peso 15%) — preferimos mid-cap con crecimiento
        if 5 < market_cap < 50:    score += 8   # sweet spot
        elif 1 < market_cap < 5:   score += 5   # small pero viable
        elif market_cap > 200:     score -= 5   # mega-cap, menos upside

        # Volatilidad (peso 10%)
        if 25 < vol_ann < 60:      score += 5   # vol razonable para growth
        elif vol_ann > 80:         score -= 8   # demasiado especulativo

        return {
            "ticker":       ticker,
            "name":         name,
            "sector":       sector,
            "price":        round(price, 2),
            "market_cap_b": round(market_cap, 1),
            "beta":         round(beta, 2),
            "ret_1m":       round(ret_1m, 1) if ret_1m else None,
            "ret_3m":       round(ret_3m, 1) if ret_3m else None,
            "ret_6m":       round(ret_6m, 1),
            "vol_ann":      round(vol_ann, 1),
            "revenue_gr":   round(revenue_gr * 100, 1) if revenue_gr else None,
            "gross_margin": round(gross_margin * 100, 1) if gross_margin else None,
            "pe_fwd":       round(pe_fwd, 1) if pe_fwd else None,
            "score":        min(100, max(0, score)),
        }

    except Exception as e:
        return None


def screen_thesis(thesis_key: str, thesis: dict, top_n: int = 3,
                  current_portfolio: set = None) -> list:
    """Analiza todos los candidatos de una tesis y devuelve los mejores."""
    portfolio  = current_portfolio or CURRENT_PORTFOLIO
    candidates = [t for t in thesis["candidatos"] if t not in portfolio]

    print(f"\n  {thesis['emoji']} {thesis['nombre']} — analizando {len(candidates)} candidatos...")

    results = []
    for ticker in candidates:
        result = analyze_candidate(ticker)
        if result:
            results.append(result)

    # Ordenar por score
    results.sort(key=lambda x: x["score"], reverse=True)
    return results[:top_n]


# ─── Generar reporte ───────────────────────────────────────────────────────────

def format_candidate(c: dict, rank: int) -> str:
    ret1  = f"{c['ret_1m']:+.1f}%" if c["ret_1m"] is not None else "N/A"
    ret3  = f"{c['ret_3m']:+.1f}%" if c["ret_3m"] is not None else "N/A"
    rev_g = f"{c['revenue_gr']:+.0f}%" if c["revenue_gr"] else "N/A"
    pe    = f"{c['pe_fwd']:.0f}x" if c["pe_fwd"] else "N/A"
    gm    = f"{c['gross_margin']:.0f}%" if c["gross_margin"] else "N/A"

    score_icon = "🔥" if c["score"] >= 70 else "⭐" if c["score"] >= 55 else "👀"

    return (
        f"{rank}. {score_icon} <b>{c['ticker']}</b> — {c['name']}\n"
        f"   💰 ${c['price']} | Cap: ${c['market_cap_b']:.1f}B | Beta: {c['beta']}\n"
        f"   📈 1m: {ret1} | 3m: {ret3} | 6m: {c['ret_6m']:+.1f}%\n"
        f"   📊 Rev growth: {rev_g} | Margen bruto: {gm} | P/E fwd: {pe}\n"
        f"   Score: {c['score']}/100"
    )


def build_scout_message(results_by_thesis: dict, date_str: str) -> str:
    msg = (
        f"🔭 <b>Opportunity Scout — {date_str}</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"Nuevas ideas alineadas con tu portfolio\n\n"
    )

    total_candidates = sum(len(v) for v in results_by_thesis.values())
    if total_candidates == 0:
        msg += "Sin nuevas oportunidades destacadas esta semana."
        return msg

    for thesis_key, candidates in results_by_thesis.items():
        if not candidates:
            continue
        thesis = THESIS_UNIVERSE[thesis_key]
        msg += f"{thesis['emoji']} <b>{thesis['nombre']}</b>\n"
        for i, c in enumerate(candidates, 1):
            msg += format_candidate(c, i) + "\n"
        msg += "\n"

    msg += (
        f"━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"💡 Para analizar en profundidad, añade el ticker a\n"
        f"PORTFOLIO_TICKERS en trading_agents_etoro.py\n"
        f"🕐 {datetime.now().strftime('%Y-%m-%d %H:%M')}"
    )
    return msg


def save_scout_log(results_by_thesis: dict):
    entry = {
        "date":    datetime.now().isoformat(),
        "results": results_by_thesis,
    }
    with open(SCOUT_LOG, "a") as f:
        f.write(json.dumps(entry) + "\n")


# ─── Consola ───────────────────────────────────────────────────────────────────

def print_results(results_by_thesis: dict):
    for thesis_key, candidates in results_by_thesis.items():
        if not candidates:
            continue
        thesis = THESIS_UNIVERSE[thesis_key]
        print(f"\n  {thesis['emoji']} {thesis['nombre']}")

        rows = []
        for c in candidates:
            ret1 = f"{c['ret_1m']:+.1f}%" if c["ret_1m"] is not None else "N/A"
            ret3 = f"{c['ret_3m']:+.1f}%" if c["ret_3m"] is not None else "N/A"
            revg = f"{c['revenue_gr']:+.0f}%" if c["revenue_gr"] else "N/A"
            rows.append([
                c["ticker"], f"${c['price']}", f"${c['market_cap_b']:.1f}B",
                ret1, ret3, revg, f"{c['score']}/100"
            ])

        print(tabulate(rows,
                       headers=["Ticker", "Precio", "Cap", "1m", "3m", "Rev↑", "Score"],
                       tablefmt="rounded_outline"))


# ─── Main ───────────────────────────────────────────────────────────────────────

def run_scout(thesis_filter: str = None, quick: bool = False) -> dict:
    date_str  = datetime.now().strftime("%d %b %Y")
    top_n     = 2 if quick else 3

    print(f"\n🔭 Opportunity Scout | {date_str}")
    print(f"   Buscando ideas alineadas con tu portfolio...\n")

    current_portfolio = load_portfolio_tickers_from_etoro()

    theses_to_scan = THESIS_UNIVERSE
    if thesis_filter:
        theses_to_scan = {k: v for k, v in THESIS_UNIVERSE.items()
                          if thesis_filter.lower() in k.lower()}
        if not theses_to_scan:
            print(f"  ⚠️ Tesis '{thesis_filter}' no encontrada.")
            print(f"  Opciones: {', '.join(THESIS_UNIVERSE.keys())}")
            return {}

    results_by_thesis = {}
    for thesis_key, thesis in theses_to_scan.items():
        top = screen_thesis(thesis_key, thesis, top_n=top_n,
                            current_portfolio=current_portfolio)
        results_by_thesis[thesis_key] = top

    # Mostrar en consola
    print_results(results_by_thesis)

    # Resumen
    total = sum(len(v) for v in results_by_thesis.values())
    high_score = [c for v in results_by_thesis.values() for c in v if c["score"] >= 70]

    print(f"\n  📊 {total} candidatos analizados")
    if high_score:
        print(f"  🔥 Alta convicción: {', '.join(c['ticker'] for c in high_score)}")

    # Guardar log
    save_scout_log(results_by_thesis)

    # Enviar Telegram
    msg  = build_scout_message(results_by_thesis, date_str)
    sent = send_telegram(msg)
    print(f"\n  📱 Telegram: {'✅ enviado' if sent else '❌ falló'}")

    # Sugerir añadir a trading_agents_etoro.py
    all_candidates = [c["ticker"] for v in results_by_thesis.values() for c in v]
    if all_candidates:
        print(f"\n  💡 Para analizar en profundidad el domingo, añade a trading_agents_etoro.py:")
        print(f"     NEW_POSITION_CANDIDATES = {all_candidates}")

    return results_by_thesis


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

    parser = argparse.ArgumentParser(description="Opportunity Scout")
    parser.add_argument("--thesis", type=str, help="Filtrar por tesis (ai, cyber, defense, energy, space, gov)")
    parser.add_argument("--quick",  action="store_true", help="Top 2 por tesis, sin análisis profundo")
    args = parser.parse_args()

    run_scout(thesis_filter=args.thesis, quick=args.quick)
