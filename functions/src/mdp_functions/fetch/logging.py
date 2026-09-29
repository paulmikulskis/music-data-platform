"""Configure transport logging before dispatch, when secrets are still on the wire."""

import logging
import re


class TransportFilter(logging.Filter):
    def filter(self, record):
        if record.levelno < logging.WARNING:
            return False
        text = record.getMessage()
        if re.search(r"(?i)headers?|cookie|authorization", text):
            text = "[redacted transport headers]"
        else:
            text = re.sub(r"https?://\S+", "[redacted URL]", text)
        record.msg, record.args = text, ()
        return True


def protect_transport_logs():
    names = {"httpx", "httpcore"} | {
        n
        for n in logging.root.manager.loggerDict
        if n.startswith(("httpx.", "httpcore."))
    }
    for name in names:
        logger = logging.getLogger(name)
        logger.setLevel(logging.WARNING)
        if not any(isinstance(f, TransportFilter) for f in logger.filters):
            logger.addFilter(TransportFilter())
