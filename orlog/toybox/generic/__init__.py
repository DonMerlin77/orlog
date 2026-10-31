"""
orlog/toybox/generic/ — Domain-agnostic agents for any project that doesn't fit a named bin.

Pre-seeded domain context for Orlog. When a user's goal maps to this domain,
Orlog loads this bin and knows immediately what agents, metrics, and APIs apply.

Cross-domain agents (notification, analytics, document, anomaly, crm) live in
toybox/generic/ and are available to all bins.
"""

DOMAIN = "generic"
DESCRIPTION = "Domain-agnostic agents for any project that doesn't fit a named bin."

AGENT_PACK = ['goal_tracker', 'research_relay', 'document_drafter', 'analytics_aggregator', 'notification_sequencer']

KEY_METRICS = ['task_completion_rate', 'output_quality_score', 'time_to_first_result', 'iteration_count']

COMMON_APIS = ['Anthropic API', 'Google Search API', 'Zapier API']

REQUIRED_ENV = ['ANTHROPIC_API_KEY']
