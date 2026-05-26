# 📝 Changelog

Historial de cambios del sistema. Formato: [version] fecha — descripción.

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
