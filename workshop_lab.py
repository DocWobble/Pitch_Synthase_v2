"""Shim — RiboTome is the canonical server. This name is kept for backwards compatibility."""
from ribotome import *  # noqa: F401, F403
from ribotome import app, main

if __name__ == "__main__":
    main()
