"""
orlog/toybox/music/ — Creating, producing, and distributing music, podcasts, or audio experiences.

Pre-seeded domain context for Orlog. When a user's goal maps to this domain,
Orlog loads this bin and knows immediately what agents, metrics, and APIs apply.

Cross-domain agents (notification, analytics, document, anomaly, crm) live in
toybox/generic/ and are available to all bins.
"""

DOMAIN = "music"
DESCRIPTION = "Creating, producing, and distributing music, podcasts, or audio experiences."

AGENT_PACK = ['lyrics_melody_generator', 'mastering_assistant', 'distribution_submitter', 'playlist_pitch_tracker', 'sync_licensing_finder', 'royalty_tracker', 'social_clip_generator', 'fan_engagement_monitor']

KEY_METRICS = ['monthly_listeners', 'stream_count', 'save_rate', 'playlist_adds', 'royalty_revenue_per_stream', 'follower_growth']

COMMON_APIS = ['Spotify Web API', 'Apple Music API', 'SoundCloud API', 'DistroKid API', 'LANDR API', 'ElevenLabs API', 'Soundcharts API', 'YouTube Content ID API']

REQUIRED_ENV = ['SPOTIFY_CLIENT_ID', 'SPOTIFY_CLIENT_SECRET']
