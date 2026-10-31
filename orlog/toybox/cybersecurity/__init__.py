"""
orlog/toybox/cybersecurity/ — Protecting systems, networks, and data — from SOC operations to product security to compliance auditing.

Pre-seeded domain context for Orlog. When a user's goal maps to this domain,
Orlog loads this bin and knows immediately what agents, metrics, and APIs apply.

Cross-domain agents (notification, analytics, document, anomaly, crm) live in
toybox/generic/ and are available to all bins.
"""

DOMAIN = "cybersecurity"
DESCRIPTION = "Protecting systems, networks, and data — from SOC operations to product security to compliance auditing."

AGENT_PACK = ['threat_intelligence_ingestion', 'siem_log_analyzer', 'vulnerability_scanner_orchestrator', 'incident_response_runner', 'compliance_gap_assessor', 'phishing_simulation', 'patch_status_tracker', 'access_review']

KEY_METRICS = ['mean_time_to_detect', 'mean_time_to_respond', 'false_positive_rate', 'patch_compliance_pct', 'open_critical_cves', 'phishing_click_rate', 'security_incidents_per_quarter']

COMMON_APIS = ['Elastic Security API', 'Splunk API', 'SentinelOne API', 'CrowdStrike Falcon API', 'Qualys VMDR API', 'Shodan API', 'VirusTotal API', 'Okta API', 'PagerDuty API']

REQUIRED_ENV = ['CROWDSTRIKE_API_KEY', 'SPLUNK_API_KEY']
