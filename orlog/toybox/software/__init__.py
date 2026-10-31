"""
orlog/toybox/software/ — Building subscription-based web or mobile software products for end users or businesses.

Pre-seeded domain context for Orlog. When a user's goal maps to this domain,
Orlog loads this bin and knows immediately what agents, metrics, and APIs apply.

Cross-domain agents (notification, analytics, document, anomaly, crm) live in
toybox/generic/ and are available to all bins.
"""

DOMAIN = "software"
DESCRIPTION = "Building subscription-based web or mobile software products for end users or businesses."

AGENT_PACK = ['feature_usage_analytics', 'churn_predictor', 'onboarding_optimizer', 'error_crash_monitor', 'cicd_pipeline_manager', 'sla_uptime_monitor', 'revenue_tracker', 'support_bot']

KEY_METRICS = ['mrr', 'churn_rate', 'net_revenue_retention', 'cac', 'ltv_cac_ratio', 'time_to_value', 'feature_adoption_rate', 'uptime_pct']

COMMON_APIS = ['Stripe API', 'PostHog API', 'Mixpanel API', 'ChartMogul API', 'Sentry API', 'PagerDuty API', 'Datadog API', 'Auth0', 'Twilio', 'Segment']

REQUIRED_ENV = ['STRIPE_SECRET_KEY', 'POSTHOG_API_KEY']
