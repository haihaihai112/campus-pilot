from ..auth import role
from ..models import failure, success
from .calendar import CalendarService
from .maps import AmapProvider, CampusLocations
from .school import SchoolService, school_unavailable


READ_INTEGRATIONS = {'get_integration_status', 'search_places', 'get_route',
                     'get_weather_forecast', 'get_campus_location',
                     'list_external_calendar_events', 'search_school_notices', 'get_timetable'}


def register_integrations(mcp, config, campus):
    maps = AmapProvider(config)
    locations = CampusLocations(config)
    calendar = CalendarService(config, campus)
    school = SchoolService()

    @mcp.tool()
    def get_integration_status() -> dict:
        """Read configured integration status. Configured does not mean verified connectivity."""
        return success({'maps_weather': maps.status(), 'calendar': calendar.status(),
                        'school_notices': {'status': 'not_connected'},
                        'school_timetable': school.status(), 'school_housing': school.status()})

    @mcp.tool()
    async def search_places(keywords: str, city: str, limit: int = 5) -> dict:
        """Find real Amap POI candidates in an explicitly supplied city. Ask the user to disambiguate."""
        return await maps.search_places(keywords=keywords, city=city, limit=limit)

    @mcp.tool()
    async def get_route(origin: str, destination: str, mode: str = 'walking') -> dict:
        """Query walking/driving route using confirmed GCJ-02 longitude,latitude coordinates. Current estimate only."""
        return await maps.get_route(origin=origin, destination=destination, mode=mode)

    @mcp.tool()
    async def get_weather_forecast(city_adcode: str, target_date: str | None = None) -> dict:
        """Read actual available Amap forecasts. Never extrapolate dates outside the returned forecast window."""
        return await maps.weather(city_adcode=city_adcode, target_date=target_date)

    @mcp.tool()
    def get_campus_location(campus_id: str) -> dict:
        """Read operator-configured real campus gate; synthetic campuses have no default real coordinates."""
        return locations.get(campus_id)

    @mcp.tool()
    async def list_external_calendar_events(start: str, end: str) -> dict:
        """Read external calendar busy periods (ISO8601 with timezone, at most 31 days). Unconnected is NOT free time."""
        return await calendar.list_events(start, end)

    @mcp.tool()
    async def sync_plan_calendar(plan_id: str, plan_version: int, provider_binding: str,
                                 reminder_minutes: int, operation_id: str, user_id: str = 'demo-user') -> dict:
        """WRITE: sync a locally saved server-side plan only after explicit user confirmation; idempotent."""
        if role.get() != 'write':
            return failure('forbidden', '只读凭据不能同步外部日历')
        return await calendar.sync_plan(plan_id, plan_version, provider_binding,
                                        reminder_minutes, operation_id, user_id)

    @mcp.tool()
    def search_school_notices(query: str, campus_id: str | None = None) -> dict:
        """Reserved school notices adapter. Currently unconnected; does not return simulated notices."""
        return school_unavailable('notices')

    @mcp.tool()
    async def get_timetable(start_date: str, end_date: str, user_id: str = 'demo-user') -> dict:
        """Reserved personal timetable adapter. Requires an authorized school integration; currently unconnected."""
        return await school.timetable(user_id, start_date, end_date)

    @mcp.tool()
    async def get_dorm_assignment(user_id: str = 'demo-user') -> dict:
        """Read this account's authorized housing assignment. Unconnected is not an empty assignment."""
        return await school.housing(user_id)
