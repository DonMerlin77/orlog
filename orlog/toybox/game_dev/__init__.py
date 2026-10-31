"""
orlog/toybox/game_dev/ — Building interactive games across PC, console, mobile, or web platforms.

Pre-seeded domain context for Orlog. When a user's goal maps to this domain,
Orlog loads this bin and knows immediately what agents, metrics, and APIs apply.

Cross-domain agents (notification, analytics, document, anomaly, crm) live in
toybox/generic/ and are available to all bins.
"""

DOMAIN = "game_dev"
DESCRIPTION = "Building interactive games across PC, console, mobile, or web platforms."

AGENT_PACK = ['asset_pipeline_manager', 'playtester_analytics', 'bug_triage', 'localization_manager', 'store_page_optimizer', 'economy_balancer', 'churn_predictor', 'community_sentiment_monitor']

KEY_METRICS = ['day1_retention', 'day30_retention', 'session_length', 'dau_mau_ratio', 'arpu', 'steam_review_score', 'crash_rate']

COMMON_APIS = ['Unity Services API', 'Steam Web API', 'GameAnalytics SDK', 'Firebase Analytics', 'Discord API', 'Xsolla', 'Photon', 'Apple Game Center API', 'Google Play Games API']

REQUIRED_ENV = ['STEAM_API_KEY', 'FIREBASE_API_KEY']
