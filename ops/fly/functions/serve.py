"""Private deployment entrypoint: optional lifecycle probes and public collectors, normal runtime."""
import os
import httpx
import uvicorn
from mdp_functions.api import create_app
from mdp_functions.fixture_control import ControlledTransport, FixturePlan, PLANS
from mdp_functions.runs import Runtime
from mdp_functions.settings import Settings

class InterimTransport(ControlledTransport):
    def is_synthetic(self, request):
        return request.url.host == 'fixture.invalid'

    async def handle_async_request(self, request):
        if request.url.host == 'fixture.invalid':
            return await super().handle_async_request(request)
        async with httpx.AsyncHTTPTransport() as live:
            response = await live.handle_async_request(request)
            await response.aread()
            return response

def factory():
    settings = Settings()
    if os.environ.get("FLY_IMAGE_REF"):
        settings = settings.model_copy(update={"image_digest": os.environ["FLY_IMAGE_REF"]})
    if os.environ.get('MDP_FIXTURE_MODE') != '1':
        return create_app(settings)
    runtime = Runtime(settings)
    runtime.transport = InterimTransport(runtime.db)
    return create_app(settings, runtime=runtime)

if __name__ == '__main__':
    import socket
    # One dual-stack socket: 6PN callers arrive over IPv6, Fly's health check over IPv4.
    sock = socket.socket(socket.AF_INET6, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 0)
    sock.bind(('::', 8080))
    uvicorn.Server(uvicorn.Config(factory())).run(sockets=[sock])
