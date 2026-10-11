"""The network, behind adapters (ADR 0003): web search providers and the page fetcher.

Nothing else in the backend opens a connection to the internet. Each request is
bounded in size and time, and what comes back is untrusted text.
"""
