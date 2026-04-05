from .lenta import parse_lenta
from .ria import parse_ria
from .bbc import parse_bbc
from .vnexpress import get_news as parse_vnexpress
from .tuoitre import get_news as parse_tuoitre
from .dantri import get_news as parse_dantri

__all__ = [
    "parse_lenta",
    "parse_ria",
    "parse_bbc",
    "parse_vnexpress",
    "parse_tuoitre",
    "parse_dantri",
]
