"""Payment Provider Simulator: a fake external bank/gateway (ADR-0005).

Runs as its own service with its own database and secrets. It never imports VieWallet code
(``app``) and VieWallet never imports it; they talk over HTTP only.
"""
