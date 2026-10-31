"""
Orlog Toy Box — domain agent libraries.

Orlog core has no domain knowledge.
The toy box provides pre-built agents for specific domains.
Orlog assembles what it needs based on the project goal.

Bins:
    orlog.toybox.stores    — Shopify, Etsy, Amazon, WooCommerce
    orlog.toybox.game_dev  — design, code review, testing, asset pipeline
    orlog.toybox.video_cgi — production pipeline, review, render tracking
    orlog.toybox.music     — lesson planning, theory, practice tracking
    orlog.toybox.generic   — research, writing, project management

Usage:
    from orlog.toybox.stores import ShopifyAgentPack
    from orlog.toybox.game_dev import GameDevAgentPack

    project = Orlog(goal="Build a 3D platformer engine")
    project.load_pack(GameDevAgentPack)
"""
