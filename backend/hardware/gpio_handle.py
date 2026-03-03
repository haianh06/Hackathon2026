#!/usr/bin/env python3
"""
GPIO Handle Manager for Raspberry Pi 5
Shares a single lgpio chip handle across all modules.
"""

import logging

logger = logging.getLogger('gpio_handle')

_h = None

def gpio_open(chip=0):
    """Open gpiochip and return handle (shared singleton)"""
    global _h
    if _h is None:
        try:
            import lgpio
            _h = lgpio.gpiochip_open(chip)
            logger.info(f"GPIO chip {chip} opened (handle={_h})")
        except Exception as e:
            logger.error(f"Failed to open gpiochip {chip}: {e}")
            _h = None
    return _h

def gpio_close():
    """Close the shared GPIO handle"""
    global _h
    if _h is not None:
        try:
            import lgpio
            lgpio.gpiochip_close(_h)
            logger.info("GPIO handle closed")
        except Exception as e:
            logger.warning(f"Error closing GPIO: {e}")
        _h = None
