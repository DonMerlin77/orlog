"""
Orlog — A self-improving AI operating system for any project.

Usage:
    from anthropic import Anthropic
    from orlog import Orlog

    project = Orlog(
        goal="Build a self-running Shopify store",
        api_key="your-anthropic-key"
    )

    project.brain.teach("listing", "Short headlines convert better")
    project.council.run()
    project.eval.run(task="council", n=3)

Toy box — domain agent libraries:
    from orlog.toybox.stores import ShopifyAgent, ListingAgent
    from orlog.toybox.game_dev import DesignAgent, CodeAgent
    from orlog.toybox.generic import ResearchAgent, WritingAgent
"""

from orlog.core import Orlog

__version__ = "0.1.0"
__all__ = ["Orlog"]
