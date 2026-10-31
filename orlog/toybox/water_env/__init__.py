"""
orlog/toybox/water_env/ — Monitoring, managing, or improving water systems, environmental compliance, or climate infrastructure.

Pre-seeded domain context for Orlog. When a user's goal maps to this domain,
Orlog loads this bin and knows immediately what agents, metrics, and APIs apply.

Cross-domain agents (notification, analytics, document, anomaly, crm) live in
toybox/generic/ and are available to all bins.
"""

DOMAIN = "water_env"
DESCRIPTION = "Monitoring, managing, or improving water systems, environmental compliance, or climate infrastructure."

AGENT_PACK = ['iot_sensor_ingestion', 'anomaly_leak_detector', 'compliance_reporter', 'digital_twin_simulator', 'maintenance_work_order', 'energy_optimizer', 'water_quality_analyzer', 'public_dashboard_publisher']

KEY_METRICS = ['non_revenue_water_pct', 'contaminant_levels_vs_limits', 'leak_detection_response_time', 'energy_cost_per_cubic_meter', 'quality_complaint_rate', 'sensor_uptime_pct']

COMMON_APIS = ['ThingsLog IoT API', 'AWS IoT Core', 'EPA ECHO API', 'USGS Water Services API', 'OpenWeatherMap API', 'ArcGIS/Esri API', 'OSIsoft PI System API', 'Trimble Cityworks API']

REQUIRED_ENV = ['AWS_ACCESS_KEY_ID', 'ARCGIS_API_KEY']
