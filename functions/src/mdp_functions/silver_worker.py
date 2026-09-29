"""Trusted runtime IPC only. Never load this pickle surface from an HTTP request."""

import asyncio
import sys
from contextlib import redirect_stdout

import cloudpickle

from mdp_functions.derived import call_function


def main():
    loop = asyncio.get_event_loop()

    try:
        ctx, rows = cloudpickle.loads(sys.stdin.buffer.read())
        with redirect_stdout(sys.stderr):
            for row in rows:
                ctx.input_row = row
                ctx.observed(1)
                loop.run_until_complete(call_function(ctx, [row]))
        result = {
            key: getattr(ctx, key)
            for key in ("outputs", "rejected", "observed_count", "yielded_count")
        }
    except BaseException as exc:  # noqa: BLE001 - subprocess boundary returns a durable failure
        result = {
            "error_class": getattr(exc, "error_class", "function_failed"),
            "message": str(exc),
        }
    finally:
        loop.close()
    sys.stdout.buffer.write(cloudpickle.dumps(result))


if __name__ == "__main__":
    main()
