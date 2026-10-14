from abc import ABC, abstractmethod
from datetime import datetime

from ..models import StrictModel, failure


class SchoolNotice(StrictModel):
    notice_id: str
    title: str
    content: str
    source_url: str
    published_at: datetime
    campus_id: str | None = None
    valid_until: datetime | None = None


class TimetableEntry(StrictModel):
    course_id: str
    title: str
    start: datetime
    end: datetime
    location: str
    source: str


class SchoolProvider(ABC):
    """Extension contract. Obtain official data access or support explicit file import.

    Never collect school passwords through the LLM. Public notices need source and timestamp;
    personal timetables require an authorized API or an independently implemented import path.
    """
    @abstractmethod
    async def search_notices(self, query: str, campus_id: str | None) -> list[SchoolNotice]: ...

    @abstractmethod
    async def list_timetable(self, user_id: str, start: datetime, end: datetime) -> list[TimetableEntry]: ...


def school_unavailable(capability: str):
    message = ('校园通知接口已预留，尚未配置学校官方通知来源。' if capability == 'notices' else
               '课表接口已预留，尚未接入学校授权接口或文件导入。')
    return failure('not_connected', message)
