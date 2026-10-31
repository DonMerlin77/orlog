"""
orlog/toybox/hardware/ — Designing, prototyping, and shipping physical devices — robots, drones, embedded systems, H2O tech, IoT.

Pre-seeded domain context for Orlog. When a user's goal maps to this domain,
Orlog loads this bin and knows immediately what agents, metrics, and APIs apply.

Cross-domain agents (notification, analytics, document, anomaly, crm) live in
toybox/generic/ and are available to all bins.
"""

DOMAIN = "hardware"
DESCRIPTION = "Designing, prototyping, and shipping physical devices — robots, drones, embedded systems, H2O tech, IoT."

AGENT_PACK = ['bom_tracker', 'firmware_version_manager', 'iot_telemetry_ingestion', 'supplier_lead_time_monitor', 'compliance_tracker', 'field_failure_log_analyzer', 'ota_update_orchestrator', 'cad_change_notifier']

KEY_METRICS = ['yield_rate', 'mean_time_between_failures', 'firmware_adoption_rate', 'unit_cost_vs_target', 'time_to_first_prototype', 'defect_rate_at_qa']

COMMON_APIS = ['AWS IoT Core', 'Google Cloud IoT', 'Particle.io', 'Arduino cloud APIs', 'Onshape API', 'Octopart', 'ROS 2', 'PX4 ArduPilot API']

REQUIRED_ENV = ['AWS_ACCESS_KEY_ID', 'AWS_SECRET_ACCESS_KEY']
