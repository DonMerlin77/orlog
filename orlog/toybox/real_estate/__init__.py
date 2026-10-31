"""
orlog/toybox/real_estate/ — Building tools for buying, selling, renting, managing, or investing in real estate.

Pre-seeded domain context for Orlog. When a user's goal maps to this domain,
Orlog loads this bin and knows immediately what agents, metrics, and APIs apply.

Cross-domain agents (notification, analytics, document, anomaly, crm) live in
toybox/generic/ and are available to all bins.
"""

DOMAIN = "real_estate"
DESCRIPTION = "Building tools for buying, selling, renting, managing, or investing in real estate."

AGENT_PACK = ['listing_scraper_analyzer', 'property_valuation_model', 'deal_underwriting_agent', 'lead_nurturing_crm', 'tenant_screening_assistant', 'rent_roll_tracker', 'market_comp_generator', 'maintenance_router']

KEY_METRICS = ['cap_rate', 'cash_on_cash_return', 'occupancy_rate', 'days_on_market', 'lead_to_close_conversion', 'arv', 'net_operating_income']

COMMON_APIS = ['HouseCanary API', 'Mashvisor API', 'Zillow Bridge API', 'ATTOM Data API', 'Google Maps API', 'Plaid API', 'DocuSign API', 'Buildium API']

REQUIRED_ENV = ['ATTOM_API_KEY', 'GOOGLE_MAPS_KEY']
