"""
orlog/toybox/video_cgi/ — Producing film, YouTube content, VFX, motion graphics, or AI-generated video.

Pre-seeded domain context for Orlog. When a user's goal maps to this domain,
Orlog loads this bin and knows immediately what agents, metrics, and APIs apply.

Cross-domain agents (notification, analytics, document, anomaly, crm) live in
toybox/generic/ and are available to all bins.
"""

DOMAIN = "video_cgi"
DESCRIPTION = "Producing film, YouTube content, VFX, motion graphics, or AI-generated video."

AGENT_PACK = ['script_to_storyboard', 'asset_version_manager', 'render_scheduler', 'ai_video_enhancer', 'subtitle_generator', 'thumbnail_ab_tester', 'youtube_metadata_optimizer', 'vfx_shot_tracker']

KEY_METRICS = ['render_time_per_frame', 'views', 'audience_retention_pct', 'click_through_rate', 'subscriber_growth', 'cost_per_minute_of_content']

COMMON_APIS = ['YouTube Data API', 'Runway ML API', 'ElevenLabs API', 'Stability AI API', 'Frame.io API', 'Dolby.io', 'Replicate API', 'DaVinci Resolve scripting API']

REQUIRED_ENV = ['YOUTUBE_API_KEY', 'RUNWAY_API_KEY']
