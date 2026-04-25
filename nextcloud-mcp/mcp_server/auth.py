import os

_BEARER_PREFIX = "Bearer "


def check_auth(scope) -> bool:
    token = os.environ.get("NEXTCLOUD_MCP_API_KEY", "")
    if not token:
        return True
    headers = {}
    for key, value in scope.get("headers", []):
        headers[key.decode()] = value.decode()
    auth_header = headers.get("authorization", "")
    if not auth_header.startswith(_BEARER_PREFIX):
        return False
    return auth_header[len(_BEARER_PREFIX):] == token
