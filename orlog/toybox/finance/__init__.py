"""
orlog/toybox/finance/ — Building algorithmic trading systems, investment research tools, or personal/institutional finance applications.

Pre-seeded domain context for Orlog. When a user's goal maps to this domain,
Orlog loads this bin and knows immediately what agents, metrics, and APIs apply.

Cross-domain agents (notification, analytics, document, anomaly, crm) live in
toybox/generic/ and are available to all bins.
"""

DOMAIN = "finance"
DESCRIPTION = "Building algorithmic trading systems, investment research tools, or personal/institutional finance applications."

AGENT_PACK = ['market_data_ingestion', 'strategy_backtester', 'order_execution', 'portfolio_risk_monitor', 'news_sentiment_analyzer', 'position_sizing_calculator', 'tax_lot_tracker', 'drawdown_circuit_breaker']

KEY_METRICS = ['sharpe_ratio', 'max_drawdown_pct', 'win_rate', 'profit_factor', 'cagr', 'slippage_cost', 'portfolio_beta', 'var']

COMMON_APIS = ['Alpaca Trading API', 'Interactive Brokers API', 'Alpha Vantage API', 'Polygon.io', 'Yahoo Finance API', 'Binance API', 'FRED API', 'SEC EDGAR API']

REQUIRED_ENV = ['ALPACA_API_KEY', 'ALPACA_SECRET_KEY']
