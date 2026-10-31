"""
orlog/toybox/events/ — Planning, running, and optimizing conferences, weddings, venue bookings, or hospitality operations.

Pre-seeded domain context for Orlog. When a user's goal maps to this domain,
Orlog loads this bin and knows immediately what agents, metrics, and APIs apply.

Cross-domain agents (notification, analytics, document, anomaly, crm) live in
toybox/generic/ and are available to all bins.
"""

DOMAIN = "events"
DESCRIPTION = "Planning, running, and optimizing conferences, weddings, venue bookings, or hospitality operations."

AGENT_PACK = ['registration_ticketing', 'venue_availability_checker', 'attendee_communication_sequencer', 'agenda_builder', 'speaker_vendor_coordinator', 'checkin_manager', 'survey_analyzer', 'logistics_tracker']

KEY_METRICS = ['registration_conversion_rate', 'no_show_rate', 'attendee_nps', 'revenue_per_attendee', 'sponsor_roi', 'checkin_throughput', 'repeat_booking_rate']

COMMON_APIS = ['Eventbrite API', 'Cvent API', 'Stripe API', 'Twilio API', 'Zoom API', 'Typeform API', 'Salesforce API', 'Google Maps API']

REQUIRED_ENV = ['EVENTBRITE_API_KEY', 'STRIPE_SECRET_KEY']
