# ROADMAP — Sergio Trading System

Estado de mejoras planificadas. Actualizado: 2026-05-31.

---

## Fase 1 — Completada (v1.0.0, 2026-05-26)

- [x] `etoro_client.py` — cliente API eToro
- [x] `trading_agents_etoro.py` — análisis multi-agente LLM
- [x] `price_monitor.py` — monitor de precios + órdenes condicionales
- [x] `cvar_gate.py` — CVaR-99 Gate + Morning Briefing Telegram
- [x] `opportunity_scout.py` — screener temático por 6 tesis
- [x] `invest_advisor.py` — recomendación de inversión dado importe
- [x] `postmortem.py` — análisis calidad de decisiones
- [x] Earnings blackout automático T-2 / T+3 en `price_monitor.py`
- [x] Integración Telegram Bot (notificaciones y briefing diario)
- [x] Cron diario 8:50 para Morning Briefing

---

## Fase 1.1 — Completada (v1.1.0, 2026-05-31)

Correcciones de infraestructura antes de añadir features nuevas.

- [x] **Modelo LLM corregido** — `claude-haiku-4-5-20251001` en `trading_agents_etoro.py`
      (era `claude-sonnet-4-6`; coste reducido ~3-5x por ejecución dominical)
- [x] **CVaR Gate con datos reales** — `cvar_gate.py` carga pesos y NAV desde la API
      en lugar de constantes hardcodeadas; fallback automático si API falla
- [x] **Precios para nuevas posiciones** — `price_monitor.py` resuelve via yfinance
      los tickers no poseídos que la API `/pnl` no devuelve
- [x] **INSTRUMENT_MAP centralizado** — `config.py` como fuente única; elimina
      duplicación entre `etoro_client.py` y `price_monitor.py`
- [x] **Posiciones reales en advisors** — `invest_advisor.py` y `opportunity_scout.py`
      cargan el portfolio real desde eToro en lugar de dicts hardcodeados
- [x] **CLAUDE.md** — memoria permanente del proyecto para Claude Code

---

## Fase 2 — Pendiente

### Alta prioridad (antes de siguientes features)

- [ ] **Actualización automática de pesos CVaR con portfolio real Q2**
      `cvar_gate.py` ya usa pesos reales en tiempo de ejecución (v1.1.0),
      pero el dict `PORTFOLIO` hardcodeado sigue como fallback de emergencia.
      Revisar si los pesos del fallback reflejan la composición actual del Q2.

- [ ] **Conversión EUR/USD dinámica**
      Actualmente hardcodeada a 1.08 en tres scripts (`price_monitor.py`,
      `cvar_gate.py`, `invest_advisor.py`). Obtener tasa real desde yfinance
      (`EURUSD=X`) que ya se descarga en el Morning Briefing.

### Features nuevas

- [ ] **Prediction Markets signal layer** — Polymarket / Kalshi API
      Añadir señal de mercados de predicción como capa adicional de confirmación
      antes de ejecutar órdenes. Ver `README.md` sección "Capas disruptivas".

- [ ] **WorldQuant 101 Alphas Agent**
      Incorporar factores cuantitativos clásicos (momentum, reversal, liquidity)
      como señales adicionales en `trading_agents_etoro.py`.

- [ ] **Nuevos candidatos watchlist**
      RKLB, IRDM, ASTS (space economy), MU, DDOG (AI infrastructure),
      AXON (defense tech). Añadir a `WATCHLIST_TICKERS` en `trading_agents_etoro.py`.

---

## Fase 3 — Planificada

- [ ] **Conformal Prediction wrapper**
      Intervalos de confianza calibrados para las señales de TradingAgents.
      Reduce el riesgo de sobreconfianza en señales HIGH confidence.

- [ ] **Backtesting automático en postmortem**
      Actualmente `postmortem.py` evalúa retornos reales post-ejecución.
      Añadir simulación de señales históricas de TradingAgents para validar
      el sistema antes de confiar en señales nuevas.

---

## Deuda técnica conocida

| Issue | Script | Impacto | Esfuerzo |
|-------|--------|---------|----------|
| EUR/USD hardcodeado a 1.08 | price_monitor, cvar_gate, invest_advisor | Medio | Bajo |
| `postmortem.py` usa `iloc[days]` en lugar de fecha real | postmortem.py | Bajo | Bajo |
| Caché de earnings invalida todo si falta 1 ticker | earnings_blackout.py | Bajo | Bajo |
| `reconcile_with_portfolio()` busca campo `instrumentTicker` que no existe en API | trading_agents_etoro.py | Medio | Bajo |
