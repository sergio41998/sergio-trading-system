# 📝 Changelog

Historial de cambios del sistema. Formato: [version] fecha — descripción.

---

## [1.1.0] — 2026-05-31

### Correcciones y refactoring de infraestructura

**fix: modelo LLM corregido a claude-haiku-4-5** (`fdbfbb7`)
- `trading_agents_etoro.py` usaba `claude-sonnet-4-6` en lugar de `claude-haiku-4-5-20251001`
- Coste por ejecución dominical reducido ~3-5x
- Añadido `CLAUDE.md` como memoria permanente del proyecto para Claude Code

**feat: cvar_gate conectado al portfolio real** (`b42759b`)
- `build_portfolio_from_etoro()` construye pesos y NAV desde la API en cada ejecución
- El CVaR-99 y P&L del Morning Briefing se calculan sobre el portfolio actual (31 tickers reales vs 20 hardcodeados)
- Fallback automático a pesos manuales si la API no responde
- El mensaje Telegram indica la fuente de datos usada (`📡 portfolio real / pesos manuales`)

**fix: price_monitor resuelve precios para nuevas posiciones** (`89f0126`)
- `get_current_prices()` solo consultaba `/pnl`, dejando sin precio los tickers no poseídos
- Las órdenes de nueva posición nunca se disparaban (bug silencioso)
- Fallback batch a yfinance para los instrument IDs sin precio tras la llamada a eToro

**refactor: INSTRUMENT_MAP centralizado + advisors con datos reales** (`3625bcb`)
- Creado `config.py` como fuente única de `INSTRUMENT_MAP` (elimina duplicación y discrepancia `AIR.PA` vs `AIR`)
- `invest_advisor.py`: `load_positions_from_etoro()` carga P&L real; funciones `find_positions_to_close` y `find_best_opportunity` reciben `positions` como parámetro
- `opportunity_scout.py`: `load_portfolio_tickers_from_etoro()` obtiene tickers reales para filtrar candidatos ya poseídos
- Ambos mantienen fallback a datos hardcodeados si la API falla

---

## [1.0.0] — 2026-05-26

### Lanzamiento inicial del sistema completo

**Añadido:**
- `etoro_client.py` — cliente API eToro con mapa de 30 instrumentos
- `trading_agents_etoro.py` — TradingAgents con Claude Haiku 4.5
- `price_monitor.py` — monitor de precios + órdenes condicionales
- `cvar_gate.py` — CVaR-99 Gate + Morning Briefing Telegram completo
- `opportunity_scout.py` — screener temático por 6 tesis del portfolio
- `invest_advisor.py` — recomendación de inversión dado importe
- `postmortem.py` — análisis calidad de decisiones
- `orders.json` — estructura de órdenes condicionales
- Integración Telegram Bot para notificaciones
- Cron diario 8:50 para Morning Briefing automático

**Morning Briefing incluye:**
- CVaR-99 con tendencia histórica
- P&L del día anterior con top movers
- Contexto macro (VIX, SPY, EUR/USD, Yield 10Y)
- Earnings próximos 7 días de posiciones del portfolio
- Modo de mercado (Normal / Corrección / Crisis)
- Resumen ejecutivo en lenguaje natural

**Opportunity Scout cubre 6 tesis:**
- AI Infrastructure (16 candidatos)
- Ciberseguridad (11 candidatos)
- Defensa Europa (10 candidatos)
- Energía AI Datacenters (15 candidatos)
- Economía Espacial (14 candidatos)
- AI Software Gov (12 candidatos)

**Primera señal real generada:**
- NVDA → HOLD pre-earnings (20 mayo 2026)
- Análisis institucional: PEG 0.72, FCF $96B, entrada sugerida $208-215

---

## Próxima versión planificada: [1.1.0] — Junio 2026

- [ ] Prediction Markets signal layer (Polymarket API)
- [ ] Earnings blackout automático en price_monitor
- [ ] Actualización pesos CVaR con portfolio real Q2
- [ ] Nuevos candidatos: RKLB, IRDM, ASTS, MU, DDOG, AXON
