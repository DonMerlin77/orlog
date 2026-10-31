"""
orlog/toybox/healthcare/ — Clinical, research, or consumer health applications touching patient data, diagnostics, or drug development.

Pre-seeded domain context for Orlog. When a user's goal maps to this domain,
Orlog loads this bin and knows immediately what agents, metrics, and APIs apply.

Cross-domain agents (notification, analytics, document, anomaly, crm) live in
toybox/generic/ and are available to all bins.
"""

# WARNING: HIPAA compliance required if handling PHI. Verify before building.

DOMAIN = "healthcare"
DESCRIPTION = "Clinical, research, or consumer health applications touching patient data, diagnostics, or drug development."

AGENT_PACK = ['fhir_data_ingestion', 'clinical_trial_tracker', 'adverse_event_monitor', 'patient_engagement_messenger', 'drug_interaction_checker', 'claims_processor', 'risk_stratifier', 'regulatory_submission_preparer']

KEY_METRICS = ['patient_outcomes_score', 'readmission_rate', 'medication_adherence_pct', 'trial_enrollment_rate', 'cost_per_qaly', 'time_to_diagnosis']

COMMON_APIS = ['HL7 FHIR R4 API', 'CMS BlueButton API', 'NIH Clinical Trials API', 'FDA OpenFDA API', 'Twilio', 'AWS HealthLake', 'Google Cloud Healthcare API']

REQUIRED_ENV = ['FHIR_ENDPOINT', 'FHIR_API_KEY']
