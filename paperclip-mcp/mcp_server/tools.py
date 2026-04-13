import logging
import os
from typing import Any, Dict, List, Optional

import httpx

log = logging.getLogger(__name__)

_API_URL = os.environ.get("PAPERCLIP_API_URL", "http://localhost:3100/api")

_current_api_key: str = ""
_current_company_id: str = ""
_current_agent_id: str = ""


def set_context(api_key: str = "", company_id: str = "", agent_id: str = ""):
    global _current_api_key, _current_company_id, _current_agent_id
    if api_key:
        _current_api_key = api_key
    if company_id:
        _current_company_id = company_id
    if agent_id:
        _current_agent_id = agent_id


def _headers() -> dict:
    h = {"Content-Type": "application/json"}
    if _current_api_key:
        h["Authorization"] = f"Bearer {_current_api_key}"
    return h


async def _request(method: str, path: str, *, params: dict = None, json_body: dict = None) -> Any:
    url = f"{_API_URL}{path}"
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.request(
            method, url, headers=_headers(), params=params, json=json_body
        )
    if resp.status_code >= 400:
        return {"error": f"HTTP {resp.status_code}", "detail": resp.text[:500]}
    if resp.status_code == 204:
        return {"ok": True}
    try:
        return resp.json()
    except Exception:
        return {"raw": resp.text[:2000]}


async def list_issues(
    status: Optional[str] = None,
    assigneeAgentId: Optional[str] = None,
    projectId: Optional[str] = None,
    parentId: Optional[str] = None,
) -> Any:
    params = {}
    if status:
        params["status"] = status
    if assigneeAgentId:
        params["assigneeAgentId"] = assigneeAgentId
    if projectId:
        params["projectId"] = projectId
    if parentId:
        params["parentId"] = parentId
    return await _request("GET", f"/companies/{_current_company_id}/issues", params=params)


async def get_issue(issueId: str) -> Any:
    return await _request("GET", f"/issues/{issueId}")


async def create_issue(
    title: str,
    description: Optional[str] = None,
    status: Optional[str] = None,
    priority: Optional[str] = None,
    assigneeAgentId: Optional[str] = None,
    projectId: Optional[str] = None,
    parentId: Optional[str] = None,
) -> Any:
    body: Dict[str, Any] = {"title": title}
    if description is not None:
        body["description"] = description
    if status is not None:
        body["status"] = status
    if priority is not None:
        body["priority"] = priority
    if assigneeAgentId is not None:
        body["assigneeAgentId"] = assigneeAgentId
    if projectId is not None:
        body["projectId"] = projectId
    if parentId is not None:
        body["parentId"] = parentId
    return await _request("POST", f"/companies/{_current_company_id}/issues", json_body=body)


async def update_issue(
    issueId: str,
    status: Optional[str] = None,
    priority: Optional[str] = None,
    assigneeAgentId: Optional[str] = None,
    description: Optional[str] = None,
    comment: Optional[str] = None,
) -> Any:
    body: Dict[str, Any] = {}
    if status is not None:
        body["status"] = status
    if priority is not None:
        body["priority"] = priority
    if assigneeAgentId is not None:
        body["assigneeAgentId"] = assigneeAgentId
    if description is not None:
        body["description"] = description
    if comment is not None:
        body["comment"] = comment
    return await _request("PATCH", f"/issues/{issueId}", json_body=body)


async def delete_issue(issueId: str) -> Any:
    return await _request("DELETE", f"/issues/{issueId}")


async def checkout_issue(
    issueId: str,
    expectedStatuses: Optional[List[str]] = None,
) -> Any:
    body = {
        "agentId": _current_agent_id,
        "expectedStatuses": expectedStatuses or ["todo", "backlog"],
    }
    return await _request("POST", f"/issues/{issueId}/checkout", json_body=body)


async def release_issue(issueId: str) -> Any:
    return await _request("POST", f"/issues/{issueId}/release", json_body={})


async def list_comments(issueId: str, limit: int = 50) -> Any:
    return await _request("GET", f"/issues/{issueId}/comments", params={"limit": limit, "order": "desc"})


async def create_comment(issueId: str, body: str) -> Any:
    return await _request("POST", f"/issues/{issueId}/comments", json_body={"body": body})


async def list_agents() -> Any:
    return await _request("GET", f"/companies/{_current_company_id}/agents")


async def get_agent(agentId: str) -> Any:
    if agentId == "me":
        return await get_current_agent()
    return await _request("GET", f"/agents/{agentId}")


async def get_current_agent() -> Any:
    return await _request("GET", "/agents/me")


async def list_projects() -> Any:
    return await _request("GET", f"/companies/{_current_company_id}/projects")


async def get_company() -> Any:
    return await _request("GET", f"/companies/{_current_company_id}")


async def list_goals() -> Any:
    return await _request("GET", f"/companies/{_current_company_id}/goals")


async def get_goal(goalId: str) -> Any:
    return await _request("GET", f"/goals/{goalId}")
