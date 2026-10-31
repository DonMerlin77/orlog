"""
orlog/toybox/research/ — Conducting academic or industry research — wet lab, computational, or field study.

Pre-seeded domain context for Orlog. When a user's goal maps to this domain,
Orlog loads this bin and knows immediately what agents, metrics, and APIs apply.

Cross-domain agents (notification, analytics, document, anomaly, crm) live in
toybox/generic/ and are available to all bins.
"""

DOMAIN = "research"
DESCRIPTION = "Conducting academic or industry research — wet lab, computational, or field study."

AGENT_PACK = ['literature_review_agent', 'experiment_data_logger', 'statistical_analysis_runner', 'citation_manager', 'grant_deadline_tracker', 'data_repository_publisher', 'lab_inventory_manager', 'manuscript_drafter']

KEY_METRICS = ['publications_per_year', 'citation_count', 'h_index', 'grant_funding_secured', 'experiment_replication_rate', 'time_from_hypothesis_to_publication']

COMMON_APIS = ['Semantic Scholar API', 'OpenAlex API', 'Crossref API', 'PubMed NCBI API', 'Benchling API', 'Zenodo API', 'OSF API']

REQUIRED_ENV = ['SEMANTIC_SCHOLAR_API_KEY']
