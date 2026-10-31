"""
orlog/toybox/nonprofit/ — Running mission-driven organizations relying on donor funding, volunteer coordination, and impact measurement.

Pre-seeded domain context for Orlog. When a user's goal maps to this domain,
Orlog loads this bin and knows immediately what agents, metrics, and APIs apply.

Cross-domain agents (notification, analytics, document, anomaly, crm) live in
toybox/generic/ and are available to all bins.
"""

DOMAIN = "nonprofit"
DESCRIPTION = "Running mission-driven organizations relying on donor funding, volunteer coordination, and impact measurement."

AGENT_PACK = ['donor_crm_manager', 'grant_research_writer', 'fundraising_campaign', 'volunteer_coordinator', 'impact_metrics_reporter', 'recurring_giving_optimizer', 'matching_gift_finder', 'annual_report_generator']

KEY_METRICS = ['donor_retention_rate', 'average_gift_size', 'donor_ltv', 'cost_per_dollar_raised', 'volunteer_hours', 'program_impact_ratio', 'grant_success_rate']

COMMON_APIS = ['Bloomerang API', 'Virtuous API', 'Blackbaud API', 'Stripe API', 'PayPal Giving Fund API', 'Mailchimp API', 'Salesforce NPSP']

REQUIRED_ENV = ['BLOOMERANG_API_KEY', 'STRIPE_SECRET_KEY']
