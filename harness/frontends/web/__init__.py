"""
Web Frontend Package for Harness-Zero.
Provides the HTTP and SSE server, static assets, and dual-origin sandbox.
"""
from .server import HarnessWebServer

__all__ = ["HarnessWebServer"]
