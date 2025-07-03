"""Infrastructure layer for Lux Scholar.

Adapters that communicate with external systems (HTTP APIs, databases,
file systems, etc.) live here.  Business-logic code inside
``lux_scholar.core`` should depend on small abstract interfaces, not on
these concrete classes directly.

During migration we will gradually move:
    * services.llm_client  -> infra/llm_service.py
    * services.exa_client  -> infra/exa_client.py
    * services.text_extractor -> infra/text_extractor.py
and any other I/O heavy helpers.
"""
