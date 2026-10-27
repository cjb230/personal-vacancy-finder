from .ats import ATS_COLLECTORS
from .base import Collector
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
]
