"""Palworld dedicated-server base camp dashboard.

palmon reads the server's own Level.sav and REST API and writes a single
status.json that the page in web/ renders. Start at cli.py for the entry
points, config.py for everything installation-specific, and status.py for
how one run is assembled.
"""

__version__ = "1.0.0"
