"""
dtale con datos reales de eToro API
Uso: python3.11 dtale_portfolio.py
"""

import os, uuid, requests, dtale, pandas as pd

BASE_URL = "https://public-api.etoro.com/api/v1"
MODE     = "real"
API_KEY  = os.environ.get("ETORO_API_KEY", "")
USER_KEY = os.environ.get("ETORO_USER_KEY", "")

INSTRUMENT_MAP = {
    1001:"AAPL",1002:"GOOG",1003:"META",1004:"MSFT",1005:"AMZN",
    1024:"KO",1032:"UNH",1033:"RTX",1137:"NVDA",1234:"AIR",
    1320:"CABK",1330:"IBE",1465:"GILD",1545:"NOC",1832:"AMD",
    2040:"IAG",2312:"LNVGY",2587:"RHM",4124:"PANW",4236:"AVGO",
    4363:"VST",4481:"TSM",4509:"ASML",5506:"CRWD",7991:"PLTR",
    8687:"SMFG",8867:"VRT",8956:"BWXT",9006:"ERJ",9045:"UCTT",9956:"OKLO"
}

SECTOR_MAP = {
    "NVDA":"Semis","ASML":"Semis","TSM":"Semis","AMD":"Semis","AVGO":"Semis","UCTT":"Semis",
    "PANW":"Cyber","CRWD":"Cyber","PLTR":"AI/Gov","GOOG":"Tech","MSFT":"Tech","AMZN":"Tech",
    "VRT":"Energy","VST":"Energy","OKLO":"Nuclear","RTX":"Defense","BWXT":"Defense",
    "RHM":"Defense","NOC":"Defense","SMFG":"Finance","KO":"Consumer",
    "GILD":"Healthcare","AIR":"Aerospace","ERJ":"Aerospace","UNH":"Healthcare",
    "IBE":"Utilities","CABK":"Finance","IAG":"Airlines","LNVGY":"Tech","AAPL":"Tech"
}

BETA_MAP = {
    "NVDA":2.2,"PLTR":2.0,"OKLO":2.0,"UCTT":1.8,"AMD":2.0,"VRT":1.6,
    "TSM":1.5,"ASML":1.5,"PANW":1.4,"CRWD":1.6,"VST":1.4,"AVGO":1.3,
    "SMFG":1.3,"GOOG":1.1,"AMZN":1.2,"MSFT":1.0,"RTX":0.9,"KO":0.6,
    "RHM":1.2,"BWXT":1.0,"NOC":0.9,"GILD":0.7,"AIR":1.3,"ERJ":1.4,
}

def _headers():
    return {"x-api-key": API_KEY, "x-user-key": USER_KEY,
            "x-request-id": str(uuid.uuid4()), "Content-Type": "application/json"}

def get_portfolio():
    resp = requests.get(f"{BASE_URL}/trading/info/{MODE}/pnl",
                        headers=_headers(), timeout=15)
    resp.raise_for_status()
    return resp.json()

def build_dataframe(data):
    cp        = data.get("clientPortfolio", {})
    positions = cp.get("positions", [])

    grouped = {}
    for p in positions:
        iid    = p.get("instrumentID")
        ticker = INSTRUMENT_MAP.get(iid, f"ID:{iid}")
        pnl    = p.get("unrealizedPnL", {})
        amt    = p.get("amount", 0)
        pl     = pnl.get("pnL", 0)
        price  = pnl.get("closeRate", 0)

        if ticker not in grouped:
            grouped[ticker] = {"invested": 0, "pnl_usd": 0, "price": price, "tramos": 0}
        grouped[ticker]["invested"] += amt
        grouped[ticker]["pnl_usd"]  += pl
        grouped[ticker]["tramos"]   += 1

    rows = []
    for ticker, v in grouped.items():
        amt     = v["invested"]
        pl      = v["pnl_usd"]
        pct     = (pl / amt * 100) if amt else 0
        valor   = amt + pl
        rows.append({
            "Ticker":       ticker,
            "Sector":       SECTOR_MAP.get(ticker, "Otro"),
            "Invertido $":  round(amt, 2),
            "P&L $":        round(pl, 2),
            "P&L %":        round(pct, 1),
            "Valor actual": round(valor, 2),
            "Beta":         BETA_MAP.get(ticker, 1.0),
            "Tramos":       v["tramos"],
            "Precio":       round(v["price"], 2),
        })

    df = pd.DataFrame(rows)
    df = df.sort_values("P&L $", ascending=False).reset_index(drop=True)
    return df

print("Conectando a eToro API...")
data = get_portfolio()
df   = build_dataframe(data)

total_inv = df["Invertido $"].sum()
total_pl  = df["P&L $"].sum()
total_pct = total_pl / total_inv * 100 if total_inv else 0

print(f"✅ {len(df)} posiciones | Invertido: ${total_inv:,.0f} | P&L: ${total_pl:+,.0f} ({total_pct:+.1f}%)")
print("Abriendo dtale en el browser...")

d = dtale.show(df, open_browser=True)
print(f"URL: {d._url}")
input("Pulsa Enter para cerrar...")
