"""
orlog/toybox/manufacturing/ — Running or optimizing physical production, logistics, and end-to-end supply chain operations.

Pre-seeded domain context for Orlog. When a user's goal maps to this domain,
Orlog loads this bin and knows immediately what agents, metrics, and APIs apply.

Cross-domain agents (notification, analytics, document, anomaly, crm) live in
toybox/generic/ and are available to all bins.
"""

DOMAIN = "manufacturing"
DESCRIPTION = "Running or optimizing physical production, logistics, and end-to-end supply chain operations."

AGENT_PACK = ['demand_forecasting', 'supplier_lead_time_monitor', 'production_scheduler', 'defect_classifier', 'inventory_reorder', 'shipment_tracker', 'erp_sync', 'predictive_maintenance']

KEY_METRICS = ['oee', 'on_time_delivery_rate', 'inventory_turnover', 'defect_rate_ppm', 'supplier_fill_rate', 'cogs', 'warehouse_pick_accuracy']

COMMON_APIS = ['SAP API', 'Oracle Fusion API', 'Microsoft Dynamics 365 API', 'Flexport API', 'ShipBob API', 'Project44 API', 'Katana MRP API', 'AWS IoT Core']

REQUIRED_ENV = ['ERP_API_KEY', 'FLEXPORT_API_KEY']
