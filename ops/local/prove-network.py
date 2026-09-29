#!/usr/bin/env python3
"""Local PROXY v2 and private IPv6 proofs using the shipped HAProxy frontend."""
import importlib.util
from pathlib import Path
import socket
import ssl
import struct
import tempfile
import time
import ipaddress

spec=importlib.util.spec_from_file_location('gates',Path(__file__).with_name('pg-lake-gates.py'))
g=importlib.util.module_from_spec(spec); spec.loader.exec_module(g)
ROOT=g.ROOT

def startup(sock):
    body=struct.pack('!I',196608)+b'user\x00postgres\x00database\x00warehouse\x00\x00'
    sock.sendall(struct.pack('!I',len(body)+4)+body)
    return sock.recv(4096)

def proxy(address,tls):
    sock=socket.create_connection(('127.0.0.1',15434),timeout=5)
    body=socket.inet_aton(address)+socket.inet_aton('192.0.2.1')+struct.pack('!HH',32100,5432)
    sock.sendall(b'\r\n\r\n\x00\r\nQUIT\n'+bytes([0x21,0x11])+struct.pack('!H',len(body))+body)
    if tls:
        sock.sendall(struct.pack('!II',8,80877103))
        try: response=sock.recv(1)
        except ConnectionResetError: response=b''
        if response!=b'S': sock.close(); return response
        context=ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        context.check_hostname=False; context.verify_mode=ssl.CERT_NONE
        sock=context.wrap_socket(sock,server_hostname='localhost')
    try: return startup(sock)
    finally: sock.close()

if __name__=='__main__':
    network='mdp-network-ipv6-proof'
    container='mdp-network-haproxy-proof'
    config=(ROOT/'ops/fly/haproxy/haproxy.cfg').read_text()
    # Change only local backend routing: the actual frontend/allowlist is unchanged.
    config=config.replace('server postgres mdp-postgres.internal:5432 check resolvers fly init-addr none resolve-prefer ipv6',
                          'server postgres mdp-pg-lake:5432 check')
    allowed=next(line.split()[0] for line in (ROOT/'ops/fly/haproxy/allow.lst').read_text().splitlines() if line and not line.startswith('#'))
    allowed=str(ipaddress.ip_network(allowed,strict=False).network_address)
    with tempfile.TemporaryDirectory(prefix='mdp-network-proxy-') as temp:
        Path(temp,'haproxy.cfg').write_text(config)
        Path(temp,'allow.lst').write_text((ROOT/'ops/fly/haproxy/allow.lst').read_text())
        try:
            g.run(['docker','run','-d','--name',container,'--network','local_default','-p','127.0.0.1:15434:5432',
                   '-v',temp+':/usr/local/etc/haproxy:ro','haproxy:3.2'])
            for _ in range(50):
                try:
                    result=proxy(allowed,True)
                    if result.startswith(b'R'): break
                except OSError: pass
                time.sleep(.1)
            assert result.startswith(b'R'),repr(result)
            assert proxy('192.0.2.99',True)==b''
            plain=proxy(allowed,False)
            assert b'pg_hba.conf rejects connection' in plain and b'no encryption' in plain,repr(plain)
            print('PASS PROXY v2: allowlisted forwarded source reaches TLS/auth; denied forwarded source disconnected')
            print('PASS frontend plaintext: pg_hba.conf rejects connection, no encryption')
            g.run(['docker','network','create','--ipv6','--subnet','fdaa:0:0:2c::/64',network])
            g.run(['docker','network','connect','--ip6','fdaa:0:0:2c::10',network,g.PG])
            code="""import socket,struct
s=socket.create_connection(('fdaa:0:0:2c::10',5432),timeout=5)
b=struct.pack('!I',196608)+b'user\\x00postgres\\x00database\\x00warehouse\\x00\\x00'
s.sendall(struct.pack('!I',len(b)+4)+b)
r=s.recv(4096)
assert b'pg_hba.conf rejects connection' in r and b'no encryption' in r,repr(r)
print('PASS private fdaa::/16 plaintext: pg_hba.conf rejects connection, no encryption')
"""
            print(g.run(['docker','run','--rm','--network',network,'--entrypoint','python3','mdp-postgres:local','-c',code]).stdout.strip())
        finally:
            g.run(['docker','rm','-f',container],check=False)
            g.run(['docker','network','disconnect',network,g.PG],check=False)
            g.run(['docker','network','rm',network],check=False)
