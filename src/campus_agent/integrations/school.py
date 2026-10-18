"""School-neutral read-only contracts. No live school adapter is enabled here."""
from abc import ABC, abstractmethod
import asyncio
from datetime import date, datetime
from typing import Callable
from zoneinfo import ZoneInfo

from pydantic import AwareDatetime, Field, model_validator

from ..models import StrictModel, failure, success


class SchoolNotice(StrictModel):
    notice_id: str
    title: str
    content: str
    source_url: str
    published_at: datetime
    campus_id: str | None = None
    valid_until: datetime | None = None


class TimetableEntry(StrictModel):
    course_id: str = Field(min_length=1, max_length=100)
    title: str = Field(min_length=1, max_length=200)
    start: AwareDatetime
    end: AwareDatetime
    location: str = Field(min_length=1, max_length=200)
    source: str = Field(min_length=1, max_length=200)

    @model_validator(mode='after')
    def ordered(self):
        if self.end <= self.start:
            raise ValueError('课程结束必须晚于开始')
        return self


class StudentBinding(StrictModel):
    """Private, operator/SSO-verified mapping, never a client-supplied student ID."""
    user_id: str = Field(min_length=1, max_length=100)
    school_id: str = Field(min_length=1, max_length=100)
    subject_id: str = Field(min_length=1, max_length=100)
    authorization_ref: str = Field(min_length=1, max_length=200)


class SchoolSource(StrictModel):
    system: str = Field(min_length=1, max_length=100)
    record_version: str = Field(min_length=1, max_length=200)
    retrieved_at: AwareDatetime
    data_as_of: AwareDatetime


class DormAssignment(StrictModel):
    campus: str = Field(min_length=1, max_length=100)
    building: str = Field(min_length=1, max_length=100)
    room: str = Field(min_length=1, max_length=100)
    bed: str | None = Field(default=None, max_length=50)
    valid_from: date | None = None


class TimetableSnapshot(StrictModel):
    school_id: str
    subject_id: str
    source: SchoolSource
    entries: list[TimetableEntry] = Field(max_length=500)


class HousingSnapshot(StrictModel):
    school_id: str
    subject_id: str
    source: SchoolSource
    assignment: DormAssignment | None


class SchoolError(StrictModel):
    code: str
    message: str


class TimetableData(StrictModel):
    school_id: str
    source: SchoolSource
    entries: list[TimetableEntry]
    read_only: bool = True


class HousingData(StrictModel):
    school_id: str
    source: SchoolSource
    assignment: DormAssignment | None
    read_only: bool = True


class TimetableResponse(StrictModel):
    ok: bool
    data: TimetableData | None = None
    error: SchoolError | None = None


class HousingResponse(StrictModel):
    ok: bool
    data: HousingData | None = None
    error: SchoolError | None = None


class SchoolProvider(ABC):
    """Extension contract. Obtain official data access or support explicit file import.

    Never collect school passwords through the LLM. Public notices need source and timestamp;
    personal timetables require an authorized API or an independently implemented import path.
    """
    @abstractmethod
    async def search_notices(self, query: str, campus_id: str | None) -> list[SchoolNotice]: ...

    @abstractmethod
    async def list_timetable(self, identity: StudentBinding, start: date, end: date) -> TimetableSnapshot: ...

    @abstractmethod
    async def get_housing(self, identity: StudentBinding) -> HousingSnapshot: ...


class SchoolService:
    """Fail closed until BOTH an authorized adapter and identity resolver exist.

    The resolver is server-owned; account names alone never establish school identity.
    Provider and resolver injection are integration seams, not public configuration/API.
    """
    def __init__(self, provider: SchoolProvider | None = None,
                 resolve_binding: Callable[[str], StudentBinding | None] | None = None):
        self.provider = provider
        self.resolve_binding = resolve_binding

    def status(self):
        return {'status': 'configured' if self.provider and self.resolve_binding else 'not_connected',
                'read_only': True, 'connectivity_verified': False,
                'message': '课程与宿舍只读接口已预留，尚未完成学校授权和实际连接验证。'}

    def _identity(self, user_id):
        if not self.provider or not self.resolve_binding:
            return None, school_unavailable('personal')
        try:
            identity = self.resolve_binding(user_id)
            if not identity:
                return None, failure('identity_not_bound', '当前网站账号尚未绑定经授权的学校身份。')
            identity = StudentBinding.model_validate(identity)
            if identity.user_id != user_id:
                return None, failure('identity_not_bound', '当前网站账号尚未绑定经授权的学校身份。')
            return identity, None
        except Exception:
            return None, failure('identity_not_bound', '学校身份绑定暂不可用，请联系组织者核对。')

    async def _read(self, user_id, capability, start=None, end=None):
        identity, error = self._identity(user_id)
        if error:
            return error
        try:
            if capability == 'timetable':
                raw = await asyncio.wait_for(self.provider.list_timetable(identity, start, end), 10)
                snapshot_type = TimetableSnapshot
            else:
                raw = await asyncio.wait_for(self.provider.get_housing(identity), 10)
                snapshot_type = HousingSnapshot
            if isinstance(raw, snapshot_type):
                raw = raw.model_dump(mode='json')
            snapshot = snapshot_type.model_validate(raw)
            if (snapshot.school_id, snapshot.subject_id) != (identity.school_id, identity.subject_id):
                raise ValueError('School response owner mismatch')
            if capability == 'timetable' and any(
                not start <= item.start.astimezone(ZoneInfo('Asia/Shanghai')).date() <= end
                for item in snapshot.entries
            ):
                raise ValueError('School timetable out of range')
            data = snapshot.model_dump(mode='json', exclude={'subject_id'})
            data['read_only'] = True
            return success(data)
        except TimeoutError:
            return failure('unavailable', '学校数据服务响应超时，请稍后重试。')
        except Exception:
            # Never echo upstream response bodies, URLs, bindings or credentials.
            return failure('provider_error', '学校数据暂不可用或不符合接入约定，请联系组织者核对。')

    async def timetable(self, user_id: str, start_date: str, end_date: str):
        try:
            start, end = date.fromisoformat(start_date), date.fromisoformat(end_date)
            if not 0 <= (end - start).days <= 30:
                raise ValueError('Date range must cover 1 to 31 days inclusive')
        except (TypeError, ValueError):
            return failure('invalid_arguments', '课表查询须提供有效日期，范围为1至31天（含首尾日期）。')
        return await self._read(user_id, 'timetable', start, end)

    async def housing(self, user_id: str):
        return await self._read(user_id, 'housing')


def school_unavailable(capability: str):
    message = ('校园通知接口已预留，尚未配置学校官方通知来源。' if capability == 'notices' else
               '课程与宿舍接口已预留，尚未接入学校授权数据源。')
    return failure('not_connected', message)
