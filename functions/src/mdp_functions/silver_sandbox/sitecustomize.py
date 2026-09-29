"""Installed before unpickling silver code in the disposable worker process."""

import _socket
import asyncio
import socket
import sys

import httpx
from mdp_functions.derived import (
    call_function,  # noqa: F401 - preload trusted runtime before sealing
)
from mdp_functions.errors import ServiceError

# asyncio needs its local wakeup socket; construct it before sealing the process.
asyncio.set_event_loop(asyncio.new_event_loop())


def blocked(*args, **kwargs):
    raise ServiceError("egress_blocked", "Silver process networking is disabled")


def audit(event, args):
    if event.startswith(
        ("socket.", "os.exec", "os.spawn", "os.posix_spawn")
    ) or event in {
        "subprocess.Popen",
        "os.system",
        "ctypes.dlopen",
        "ctypes.dlsym",
    }:
        blocked()


sys.addaudithook(audit)
socket.socket = blocked
_socket.socket = blocked
socket.create_connection = blocked
httpx.Client.send = blocked
httpx.AsyncClient.send = blocked
