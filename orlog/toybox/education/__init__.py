"""
orlog/toybox/education/ — Building or operating tools that teach — from solo course creators to full LMS deployments.

Pre-seeded domain context for Orlog. When a user's goal maps to this domain,
Orlog loads this bin and knows immediately what agents, metrics, and APIs apply.

Cross-domain agents (notification, analytics, document, anomaly, crm) live in
toybox/generic/ and are available to all bins.
"""

DOMAIN = "education"
DESCRIPTION = "Building or operating tools that teach — from solo course creators to full LMS deployments."

AGENT_PACK = ['course_content_generator', 'learner_progress_tracker', 'at_risk_early_warning', 'adaptive_quiz_engine', 'cohort_engagement_monitor', 'certificate_issuer', 'report_generator', 'support_chatbot']

KEY_METRICS = ['course_completion_rate', 'quiz_pass_rate', 'learner_nps', 'daily_active_learners', 'time_to_mastery', 'cohort_retention', 'revenue_per_learner']

COMMON_APIS = ['Teachable API', 'Thinkific API', 'Moodle REST API', 'Stripe API', 'Zoom API', 'Loom API', 'Canvas LMS API']

REQUIRED_ENV = ['LMS_API_KEY', 'STRIPE_SECRET_KEY']
