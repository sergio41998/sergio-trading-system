# CLAUDE.md — Sergio Trading System

Memoria permanente del proyecto. Lee este archivo al inicio de cada conversación.

---

## Qué es este proyecto

Sistema personal de gestión de portfolio con IA para la cuenta real eToro de Sergio.
Stack: eToro API + TradingAgents + Claude + yfinance + Telegram Bot.
Capital de referencia: ~€15k. Repositorio privado.

---

## Perfil de inversión

- **Estilo**: Alta convicción, concentrado, growth/tech
- **Horizonte**: Largo plazo con rebalanceo trimestral
- **Broker**: eToro (cuenta real, MODE = "real" en todos los scripts)
- **Tesis activas** (6):
  - `ai_infrastructure` — NVDA, AMD, TSM, ASML, AVGO, UCTT
  - `cybersecurity` — PANW, CRWD
  - `defense_europe` — RHM, RTX, NOC, BWXT
  - `energy_ai` — VST, VRT, OKLO
  - `space_economy` — ERJ
  - `ai_software_gov` — PLTR, MSFT, GOOG
- **Reglas de riesgo**: CVaR-99 umbral 4% | stop loss estándar 7-8% | no apalancar (leverage=1)
- **Pérdida tolerada antes de cerrar**: >15% + señal SELL o tesis rota

---

## Scripts — qué hace cada uno

### `etoro_client.py`
Cliente base de la API eToro. Lee portfolio real, P&L, balance.
Funciones clave: `get_portfolio()`, `get_account_balance()`, `search_instrument()`.
Usado como importación por los demás scripts.
```bash
python3.11 etoro_client.py          # ver portfolio completo en consola
```

### `cvar_gate.py`
Morning Briefing diario + CVaR Gate de riesgo.
Calcula CVaR-99 histórico con 60 días de datos (scipy/numpy), evalúa modo de mercado
(Normal / Corrección / Crisis), P&L del día anterior, earnings próximos, contexto macro
(VIX, SPY, EUR/USD, Yield 10Y). Envía todo por Telegram a las 8:50 vía cron.
Guarda historial en `cvar_history.jsonl` y estado actual en `cvar_status.json`.

Secciones adicionales en el briefing:
- **🔍 Descubrimientos Scout** — lista top 5 candidatos Scout (score≥85, log≤7d) que
  no están en cartera. Sin auto-promoción: ascender al watchlist es decisión manual.
- **⚠️ Modo defensivo ACTIVO** — aparece si CVaR>3.9% o VIX>25 (o override manual),
  con el motivo explícito. Solo avisa, no opera.
```bash
python3.11 cvar_gate.py             # briefing completo
python3.11 cvar_gate.py --test      # probar Telegram
python3.11 cvar_gate.py --status    # estado CVaR actual
python3.11 cvar_gate.py --monitor   # loop cada hora
```

### `price_monitor.py`
Monitor de precios + motor de ejecución automática de órdenes condicionales.
Lee `orders.json` cada 60s, comprueba triggers (below/above), aplica earnings blackout,
ejecuta en eToro API cuando el precio toca el nivel. Notifica por Telegram.
Desactiva la orden después de ejecutarla. Guarda log en `executed_orders.jsonl`.
```bash
python3.11 price_monitor.py         # lanzar monitor (corre en background)
```

### `earnings_blackout.py`
Módulo de protección automática alrededor de earnings. Ventana: **T-2 a T+3**.
Importado por `price_monitor.py` — si el ticker está en blackout, la orden se salta.
Usa caché de 24h en `earnings_cache.json` para no consultar yfinance en cada ciclo.
```bash
python3.11 earnings_blackout.py                # calendario completo
python3.11 earnings_blackout.py --check NVDA   # comprobar ticker específico
```

### `trading_agents_etoro.py`
Análisis multi-agente LLM (TradingAgents framework) sobre el portfolio.
Genera señales BUY/SELL/HOLD con confianza HIGH/MEDIUM/LOW para cada ticker.
Las guarda en `trading_decisions.jsonl`. No ejecuta ninguna orden — solo genera señales.
Coste estimado: ~€2-3 por ejecución completa (+~€0.01 por cada ticker Scout extra).
Ejecutar el **primer domingo del mes** (tras ejecutar el Scout el sábado).

**Universo de análisis — tres capas:**
1. **Cartera real** — `build_portfolio_tickers_from_etoro()` lee la API de eToro en
   tiempo real; fallback a `INSTRUMENT_MAP` de `config.py`. CVX y SMCI excluidos.
2. **Watchlist manual** — `WATCHLIST_TICKERS` (convicción propia, NO se auto-actualiza):
   `MU, DDOG, FSLR, AXON, SNOW, RKLB, IRDM, ASTS, TE`.
3. **Descubrimientos Scout** — `get_scout_candidates()` lee `scout_opportunities.jsonl`
   si tiene ≤7 días, filtra score≥85, deduplica, inyecta top 3. Si el log es más
   antiguo, devuelve [] con aviso — ejecutar Scout el sábado previo para datos frescos.

Constantes configurables: `SCOUT_TOP_N = 3`, `SCOUT_MIN_SCORE = 85`,
`SCOUT_MAX_AGE_DAYS = 7`.
```bash
python3.11 trading_agents_etoro.py
```

### `opportunity_scout.py`
Screener de nuevas ideas por tesis. Sin LLM — solo yfinance + scoring propio (0-100).
Filtra por market cap >$1B, volumen >500k, beta 0.3-4.0. Puntúa por momentum,
crecimiento de revenue, márgenes, tamaño. Descarta posiciones ya en portfolio.
Envía top candidatos por tesis a Telegram. Ejecutar el **sábado antes del análisis**.

**Modo defensivo** — dos estados discretos, sin auto-evolución:
- **Normal**: screenea las 6 tesis growth habituales.
- **Defensivo**: AÑADE temporalmente 4 tesis (utilities, oro/mineras, consumo básico,
  salud defensiva) al universo. Las tesis growth no se eliminan.
- Disparo automático: `CVaR > 3.9%` O `VIX > 25` (leídos de `cvar_status.json`
  y yfinance respectivamente).
- Override manual: `DEFENSIVE_MODE_OVERRIDE` en `opportunity_scout.py`:
  `None` = auto | `True` = forzar defensivo | `False` = forzar normal.
  El override siempre gana al automático.
- El modo solo AVISA — no mueve órdenes, no altera CVaR Gate ni stop-losses.
```bash
python3.11 opportunity_scout.py               # análisis completo (6 tesis)
python3.11 opportunity_scout.py --quick       # top 2 por tesis, rápido
python3.11 opportunity_scout.py --thesis ai   # solo tesis AI
```

### `invest_advisor.py`
Recomendación de inversión dado un importe. Lee señales de `trading_decisions.jsonl`
y estado CVaR. Identifica posiciones a cerrar (pérdida >15% + señal SELL o tesis rota).
Recomienda UNA sola posición con el importe completo. Bloquea si CVaR es CRÍTICO.
Actualizar `CURRENT_POSITIONS` mensualmente con P&L reales.
```bash
python3.11 invest_advisor.py --amount 1000
```

### `postmortem.py`
Análisis de calidad de decisiones pasadas. Lee `executed_orders.jsonl`, obtiene precio
7d y 30d después de cada entrada, califica el proceso (0-7) independientemente del
resultado. Clasifica: buen proceso/resultado, buen proceso/mala suerte, suerte, error.
```bash
python3.11 postmortem.py            # análisis fin de trimestre
```

### `dtale_portfolio.py`
Visualización interactiva del portfolio con D-Tale (pandas GUI). Uso ocasional.

### `orders.json`
Archivo de órdenes condicionales editado manualmente cada mes.
Campos clave: `ticker`, `action`, `trigger_price`, `trigger_type` (below/above),
`amount_eur`, `stop_loss_pct`, `thesis`, `active`.

---

## Flujo mensual

```
Sábado        → python3.11 opportunity_scout.py --quick
                Lee resultados, identifica candidatos
                (guarda scout_opportunities.jsonl para el domingo)
                Si modo defensivo activo, añade tesis defensivas automáticamente

Domingo 1º    → python3.11 trading_agents_etoro.py
                Universo = cartera real (API eToro) + watchlist manual + top 3 Scout (≤7d)
                Si el log Scout tiene >7d, los descubrimientos no se inyectan — aviso
                Lee señales en trading_decisions.jsonl
                python3.11 invest_advisor.py --amount <importe>
                Editar orders.json con nuevos niveles del mes
                python3.11 price_monitor.py &  (lanzar o reiniciar)

Lunes-Viernes → price_monitor corre en background, ejecuta automáticamente
                Morning Briefing llega por Telegram a las 8:50

Fin trimestre → python3.11 postmortem.py
                Rebalanceo manual en eToro si drift >5%
```

---

## Reglas importantes (no negociables)

1. **Modelo LLM**: `claude-haiku-4-5` para TradingAgents (balance coste/calidad).
   Si hay que cambiar el modelo en `trading_agents_etoro.py`, usar `claude-haiku-4-5-20251001`.

2. **Python**: siempre `python3.11`. El venv se activa con `source venv/bin/activate`.

3. **Seguridad**: **NUNCA subir `.env` al repositorio**. El `.gitignore` excluye
   `.env`, `*.jsonl`, `cvar_status.json`. Si se detecta que algún archivo sensible
   entró en git, eliminar inmediatamente del historial.

4. **Earnings blackout**: **T-2 a T+3** — no ejecutar órdenes de ese ticker.
   Implementado en `earnings_blackout.py` e importado por `price_monitor.py`.
   Constantes: `DAYS_BEFORE = 2`, `DAYS_AFTER = 3`.

5. **Sin apalancamiento**: `leverage=1` siempre. El sistema es para posiciones de
   convicción a largo plazo, no trading especulativo.

6. **CVaR gate**: si `cvar_status.json` indica nivel CRÍTICO, `invest_advisor.py`
   bloquea nuevas entradas. Si es ALTO, reduce importe al 50%.

7. **MODE = "real"**: todos los scripts operan en cuenta real por defecto.
   Cambiar a `"demo"` para pruebas sin capital real.

---

## Variables de entorno (`.env`)

```
ETORO_API_KEY=...
ETORO_USER_KEY=...
ANTHROPIC_API_KEY=...
TELEGRAM_TOKEN=...
TELEGRAM_CHAT_ID=...
```

---

## Archivos de datos (no subir a git)

| Archivo | Contenido |
|---------|-----------|
| `cvar_history.jsonl` | Historial diario de CVaR (fecha, valor, nivel) |
| `cvar_status.json` | Estado CVaR actual (última ejecución) |
| `earnings_cache.json` | Caché 24h de fechas de earnings |
| `trading_decisions.jsonl` | Señales históricas de TradingAgents |
| `scout_opportunities.jsonl` | Log de oportunidades encontradas por Scout |
| `executed_orders.jsonl` | Log de órdenes ejecutadas (para postmortem) |

---

## Stack tecnológico

| Componente | Tecnología |
|-----------|-----------|
| Broker | eToro API REST (`public-api.etoro.com/api/v1`) |
| LLM análisis | TradingAgents + `claude-haiku-4-5` |
| Riesgo | CVaR-99 histórico (numpy/scipy) |
| Datos mercado | yfinance |
| Notificaciones | Telegram Bot API |
| Lenguaje | Python 3.11 |

---

## Fase 2 planificada (no implementada)

- Prediction Markets signal layer (Polymarket/Kalshi API)
- WorldQuant 101 Alphas Agent
- Conformal Prediction wrapper para intervalos de confianza
- Actualización automática de pesos CVaR con portfolio real
