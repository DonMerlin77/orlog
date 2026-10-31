"""
orlog/toybox/stores/ — Online stores selling physical or digital goods via Shopify, WooCommerce, or custom storefronts.

Pre-seeded domain context for Orlog. When a user's goal maps to this domain,
Orlog loads this bin and knows immediately what agents, metrics, and APIs apply.

Cross-domain agents (notification, analytics, document, anomaly, crm) live in
toybox/generic/ and are available to all bins.
"""

DOMAIN = "stores"
DESCRIPTION = "Online stores selling physical or digital goods via Shopify, WooCommerce, or custom storefronts."

AGENT_PACK = ['product_description_writer', 'inventory_sync', 'abandoned_cart_email', 'dynamic_pricing', 'supplier_research', 'customer_support', 'review_aggregator', 'ad_performance_monitor']

KEY_METRICS = ['conversion_rate', 'average_order_value', 'customer_acquisition_cost', 'gross_margin_pct', 'cart_abandonment_rate', 'repeat_purchase_rate']

COMMON_APIS = ['Shopify Admin API', 'WooCommerce REST API', 'CJDropshipping API', 'Printful API', 'Klaviyo API', 'Meta Marketing API', 'Google Merchant Center API', 'ShipStation API', 'Judge.me']

REQUIRED_ENV = ['SHOPIFY_SHOP', 'SHOPIFY_CLIENT_ID', 'SHOPIFY_CLIENT_SECRET']
