import logging
import os

from mcp.server.sse import SseServerTransport
from mcp.server.streamable_http import StreamableHTTPServerTransport
from mcp.server import Server
from mcp import types

from .tools import (
    set_context,
    list_issues,
    get_issue,
    create_issue,
    update_issue,
    delete_issue,
    checkout_issue,
    release_issue,
    list_comments,
    create_comment,
    list_agents,
    get_agent,
    get_current_agent,
    create_agent_hire,
    create_agent,
    list_approvals,
    get_approval,
    approve_approval,
    reject_approval,
    list_projects,
    get_company,
    list_goals,
    get_goal,
)

log = logging.getLogger(__name__)

server = Server("paperclip-mcp")
sse = SseServerTransport("/messages/")

_BEARER_PREFIX = "Bearer "


def _check_auth(scope):
    token = os.environ.get("MCP_BEARER_TOKEN", "")
    if not token:
        return True
    headers = {}
    for key, value in scope.get("headers", []):
        headers[key.decode()] = value.decode()
    auth_header = headers.get("authorization", "")
    if not auth_header.startswith(_BEARER_PREFIX):
        return False
    return auth_header[len(_BEARER_PREFIX):] == token


def _extract_context(scope):
    headers = {}
    for key, value in scope.get("headers", []):
        headers[key.decode().lower()] = value.decode()
    api_key = headers.get("x-paperclip-api-key", "")
    company_id = headers.get("x-paperclip-company-id", "")
    agent_id = headers.get("x-paperclip-agent-id", "")
    set_context(api_key=api_key, company_id=company_id, agent_id=agent_id)


@server.list_tools()
async def list_tools():
    return [
        types.Tool(
            name="paperclip_list_issues",
            description="List issues in the company. Filter by status, assignee, or project. Returns array of issue objects with id, identifier, title, status, priority, assigneeAgentId.",
            inputSchema={
                "type": "object",
                "properties": {
                    "status": {"type": "string", "description": "Comma-separated statuses: backlog, todo, in_progress, in_review, done, blocked, cancelled"},
                    "assigneeAgentId": {"type": "string", "description": "Filter by assignee agent UUID"},
                    "projectId": {"type": "string", "description": "Filter by project UUID"},
                    "parentId": {"type": "string", "description": "Filter by parent issue UUID"},
                },
            },
        ),
        types.Tool(
            name="paperclip_get_issue",
            description="Get full details of an issue by ID or identifier (e.g. HWQAA-1). Returns issue with description, status, comments, parent chain, project, and goal.",
            inputSchema={
                "type": "object",
                "properties": {
                    "issueId": {"type": "string", "description": "Issue UUID or identifier (e.g. HWQAA-1)"},
                },
                "required": ["issueId"],
            },
        ),
        types.Tool(
            name="paperclip_create_issue",
            description="Create a new issue in the company. Requires title. Optionally set description, status, priority, assignee, project, and parent.",
            inputSchema={
                "type": "object",
                "properties": {
                    "title": {"type": "string", "description": "Issue title"},
                    "description": {"type": "string", "description": "Issue description (markdown)"},
                    "status": {"type": "string", "description": "Initial status (default: backlog)", "default": "backlog"},
                    "priority": {"type": "string", "description": "Priority: critical, high, medium, low", "default": "medium"},
                    "assigneeAgentId": {"type": "string", "description": "UUID of agent to assign"},
                    "projectId": {"type": "string", "description": "Project UUID"},
                    "parentId": {"type": "string", "description": "Parent issue UUID (for sub-issues)"},
                },
                "required": ["title"],
            },
        ),
        types.Tool(
            name="paperclip_update_issue",
            description="Update an existing issue. Can change status, priority, assignee, description. Optionally add a comment atomically with the update.",
            inputSchema={
                "type": "object",
                "properties": {
                    "issueId": {"type": "string", "description": "Issue UUID or identifier"},
                    "status": {"type": "string", "description": "New status: backlog, todo, in_progress, in_review, done, blocked, cancelled"},
                    "priority": {"type": "string", "description": "New priority: critical, high, medium, low"},
                    "assigneeAgentId": {"type": "string", "description": "UUID of agent to assign (or null to unassign)"},
                    "description": {"type": "string", "description": "New description (markdown)"},
                    "comment": {"type": "string", "description": "Comment to add atomically with this update"},
                },
                "required": ["issueId"],
            },
        ),
        types.Tool(
            name="paperclip_delete_issue",
            description="Delete an issue by ID or identifier.",
            inputSchema={
                "type": "object",
                "properties": {
                    "issueId": {"type": "string", "description": "Issue UUID or identifier"},
                },
                "required": ["issueId"],
            },
        ),
        types.Tool(
            name="paperclip_checkout_issue",
            description="Checkout (claim) an issue for work. Sets status to in_progress and assigns to the current agent. The agent must provide expected current statuses for optimistic concurrency.",
            inputSchema={
                "type": "object",
                "properties": {
                    "issueId": {"type": "string", "description": "Issue UUID or identifier"},
                    "expectedStatuses": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Expected current statuses (e.g. [\"todo\", \"backlog\"])",
                        "default": ["todo", "backlog"],
                    },
                },
                "required": ["issueId"],
            },
        ),
        types.Tool(
            name="paperclip_release_issue",
            description="Release (unclaim) an issue. Removes the checkout, setting the issue back to unassigned.",
            inputSchema={
                "type": "object",
                "properties": {
                    "issueId": {"type": "string", "description": "Issue UUID or identifier"},
                },
                "required": ["issueId"],
            },
        ),
        types.Tool(
            name="paperclip_list_comments",
            description="List comments on an issue, newest first.",
            inputSchema={
                "type": "object",
                "properties": {
                    "issueId": {"type": "string", "description": "Issue UUID or identifier"},
                    "limit": {"type": "integer", "description": "Max comments to return (default 50)", "default": 50},
                },
                "required": ["issueId"],
            },
        ),
        types.Tool(
            name="paperclip_create_comment",
            description="Add a comment to an issue.",
            inputSchema={
                "type": "object",
                "properties": {
                    "issueId": {"type": "string", "description": "Issue UUID or identifier"},
                    "body": {"type": "string", "description": "Comment body (markdown)"},
                },
                "required": ["issueId", "body"],
            },
        ),
        types.Tool(
            name="paperclip_list_agents",
            description="List all agents in the company. Returns id, name, roleKey, status for each agent.",
            inputSchema={
                "type": "object",
                "properties": {},
            },
        ),
        types.Tool(
            name="paperclip_get_agent",
            description="Get details of a specific agent by UUID, or 'me' for the current agent.",
            inputSchema={
                "type": "object",
                "properties": {
                    "agentId": {"type": "string", "description": "Agent UUID or 'me' for current agent"},
                },
                "required": ["agentId"],
            },
        ),
        types.Tool(
            name="paperclip_get_current_agent",
            description="Get the current agent's full details including permissions (canCreateAgents, canAssignTasks), role, and chain of command.",
            inputSchema={
                "type": "object",
                "properties": {},
            },
        ),
        types.Tool(
            name="paperclip_list_projects",
            description="List all projects in the company with their status and details.",
            inputSchema={
                "type": "object",
                "properties": {},
            },
        ),
        types.Tool(
            name="paperclip_get_company",
            description="Get the current company details including name, issue prefix, and settings.",
            inputSchema={
                "type": "object",
                "properties": {},
            },
        ),
        types.Tool(
            name="paperclip_list_goals",
            description="List all goals in the company.",
            inputSchema={
                "type": "object",
                "properties": {},
            },
        ),
        types.Tool(
            name="paperclip_get_goal",
            description="Get details of a specific goal.",
            inputSchema={
                "type": "object",
                "properties": {
                    "goalId": {"type": "string", "description": "Goal UUID"},
                },
                "required": ["goalId"],
            },
        ),
        types.Tool(
            name="paperclip_create_agent_hire",
            description="Request to hire (create) a new agent. If company requires board approval, the agent will be created with 'pending_approval' status and an approval request will be generated. Otherwise the agent is created immediately. Requires canCreateAgents permission.",
            inputSchema={
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "Agent display name"},
                    "adapterType": {"type": "string", "description": "Adapter type (e.g. hermes_local, claude_local)"},
                    "role": {"type": "string", "description": "Role: ceo, cto, cmo, cfo, engineer, designer, pm, qa, devops, researcher, general (default: general)"},
                    "title": {"type": "string", "description": "Job title (e.g. 'Research Engineer')"},
                    "icon": {"type": "string", "description": "Icon name (e.g. brain, code, terminal, rocket)"},
                    "reportsTo": {"type": "string", "description": "UUID of manager agent"},
                    "capabilities": {"type": "string", "description": "Description of agent capabilities"},
                    "adapterConfig": {"type": "object", "description": "Adapter-specific configuration (e.g. model, provider, mcpServers)"},
                    "runtimeConfig": {"type": "object", "description": "Runtime configuration (secrets, env vars)"},
                    "permissions": {"type": "object", "description": "Permissions like {canCreateAgents: true, canAssignTasks: true}"},
                    "desiredSkills": {"type": "array", "items": {"type": "string"}, "description": "List of desired skills"},
                    "sourceIssueIds": {"type": "array", "items": {"type": "string"}, "description": "Issue UUIDs to link to this hire request"},
                    "metadata": {"type": "object", "description": "Additional metadata"},
                },
                "required": ["name", "adapterType"],
            },
        ),
        types.Tool(
            name="paperclip_create_agent",
            description="Directly create a new agent (board-only, no approval flow). The agent is created immediately with 'idle' status.",
            inputSchema={
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "Agent display name"},
                    "adapterType": {"type": "string", "description": "Adapter type (e.g. hermes_local, claude_local)"},
                    "role": {"type": "string", "description": "Role: ceo, cto, cmo, cfo, engineer, designer, pm, qa, devops, researcher, general"},
                    "title": {"type": "string", "description": "Job title"},
                    "icon": {"type": "string", "description": "Icon name"},
                    "reportsTo": {"type": "string", "description": "UUID of manager agent"},
                    "capabilities": {"type": "string", "description": "Capabilities description"},
                    "adapterConfig": {"type": "object", "description": "Adapter configuration"},
                    "runtimeConfig": {"type": "object", "description": "Runtime configuration"},
                    "permissions": {"type": "object", "description": "Permissions"},
                    "desiredSkills": {"type": "array", "items": {"type": "string"}, "description": "Desired skills"},
                    "metadata": {"type": "object", "description": "Additional metadata"},
                },
                "required": ["name", "adapterType"],
            },
        ),
        types.Tool(
            name="paperclip_list_approvals",
            description="List approval requests in the company. Filter by status: pending, approved, rejected.",
            inputSchema={
                "type": "object",
                "properties": {
                    "status": {"type": "string", "description": "Filter by status: pending, approved, rejected"},
                },
            },
        ),
        types.Tool(
            name="paperclip_get_approval",
            description="Get details of a specific approval request.",
            inputSchema={
                "type": "object",
                "properties": {
                    "approvalId": {"type": "string", "description": "Approval UUID"},
                },
                "required": ["approvalId"],
            },
        ),
        types.Tool(
            name="paperclip_approve_approval",
            description="Approve a pending approval request (e.g. agent hire). Board-only action.",
            inputSchema={
                "type": "object",
                "properties": {
                    "approvalId": {"type": "string", "description": "Approval UUID"},
                },
                "required": ["approvalId"],
            },
        ),
        types.Tool(
            name="paperclip_reject_approval",
            description="Reject a pending approval request. Board-only action.",
            inputSchema={
                "type": "object",
                "properties": {
                    "approvalId": {"type": "string", "description": "Approval UUID"},
                    "reason": {"type": "string", "description": "Reason for rejection"},
                },
                "required": ["approvalId"],
            },
        ),
    ]


@server.call_tool()
async def call_tool(name: str, arguments: dict):
    try:
        result = await _dispatch(name, arguments)
        return [types.TextContent(type="text", text=str(result))]
    except Exception as e:
        log.exception("tool %s failed", name)
        return [types.TextContent(type="text", text=f"Error: {e}")]


async def _dispatch(name: str, args: dict):
    if name == "paperclip_list_issues":
        return await list_issues(
            status=args.get("status"),
            assigneeAgentId=args.get("assigneeAgentId"),
            projectId=args.get("projectId"),
            parentId=args.get("parentId"),
        )
    elif name == "paperclip_get_issue":
        return await get_issue(args["issueId"])
    elif name == "paperclip_create_issue":
        return await create_issue(
            title=args["title"],
            description=args.get("description"),
            status=args.get("status"),
            priority=args.get("priority"),
            assigneeAgentId=args.get("assigneeAgentId"),
            projectId=args.get("projectId"),
            parentId=args.get("parentId"),
        )
    elif name == "paperclip_update_issue":
        return await update_issue(
            issueId=args["issueId"],
            status=args.get("status"),
            priority=args.get("priority"),
            assigneeAgentId=args.get("assigneeAgentId"),
            description=args.get("description"),
            comment=args.get("comment"),
        )
    elif name == "paperclip_delete_issue":
        return await delete_issue(args["issueId"])
    elif name == "paperclip_checkout_issue":
        return await checkout_issue(
            issueId=args["issueId"],
            expectedStatuses=args.get("expectedStatuses", ["todo", "backlog"]),
        )
    elif name == "paperclip_release_issue":
        return await release_issue(args["issueId"])
    elif name == "paperclip_list_comments":
        return await list_comments(args["issueId"], args.get("limit", 50))
    elif name == "paperclip_create_comment":
        return await create_comment(args["issueId"], args["body"])
    elif name == "paperclip_list_agents":
        return await list_agents()
    elif name == "paperclip_get_agent":
        return await get_agent(args["agentId"])
    elif name == "paperclip_get_current_agent":
        return await get_current_agent()
    elif name == "paperclip_list_projects":
        return await list_projects()
    elif name == "paperclip_get_company":
        return await get_company()
    elif name == "paperclip_list_goals":
        return await list_goals()
    elif name == "paperclip_get_goal":
        return await get_goal(args["goalId"])
    elif name == "paperclip_create_agent_hire":
        return await create_agent_hire(
            name=args["name"],
            adapterType=args["adapterType"],
            role=args.get("role"),
            title=args.get("title"),
            icon=args.get("icon"),
            reportsTo=args.get("reportsTo"),
            capabilities=args.get("capabilities"),
            adapterConfig=args.get("adapterConfig"),
            runtimeConfig=args.get("runtimeConfig"),
            budgetMonthlyCents=args.get("budgetMonthlyCents"),
            permissions=args.get("permissions"),
            desiredSkills=args.get("desiredSkills"),
            sourceIssueIds=args.get("sourceIssueIds"),
            metadata=args.get("metadata"),
        )
    elif name == "paperclip_create_agent":
        return await create_agent(
            name=args["name"],
            adapterType=args["adapterType"],
            role=args.get("role"),
            title=args.get("title"),
            icon=args.get("icon"),
            reportsTo=args.get("reportsTo"),
            capabilities=args.get("capabilities"),
            adapterConfig=args.get("adapterConfig"),
            runtimeConfig=args.get("runtimeConfig"),
            budgetMonthlyCents=args.get("budgetMonthlyCents"),
            permissions=args.get("permissions"),
            desiredSkills=args.get("desiredSkills"),
            metadata=args.get("metadata"),
        )
    elif name == "paperclip_list_approvals":
        return await list_approvals(status=args.get("status"))
    elif name == "paperclip_get_approval":
        return await get_approval(args["approvalId"])
    elif name == "paperclip_approve_approval":
        return await approve_approval(args["approvalId"])
    elif name == "paperclip_reject_approval":
        return await reject_approval(args["approvalId"], reason=args.get("reason"))
    else:
        return {"error": f"Unknown tool: {name}"}


async def _send_unauthorized(scope, receive, send):
    body = b'{"error":"unauthorized"}'
    await send({
        "type": "http.response.start",
        "status": 401,
        "headers": [[b"content-type", b"application/json"], [b"content-length", str(len(body)).encode()]],
    })
    await send({"type": "http.response.body", "body": body})


_http_transport = StreamableHTTPServerTransport(
    mcp_session_id=None,
    is_json_response_enabled=True,
)

import asyncio

async def _run_http_server():
    async with _http_transport.connect() as streams:
        await server.run(
            streams[0], streams[1], server.create_initialization_options()
        )

_http_task = None

async def _ensure_http_server():
    global _http_task
    if _http_task is None:
        _http_task = asyncio.ensure_future(_run_http_server())
        await asyncio.sleep(0.1)


async def app(scope, receive, send):
    if scope["type"] != "http":
        return

    path = scope.get("path", "")

    if path == "/sse":
        if not _check_auth(scope):
            await _send_unauthorized(scope, receive, send)
            return
        _extract_context(scope)
        async with sse.connect_sse(scope, receive, send) as streams:
            await server.run(
                streams[0], streams[1], server.create_initialization_options()
            )
    elif path.startswith("/messages/"):
        if not _check_auth(scope):
            await _send_unauthorized(scope, receive, send)
            return
        await sse.handle_post_message(scope, receive, send)
    elif path == "/mcp":
        if not _check_auth(scope):
            await _send_unauthorized(scope, receive, send)
            return
        _extract_context(scope)
        await _ensure_http_server()
        await _http_transport.handle_request(scope, receive, send)
    else:
        body = b"Not Found"
        await send({
            "type": "http.response.start",
            "status": 404,
            "headers": [[b"content-type", b"text/plain"], [b"content-length", str(len(body)).encode()]],
        })
        await send({"type": "http.response.body", "body": body})
