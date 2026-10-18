from mcp.server.fastmcp import FastMCP
from starlette.responses import JSONResponse

from .auth import TokenMiddleware, role
from .campus import Campus
from .config import Settings
from .knowledge import Knowledge
from .models import failure
from .integrations.tools import register_integrations


def create_mcp(kind: str, config: Settings):
    mcp = FastMCP(f'campus-{kind}', stateless_http=True, json_response=True,
                  host='0.0.0.0', transport_security=None)
    if kind == 'rag':
        knowledge = Knowledge(config)

        @mcp.tool()
        def search_knowledge(query: str, campus_id: str | None, as_of_date: str, top_k: int = 5,
                             school_id: str | None = None, student_type: str = 'undergraduate', cohort_year: int = 2026) -> dict:
            """Return verbatim campus evidence with source IDs; never generate an answer."""
            return knowledge.search(query=query, campus_id=campus_id, as_of_date=as_of_date, top_k=top_k,
                school_id=school_id, student_type=student_type, cohort_year=cohort_year)
    else:
        campus = Campus(config)
        register_integrations(mcp, config, campus)

        @mcp.tool()
        def get_service_info(campus_id: str, service_ids: list[str] | None = None,
                             start_date: str | None = None, end_date: str | None = None,
                             student_type: str = 'undergraduate', cohort_year: int = 2026) -> dict:
            """Read source-backed materials and rules. WHUT windows/durations are unknown; not an appointment service."""
            return campus.service_info(campus_id, service_ids, start_date, end_date, student_type, cohort_year)

        @mcp.tool()
        def get_travel_times(campus_id: str, location_ids: list[str] | None = None) -> dict:
            """Read available travel minutes. Empty means unknown; never invent a fallback."""
            return campus.travel(campus_id, location_ids)

        @mcp.tool()
        def list_todos(user_id: str = 'demo-user') -> dict:
            """Read saved personal todos and their version numbers."""
            return campus.todos(user_id)

        @mcp.tool()
        def list_calendar_events(user_id: str = 'demo-user', start_date: str | None = None,
                                 end_date: str | None = None, exclude_plan_id: str | None = None) -> dict:
            """Read existing calendar events for conflict checking."""
            return campus.events(user_id, start_date, end_date, exclude_plan_id)

        @mcp.tool()
        def apply_plan(snapshot: dict, operation_id: str) -> dict:
            """WRITE: atomically save an approved plan snapshot; requires writer credentials."""
            if role.get() != 'write':
                return failure('forbidden', '规划凭据只读，不能保存计划')
            return campus.apply(snapshot, operation_id)

        @mcp.tool()
        def create_personal_todo(title: str, operation_id: str, user_id: str = 'demo-user',
                                 due_date: str | None = None, start: str | None = None, end: str | None = None) -> dict:
            """WRITE: save an approved personal todo; no school evidence or appointment implied."""
            if role.get() != 'write':
                return failure('forbidden', '只读凭据不能创建待办')
            return campus.create_todo(title, operation_id, user_id, due_date, start, end)

        @mcp.tool()
        def update_todo_status(todo_id: str, status: str, expected_version: int,
                               operation_id: str, user_id: str = 'demo-user') -> dict:
            """WRITE: update a todo with optimistic locking and idempotency."""
            if role.get() != 'write':
                return failure('forbidden', '规划凭据只读，不能修改待办')
            return campus.update_todo(todo_id, status, expected_version, operation_id, user_id)

        @mcp.tool()
        def export_calendar(user_id: str = 'demo-user', start_date: str | None = None,
                            end_date: str | None = None) -> dict:
            """Export saved events as standard iCalendar text; does not send notifications."""
            return campus.export(user_id, start_date, end_date)

    @mcp.custom_route('/health', methods=['GET'])
    async def health(request):
        return JSONResponse({'status': 'ok', 'service': kind, 'protocol': 'MCP Streamable HTTP'})

    app = mcp.streamable_http_app()
    return TokenMiddleware(app, config.read_token, config.write_token if kind == 'campus' else None,
                           public_paths=('/health',))
