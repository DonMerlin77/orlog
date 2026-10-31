"""
orlog/toybox/agtech/ — Using technology to improve crop yields, reduce input costs, and manage farm operations at scale.

Pre-seeded domain context for Orlog. When a user's goal maps to this domain,
Orlog loads this bin and knows immediately what agents, metrics, and APIs apply.

Cross-domain agents (notification, analytics, document, anomaly, crm) live in
toybox/generic/ and are available to all bins.
"""

DOMAIN = "agtech"
DESCRIPTION = "Using technology to improve crop yields, reduce input costs, and manage farm operations at scale."

AGENT_PACK = ['crop_health_analyzer', 'soil_moisture_monitor', 'irrigation_scheduler', 'pest_disease_warning', 'variable_rate_planner', 'weather_integration', 'yield_prediction', 'market_price_alert']

KEY_METRICS = ['yield_per_acre', 'input_cost_per_acre', 'water_use_efficiency', 'crop_loss_pct', 'pest_detection_rate', 'roi_on_precision_inputs', 'days_to_harvest_accuracy']

COMMON_APIS = ['Farmonaut API', 'Climate FieldView API', 'John Deere Operations Center API', 'Planet Labs API', 'OpenWeatherMap API', 'USDA NASS API', 'Trimble Agriculture API', 'AWS IoT Core']

REQUIRED_ENV = ['OPENWEATHERMAP_API_KEY', 'PLANET_API_KEY']
