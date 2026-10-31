"""
orlog/toybox/social_media/ — Building a following, monetizing content, or creating tools for the creator economy at scale.

Pre-seeded domain context for Orlog. When a user's goal maps to this domain,
Orlog loads this bin and knows immediately what agents, metrics, and APIs apply.

Cross-domain agents (notification, analytics, document, anomaly, crm) live in
toybox/generic/ and are available to all bins.
"""

DOMAIN = "social_media"
DESCRIPTION = "Building a following, monetizing content, or creating tools for the creator economy at scale."

AGENT_PACK = ['cross_platform_scheduler', 'viral_hook_generator', 'comment_engagement_bot', 'brand_deal_outreach', 'analytics_aggregator', 'audience_growth_tracker', 'content_repurposing', 'revenue_dashboard']

KEY_METRICS = ['follower_growth_rate', 'engagement_rate', 'views_per_post', 'revenue_per_1000_followers', 'brand_deal_close_rate', 'subscriber_churn', 'email_conversion_from_social']

COMMON_APIS = ['Instagram Graph API', 'TikTok for Developers API', 'YouTube Data API', 'LinkedIn API', 'Pinterest API', 'Buffer API', 'Patreon API', 'Beehiiv API', 'Metricool API']

REQUIRED_ENV = ['INSTAGRAM_ACCESS_TOKEN', 'YOUTUBE_API_KEY']
