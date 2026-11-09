import ctypes
import ctypes.util
import sys

_M_ARENA_MAX = -8
_libc = None


def _glibc():
    global _libc
    if _libc is None and sys.platform.startswith("linux"):
        try:
            _libc = ctypes.CDLL(ctypes.util.find_library("c") or "libc.so.6")
            _libc.malloc_trim
        except (OSError, AttributeError):
            _libc = False
    return _libc or None


def tune(arenas: int = 2) -> bool:
    libc = _glibc()
    return bool(libc and libc.mallopt(_M_ARENA_MAX, arenas))


def trim() -> bool:
    libc = _glibc()
    return bool(libc and libc.malloc_trim(0))
