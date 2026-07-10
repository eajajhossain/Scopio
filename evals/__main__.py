import asyncio
import sys

from evals.run import main

if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
