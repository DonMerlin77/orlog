# Cross-Domain Patterns

These agents live in `generic/` and are reused across all bins.

| Agent | Used In |
|---|---|
| notification_sequencer | stores, events, education, healthcare, nonprofit |
| analytics_aggregator | every domain |
| document_drafter | legal, hr, research, content, creative_writing |
| anomaly_alert_agent | cybersecurity, water_env, manufacturing, finance |
| crm_sync_agent | real_estate, nonprofit, events, software, hr |

## High-fragmentation domains (connector layer matters most)
- **healthcare** — FHIR + dozens of EHRs, each slightly different
- **real_estate** — MLS + county data + AVM providers, no standard API
- **manufacturing** — SAP vs Oracle vs Dynamics, each with proprietary schema
- **agtech** — John Deere, Trimble, Bayer all use proprietary equipment APIs

## Domains where clarifying questions are most load-bearing
- **healthcare** — PHI/HIPAA = completely different architecture if yes
- **finance** — retail vs institutional = different APIs, different compliance
- **hardware** — prototype vs mass production = totally different tooling

## Total: 23 domains + generic = 24 bins
Generated from real 2026 API research. Each domain has real APIs, real metrics.

# Cross-domain agents (built once in generic/, reused everywhere):
# - notification_sequencer  — email/SMS flows (stores, events, education, healthcare, nonprofits)
# - analytics_aggregator    — pull from N sources, unify, report (every domain)
# - document_drafter        — context-injected doc generation (legal, HR, science, content)
# - anomaly_alert_agent     — threshold breach detection (cybersecurity, water, manufacturing, trading)
# - crm_sync_agent          — Salesforce/HubSpot bridge (real_estate, nonprofit, events, saas, hr)

