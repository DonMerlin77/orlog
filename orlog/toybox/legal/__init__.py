"""
orlog/toybox/legal/ — Tools for law firms, legal departments, or compliance teams managing contracts, risk, and regulatory obligations.

Pre-seeded domain context for Orlog. When a user's goal maps to this domain,
Orlog loads this bin and knows immediately what agents, metrics, and APIs apply.

Cross-domain agents (notification, analytics, document, anomaly, crm) live in
toybox/generic/ and are available to all bins.
"""

DOMAIN = "legal"
DESCRIPTION = "Tools for law firms, legal departments, or compliance teams managing contracts, risk, and regulatory obligations."

AGENT_PACK = ['contract_review_agent', 'legal_research_agent', 'document_automation', 'regulatory_change_monitor', 'matter_tracker', 'esignature_orchestrator', 'billing_time_entry', 'deadline_calculator']

KEY_METRICS = ['contract_turnaround_time', 'clause_risk_score', 'compliance_coverage_pct', 'billable_hours_captured', 'nda_cycle_time']

COMMON_APIS = ['Ironclad API', 'DocuSign API', 'Westlaw API', 'Clio API', 'NetDocuments API', 'Relativity API', 'Microsoft Graph API']

REQUIRED_ENV = ['DOCUSIGN_API_KEY', 'CLIO_API_KEY']
