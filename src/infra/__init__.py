"""Infrastructure layer for Lux Scholar.

Adapters that communicate with external systems (HTTP APIs, databases,
file systems, etc.) live here.  Business-logic code inside
``lux_scholar.core`` should depend on small abstract interfaces, not on
these concrete classes directly.

During migration we will gradually move:
    * infra.llm_service  -> infra/llm_service.py
    * infra.exa_client  -> infra/exa_client.py
    * infra.text_extractor -> infra/text_extractor.py
and any other I/O heavy helpers.
"""
