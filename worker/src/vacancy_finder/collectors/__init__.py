from .ats import ATS_COLLECTORS
from .base import Collector
from .justjoinit import JustJoinItCollector

COLLECTORS: list[Collector] = [*ATS_COLLECTORS, JustJoinItCollector()]
