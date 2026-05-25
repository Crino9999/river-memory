import logging
import sys
from config import LOG_LEVEL, LOG_FILE

_logger = None

def get_logger(name: str = "river") -> logging.Logger:
    global _logger
    if _logger is not None:
        return _logger.getChild(name) if name != "river" else _logger

    _logger = logging.getLogger("river")
    _logger.setLevel(getattr(logging, LOG_LEVEL.upper(), logging.INFO))
    _logger.propagate = False

    fmt = logging.Formatter(
        "%(asctime)s [%(levelname)s] %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    fh = logging.FileHandler(LOG_FILE, encoding="utf-8")
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(fmt)
    _logger.addHandler(fh)

    ch = logging.StreamHandler(sys.stdout)
    ch.setLevel(logging.INFO)
    ch.setFormatter(fmt)
    _logger.addHandler(ch)

    return _logger
