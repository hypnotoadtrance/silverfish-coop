"""Exercise the host's friendly-fire setting with stock acid pools in one game pair."""

import argparse
import asyncio
from pathlib import Path

from live_portable_title_smoke import verify


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--work", type=Path, required=True)
    parser.add_argument("--first-game", type=Path, required=True)
    parser.add_argument("--second-game", type=Path, required=True)
    parser.add_argument("--vanilla-saved", type=Path, required=True)
    args = parser.parse_args()
    asyncio.run(verify(args.work.resolve(), args.first_game.resolve(), args.second_game.resolve(),
                       args.vanilla_saved.resolve(), stock_friendly_fire=True))
