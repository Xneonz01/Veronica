"""Tool servers: programs the user runs that offer her tools over the Model Context
Protocol (ADR 0003). Each runs as a child process over stdio, held in a Windows job
object so it can't outlive her, and each of its tools becomes a capability that asks
every time (`tools.py`)."""
