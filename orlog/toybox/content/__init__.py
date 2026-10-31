"""
orlog/toybox/content/ — Building audiences through written, video, or multi-format content across owned and social channels.

Pre-seeded domain context for Orlog. When a user's goal maps to this domain,
Orlog loads this bin and knows immediately what agents, metrics, and APIs apply.

Cross-domain agents (notification, analytics, document, anomaly, crm) live in
toybox/generic/ and are available to all bins.
"""

DOMAIN = "content"
DESCRIPTION = "Building audiences through written, video, or multi-format content across owned and social channels."

AGENT_PACK = ['seo_keyword_researcher', 'content_brief_generator', 'article_drafter', 'internal_link_builder', 'social_repurposing_agent', 'newsletter_writer', 'ad_revenue_optimizer', 'content_calendar_manager']

KEY_METRICS = ['organic_search_traffic', 'domain_authority', 'email_subscriber_count', 'newsletter_open_rate', 'rpm', 'affiliate_ctr', 'social_shares_per_post']

COMMON_APIS = ['Semrush API', 'Ahrefs API', 'Google Search Console API', 'WordPress REST API', 'Beehiiv API', 'ConvertKit API', 'YouTube Data API', 'Canva API']

REQUIRED_ENV = ['SEMRUSH_API_KEY', 'GOOGLE_API_KEY']
