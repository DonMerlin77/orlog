"""
orlog/toybox/hr/ — Recruiting, onboarding, employee development, payroll, and workforce analytics.

Pre-seeded domain context for Orlog. When a user's goal maps to this domain,
Orlog loads this bin and knows immediately what agents, metrics, and APIs apply.

Cross-domain agents (notification, analytics, document, anomaly, crm) live in
toybox/generic/ and are available to all bins.
"""

DOMAIN = "hr"
DESCRIPTION = "Recruiting, onboarding, employee development, payroll, and workforce analytics."

AGENT_PACK = ['job_description_writer', 'resume_screening_ranker', 'interview_scheduler', 'onboarding_automator', 'engagement_surveyor', 'performance_review_summarizer', 'attrition_risk_predictor', 'compensation_benchmarker']

KEY_METRICS = ['time_to_hire', 'offer_acceptance_rate', '90_day_retention', 'employee_nps', 'cost_per_hire', 'internal_promotion_rate', 'attrition_pct']

COMMON_APIS = ['Workday API', 'Greenhouse ATS API', 'BambooHR API', 'Rippling API', 'Deel API', 'Lattice API', 'Culture Amp API', 'LinkedIn Talent Solutions API', 'Calendly API']

REQUIRED_ENV = ['GREENHOUSE_API_KEY', 'BAMBOOHR_API_KEY']
