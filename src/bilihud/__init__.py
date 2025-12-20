"""Top-level package for bilihud."""

__author__ = """Locez"""
__email__ = 'locez@locez.com'
__version__ = '0.1.0'

# 为了确保能够找到vendor中的blivedm库，添加到sys.path
import sys
import os
_vendor_path = os.path.join(os.path.dirname(__file__), '..', '..', 'vendor', 'blivedm')
if _vendor_path not in sys.path:
    sys.path.insert(0, _vendor_path)
