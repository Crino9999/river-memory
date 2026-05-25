"""River Memory Plugin for AstrBot

AstrBot plugin that integrates the River memory system for RP memory management.
Usage: Place this directory in AstrBot's plugins folder.
"""
from .plugin import register_plugin, RiverMemoryPlugin

__version__ = "0.2.0"
__all__ = ["register_plugin", "RiverMemoryPlugin"]
