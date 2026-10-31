"""
orlog/toybox/creative_writing/ — Writing fiction, building narrative universes, developing games with rich lore, or producing transmedia projects.

Pre-seeded domain context for Orlog. When a user's goal maps to this domain,
Orlog loads this bin and knows immediately what agents, metrics, and APIs apply.

Cross-domain agents (notification, analytics, document, anomaly, crm) live in
toybox/generic/ and are available to all bins.
"""

DOMAIN = "creative_writing"
DESCRIPTION = "Writing fiction, building narrative universes, developing games with rich lore, or producing transmedia projects."

AGENT_PACK = ['story_bible_manager', 'chapter_drafter', 'character_consistency_enforcer', 'plot_hole_detector', 'query_letter_generator', 'self_publishing_formatter', 'beta_reader_synthesizer', 'series_arc_planner']

KEY_METRICS = ['words_per_day', 'chapter_completion_rate', 'continuity_error_count', 'beta_reader_score', 'amazon_review_rating', 'book_to_book_conversion']

COMMON_APIS = ['Sudowrite API', 'NovelCrafter API', 'Anthropic API', 'Amazon KDP API', 'ProWritingAid API', 'Canva API', 'Draft2Digital API']

REQUIRED_ENV = ['ANTHROPIC_API_KEY']
