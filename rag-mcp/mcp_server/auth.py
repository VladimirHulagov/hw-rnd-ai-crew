import os
from fastapi import Request
from fastapi.responses import JSONResponse


_BEARER_PREFIX = "Bearer "


async def auth_middleware(request: Request, call_next):
    token = os.environ.get("MCP_BEARER_TOKEN", "")
    if not token:
        return await call_next(request)

    auth_header = request.headers.get("authorization", "")
    if not auth_header.startswith(_BEARER_PREFIX):
        return JSONResponse({"error": "unauthorized"}, status_code=401)

    provided = auth_header[len(_BEARER_PREFIX):]
    if provided != token:
        return JSONResponse({"error": "unauthorized"}, status_code=401)

    return await call_next(request)
