import io
import logging
import os
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from typing import List, Optional
from urllib.parse import quote

import requests

log = logging.getLogger(__name__)


@dataclass
class NextcloudFile:
    path: str
    filename: str
    mimetype: str
    size: int
    modified_time: int
    file_id: Optional[int] = None


def _base_url() -> str:
    return os.environ.get("NEXTCLOUD_URL", "https://nextcloud.collaborationism.tech")


def _auth() -> tuple:
    user = os.environ.get("NEXTCLOUD_USER", "")
    password = os.environ.get("NEXTCLOUD_APP_PASSWORD", "")
    return (user, password)


def _dav_base() -> str:
    user = os.environ.get("NEXTCLOUD_USER", "")
    return f"{_base_url()}/remote.php/dav/files/{user}"


def list_files(directory: str = "/") -> List[NextcloudFile]:
    url = _dav_base() + quote(directory, safe="/")
    headers = {"Depth": "1"}
    resp = requests.request("PROPFIND", url, auth=_auth(), headers=headers, timeout=30)
    resp.raise_for_status()
    return _parse_propfind(resp.text)


def _parse_propfind(xml_text: str) -> List[NextcloudFile]:
    ns = {"d": "DAV:"}
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
        file_id_el = props.find("{http://owncloud.org/ns}fileid", ns)

        mimetype = getcontenttype.text if getcontenttype is not None else ""
        size = int(getcontentlength.text) if getcontentlength is not None else 0

        modified_time = 0
        if getlastmodified is not None and getlastmodified.text:
            from email.utils import parsedate_to_datetime
            try:
                dt = parsedate_to_datetime(getlastmodified.text)
                modified_time = int(dt.timestamp())
            except Exception:
                pass

        file_id = int(file_id_el.text) if file_id_el is not None else None

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

        if mimetype:
            files.append(NextcloudFile(
                path=relative_path,
                filename=filename,
                mimetype=mimetype,
                size=size,
                modified_time=modified_time,
                file_id=file_id,
            ))

    return files


def list_all_files(directory: str = "/") -> List[NextcloudFile]:
    all_files = []
    queue = [directory]
    while queue:
        current = queue.pop(0)
        try:
            items = list_files(current)
        except Exception as e:
            log.error("Failed to list %s: %s", current, e)
            continue
        for item in items:
            if item.mimetype in ("httpd/unix-directory", "inode/directory"):
                subpath = item.path if item.path.endswith("/") else item.path + "/"
                if subpath != current:
                    queue.append(subpath)
            else:
                all_files.append(item)
    return all_files


def download_file(file_path: str) -> bytes:
    url = _dav_base() + quote(file_path, safe="/")
    resp = requests.get(url, auth=_auth(), timeout=120)
    resp.raise_for_status()
    return resp.content
