FROM haproxy:3.2
COPY ops/fly/haproxy/haproxy.cfg ops/fly/haproxy/allow.lst /usr/local/etc/haproxy/
COPY --chmod=755 ops/fly/haproxy/start.sh /usr/local/bin/mdp-haproxy
CMD ["/usr/local/bin/mdp-haproxy"]
