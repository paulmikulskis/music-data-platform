"""Keep source code on the traced transport, including work moved to a thread."""

import socket
import sys
import threading
from builtins import BaseExceptionGroup
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from contextvars import ContextVar, copy_context
from functools import wraps

from mdp_functions.errors import ServiceError

DOC = "docs/operating.md#what-the-platform-refuses"
_active = ContextVar("mdp_source_context", default=None)
_transport = ContextVar("mdp_transport_socket", default=False)
_installed = False


def source_layer(default=None):
    ctx = _active.get()
    return ctx.manifest.layer if ctx is not None else default


def audit(event, args):
    ctx = _active.get()
    if (
        ctx is not None
        and not _transport.get()
        and event
        in {
            "socket.__new__",
            "socket.connect",
            "socket.sendto",
            "socket.getaddrinfo",
        }
    ):
        ctx.request_id = None
        raise ServiceError(
            "egress_blocked",
            f"Own network clients are refused because requests need lineage; use ctx.http ({DOC}).",
        )


@contextmanager
def source_network(ctx):
    global _installed
    if not _installed:
        sys.addaudithook(audit)
        # A client may hold a socket before the source starts. Writes still need the transport.
        for name in ("send", "sendall", "sendto", "sendmsg", "sendfile"):
            original = getattr(socket.socket, name, None)
            if original is not None:

                def guarded(method):
                    @wraps(method)
                    def call(*args, **kwargs):
                        # asyncio wakes its loop after a worker thread completes; this is not egress.
                        caller = sys._getframe(1)
                        wakeup = (
                            caller.f_globals.get("__name__")
                            == "asyncio.selector_events"
                            and caller.f_code.co_name == "_write_to_self"
                        )
                        if not wakeup:
                            audit("socket.connect", ())
                        return method(*args, **kwargs)

                    return call

                setattr(socket.socket, name, guarded(original))
        start = threading.Thread.start

        @wraps(start)
        def start_with_context(thread, *args, **kwargs):
            if _active.get() is not None and not _transport.get():
                context, run = copy_context(), thread.run
                thread.run = lambda: context.run(run)
            return start(thread, *args, **kwargs)

        threading.Thread.start = start_with_context
        submit = ThreadPoolExecutor.submit

        @wraps(submit)
        def submit_with_context(pool, function, /, *args, **kwargs):
            context = copy_context()
            # Workers may outlive this source. Only the submitted job owns its context;
            # Context.run restores the worker's context even when that job raises.
            active_token = _active.set(None)
            transport_token = _transport.set(False)
            try:
                return submit(pool, context.run, function, *args, **kwargs)
            finally:
                _transport.reset(transport_token)
                _active.reset(active_token)

        ThreadPoolExecutor.submit = submit_with_context
        _installed = True
    token = _active.set(ctx)
    try:
        yield
    except BaseExceptionGroup as exc:

        def refused(group):
            for error in group.exceptions:
                if (
                    isinstance(error, ServiceError)
                    and error.error_class == "egress_blocked"
                ):
                    return error
                if isinstance(error, BaseExceptionGroup) and (found := refused(error)):
                    return found
            return None

        if error := refused(exc):
            raise error from exc
        raise
    finally:
        _active.reset(token)


@contextmanager
def transport_network():
    token = _transport.set(True)
    try:
        yield
    finally:
        _transport.reset(token)
