# 🤖 Sergio Trading System

Sistema personal de gestión de portfolio con IA — eToro API + TradingAgents + Claude.

> **Repositorio privado** — contiene estrategia de inversión personal.

---

## ¿Qué hace este sistema?

Un stack completo de decisión humana + ejecución automática para una cartera
de alta convicción growth/tech en eToro.

```
Cada mañana (8:50)     → Morning Briefing en Telegram (CVaR, P&L, macro, earnings)
Primer domingo del mes → Análisis TradingAgents (~€2-3) + Opportunity Scout
Lunes-Viernes          → Price Monitor ejecuta órdenes condicionales automáticamente
Fin de trimestre       → Postmortem + rebalanceo manual
```

---

## Arquitectura

```
sergio-trading-system/
├── etoro_client.py          # Conexión API eToro (lectura portfolio real)
├── trading_agents_etoro.py  # Análisis multi-agente con Claude Haiku
├── price_monitor.py         # Monitor de precios + ejecución automática
├── cvar_gate.py             # CVaR Gate + Morning Briefing (Telegram)
├── opportunity_scout.py     # Screener de nuevas ideas por tesis
├── invest_advisor.py        # Recomendación de inversión dado un importe
├── postmortem.py            # Análisis calidad de decisiones
├── orders.json              # Órdenes condicionales (editar cada mes)
├── docs/
│   ├── STRATEGY.md          # Estrategia completa documentada
│   ├── ROADMAP.md           # Próximas mejoras planificadas
│   └── DECISIONS.md         # Log de decisiones importantes
└── CHANGELOG.md             # Historial de cambios
```

---

## Setup rápido

### 1. Clonar y configurar entorno

```bash
git clone git@github.com:TU_USUARIO/sergio-trading-system.git
cd sergio-trading-system
python3.11 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

### 2. Variables de entorno

```bash
cp .env.example .env
# Edita .env con tus keys reales
```

### 3. Verificar conexión

```bash
export $(cat .env | xargs)
python3.11 etoro_client.py          # Ver portfolio
python3.11 cvar_gate.py --test      # Probar Telegram
```

---

## Flujo mensual

```bash
# 1. Ver portfolio actual
python3.11 etoro_client.py

# 2. Buscar nuevas oportunidades (sábado)
python3.11 opportunity_scout.py --quick

# 3. Análisis completo (domingo)
python3.11 trading_agents_etoro.py

# 4. Ver recomendación de inversión
python3.11 invest_advisor.py --amount 1000

# 5. Actualizar orders.json con niveles del mes
nano orders.json

# 6. Lanzar monitor
python3.11 price_monitor.py &
```

---

## Stack tecnológico

| Componente | Tecnología |
|-----------|-----------|
| Broker | eToro API (REST) |
| Análisis LLM | TradingAgents + Claude Haiku 4.5 |
| Riesgo | CVaR-99 histórico (scipy) |
| Datos | yfinance + eToro WebSocket |
| Notificaciones | Telegram Bot API |
| Screener | yfinance + scoring propio |
| Lenguaje | Python 3.11 |

---

## Perfil de inversión

- **Estilo**: Alta convicción, concentrado, growth/tech
- **Horizonte**: Largo plazo con rebalanceo trimestral
- **Tesis principales**: AI Infrastructure, Ciberseguridad,
  Defensa europea, Energía AI, Espacio, AI Software Gov
- **Broker**: eToro (cuenta real)
- **Capital de referencia**: ~€15k

---

## Capas disruptivas implementadas

- [x] CVaR Gate — protección de portfolio
- [x] Morning Briefing — briefing diario automático
- [x] Opportunity Scout — screener temático por tesis
- [ ] Prediction Markets signal (Polymarket/Kalshi) — Fase 2
- [ ] WorldQuant 101 Alphas Agent — Fase 2
- [ ] Conformal Prediction wrapper — Fase 3

---

## Seguridad

- **Nunca** subir `.env` al repositorio
- `.gitignore` excluye: `.env`, `*.jsonl`, `cvar_status.json`
- Las API keys de eToro tienen permisos mínimos necesarios
- El `price_monitor.py` en modo `real` requiere confirmación explícita

---

## Licencia

Privado — uso personal exclusivo.
