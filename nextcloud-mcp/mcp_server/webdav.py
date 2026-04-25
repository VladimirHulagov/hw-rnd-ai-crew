import base64
import logging
import os
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from typing import List, Optional
from urllib.parse import quote

import httpx

log = logging.getLogger(__name__)


@dataclass
class NextcloudFile:
    path: str
    filename: str
    mimetype: str
    size: int
    modified_time: str


def _base_url() -> str:
    return os.environ.get("NEXTCLOUD_URL", "https://nextcloud.example.com")


def _auth() -> tuple:
    user = os.environ.get("NEXTCLOUD_USER", "")
    password = os.environ.get("NEXTCLOUD_APP_PASSWORD", "")
    return (user, password)


def _dav_base() -> str:
    user = os.environ.get("NEXTCLOUD_USER", "")
    return f"{_base_url()}/remote.php/dav/files/{user}"


def upload_file(path: str, content: bytes, content_type: str = "application/octet-stream") -> dict:
    url = _dav_base() + quote(path, safe="/")
    resp = httpx.put(url, auth=_auth(), content=content, headers={"Content-Type": content_type}, timeout=120)
    if resp.status_code not in (200, 201, 204):
        raise Exception(f"Upload failed: {resp.status_code} {resp.text}")
    return {"path": path, "size": len(content), "status": "uploaded"}


def download_file(path: str) -> dict:
    url = _dav_base() + quote(path, safe="/")
    resp = httpx.get(url, auth=_auth(), timeout=120)
    if resp.status_code != 200:
        raise Exception(f"Download failed: {resp.status_code} {resp.text}")
    content_b64 = base64.b64encode(resp.content).decode()
    ct = resp.headers.get("content-type", "application/octet-stream")
    return {"path": path, "content": content_b64, "size": len(resp.content), "content_type": ct}


def list_files(path: str = "/", depth: int = 1) -> List[dict]:
    url = _dav_base() + quote(path, safe="/")
    resp = httpx.request("PROPFIND", url, auth=_auth(), headers={"Depth": str(depth)}, timeout=30)
    if resp.status_code != 207:
        raise Exception(f"List failed: {resp.status_code} {resp.text}")
    return _parse_propfind(resp.text)


def _parse_propfind(xml_text: str) -> List[dict]:
    ns = {"d": "DAV:", "oc": "http://owncloud.org/ns"}
    root = ET.fromstring(xml_text)
    files = []
    for resp in root.findall("d:response", ns):
        href = resp.find("d:href", ns)
        if href is None:
            continue
        href_text = href.text

        props = resp.find(".//d:propstat/d:prop", ns)
        if props is None:
            continue

        getcontenttype = props.find("d:getcontenttype", ns)
        getcontentlength = props.find("d:getcontentlength", ns)
        getlastmodified = props.find("d:getlastmodified", ns)

        mimetype = getcontenttype.text if getcontenttype is not None else ""
        size = int(getcontentlength.text) if getcontentlength is not None else 0
        modified_time = getlastmodified.text if getlastmodified is not None else ""

        filename = href_text.rstrip("/").split("/")[-1]

        path_parts = href_text.split("/files/")
        if len(path_parts) > 1:
            sub = path_parts[-1]
            first_slash = sub.find("/")
            relative_path = sub[first_slash:] if first_slash >= 0 else "/" + sub
        else:
            relative_path = href_text

        if not mimetype and not href_text.endswith("/"):
            continue

        files.append({
            "path": relative_path,
            "filename": filename,
            "mimetype": mimetype,
            "size": size,
            "modified_time": modified_time,
        })
    return files


def mkdir(path: str) -> dict:
    url = _dav_base() + quote(path, safe="/")
    resp = httpx.request("MKCOL", url, auth=_auth(), timeout=30)
    if resp.status_code not in (200, 201, 405):
        raise Exception(f"Mkdir failed: {resp.status_code} {resp.text}")
    status = "created" if resp.status_code in (200, 201) else "already_exists"
    return {"path": path, "status": status}
