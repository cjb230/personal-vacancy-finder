from .ats import ATS_COLLECTORS
from .base import Collector
from .justjoinit import JustJoinItCollector
from .remote import REMOTE_COLLECTORS

COLLECTORS: list[Collector] = [*ATS_COLLECTORS, JustJoinItCollector(), *REMOTE_COLLECTORS]
