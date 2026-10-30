from .ats import ATS_COLLECTORS
from .base import Collector
from .email_alerts import email_collectors
from .justjoinit import JustJoinItCollector
from .more_ats import MORE_ATS_COLLECTORS
from .page import PageCollector
from .remote import REMOTE_COLLECTORS

COLLECTORS: list[Collector] = [
    *ATS_COLLECTORS,
    *MORE_ATS_COLLECTORS,
    PageCollector(),
    JustJoinItCollector(),
    *REMOTE_COLLECTORS,
    *email_collectors(),  # only when IMAP_USER and IMAP_PASSWORD are set
]
