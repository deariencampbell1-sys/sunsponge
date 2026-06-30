"""Parse pathway-manifest.md and verifier JSON into a unified map shape.

Compatible with rhobear-verifier manifest.js field semantics:
  { summary, pathways[], routes[], plugins[], suspicious[], antiPatterns[], metadata }
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urljoin

from sunsponge.capture_service import RestedCaptureError, normalize_url

_ROUTE_METHOD_RE = re.compile(
    r"\b(GET|POST|PUT|DELETE|PATCH|WS)\s+(/[^\s`'\"]+)",
    re.IGNORECASE,
)
_URL_RE = re.compile(r"https?://[^\s`'\"]+", re.IGNORECASE)

_VIEW_FROM_FILE: dict[str, str] = {
    "restedcaptureview": "capture",
    "hubview": "hub",
    "boardview": "board",
    "workbenchview": "workbench",
    "catalogview": "catalog",
    "skillsview": "skills",
    "settingsview": "settings",
    "vaultview": "vault",
    "cronview": "cron",
    "worldsview": "worlds",
    "cliview": "cli",
    "learnview": "learn",
    "app.jsx": "hub",
}


def load_pathway_map(
    *,
    manifest_path: str | Path | None = None,
    map_path: str | Path | None = None,
    manifest_text: str | None = None,
    map_text: str | None = None,
) -> dict[str, Any]:
    """Load a pathway map. PASTED text is the primary path (Captur'd is a desktop
    tool — the user pastes/uploads the manifest their agent produced); file paths
    are the CLI convenience. manifest_* = markdown pathway-manifest;
    map_* = verifier JSON."""
    if manifest_text and manifest_text.strip():
        return parse_manifest_text(manifest_text, source="<pasted manifest>")
    if map_text and map_text.strip():
        return parse_verifier_json_text(map_text, source="<pasted map>")
    if manifest_path and map_path:
        raise RestedCaptureError("provide a manifest or a map, not both")
    if manifest_path:
        path = Path(manifest_path)
        if not path.is_file():
            # Name the offending input but never echo the path itself.
            raise RestedCaptureError(
                "manifest_path does not exist or is not a readable file"
            )
        return parse_manifest_md(path)
    if map_path:
        path = Path(map_path)
        if not path.is_file():
            raise RestedCaptureError(
                "map_path does not exist or is not a readable file"
            )
        return parse_verifier_json(path)
    raise RestedCaptureError(
        "a pathway map is required — paste the manifest markdown (or pass a file)"
    )


def parse_manifest_md(manifest_path: Path) -> dict[str, Any]:
    try:
        raw = manifest_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise RestedCaptureError(
            "manifest_path could not be read as UTF-8 text"
        ) from exc
    return parse_manifest_text(raw, source=str(manifest_path))


def parse_manifest_text(raw: str, source: str = "<pasted manifest>") -> dict[str, Any]:
    if not (raw or "").strip():
        raise RestedCaptureError("pathway manifest is empty")
    now = datetime.now(timezone.utc).isoformat()
    sections = _split_sections(raw)
    return {
        "raw": raw,
        "hash": hashlib.sha256(raw.encode("utf-8")).hexdigest(),
        "file": source,
        "parsedAt": now,
        "summary": _extract_summary(sections),
        "pathways": _extract_pathways(sections),
        "routes": _extract_routes(sections),
        "plugins": _extract_plugins(sections),
        "suspicious": _extract_suspicious(sections),
        "antiPatterns": _extract_anti_patterns(sections),
        "metadata": {"file": source, "parsedAt": now},
    }


def parse_verifier_json(map_path: Path) -> dict[str, Any]:
    try:
        text = map_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise RestedCaptureError(
            "map_path could not be read as UTF-8 text"
        ) from exc
    return parse_verifier_json_text(text, source=str(map_path))


def parse_verifier_json_text(text: str, source: str = "<pasted map>") -> dict[str, Any]:
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise RestedCaptureError(
            "pathway map is not valid verifier JSON"
        ) from exc
    if not isinstance(data, dict):
        raise RestedCaptureError("verifier map JSON must be an object")

    pathways: list[dict[str, Any]] = []
    routes: list[dict[str, Any]] = []
    seen_route_paths: set[str] = set()

    for check_name, check in (data.get("checks") or {}).items():
        if not isinstance(check, dict):
            continue
        for finding in check.get("findings") or []:
            if not isinstance(finding, dict):
                continue
            fid = str(finding.get("id") or f"{check_name}-{len(pathways)}")
            file_ref = str(finding.get("file") or "")
            line_ref = str(finding.get("line") or "")
            location = f"{file_ref}:{line_ref}".strip(":")
            route_path = str(finding.get("path") or "").strip()
            pathways.append({
                "id": fid,
                "location": location,
                "trigger": str(finding.get("code") or finding.get("message") or "").strip(),
                "declaredIntent": str(finding.get("message") or "").strip(),
                "handler": str(check_name),
                "downstreamCall": route_path,
                "expectedSideEffect": str(finding.get("suggestion") or "").strip(),
                "evidence": str(finding.get("code") or "").strip(),
                "status": _status_from_finding(check_name, finding),
                "rawStatus": str(finding.get("severity") or check_name).upper(),
                "unwiredFlag": check_name == "ui-route-diff",
                "check": check_name,
            })
            if route_path and route_path not in seen_route_paths:
                seen_route_paths.add(route_path)
                routes.append({
                    "method": "GET",
                    "path": route_path,
                    "handler": location,
                    "raw": finding,
                })

    return {
        "raw": None,
        "hash": hashlib.sha256(text.encode("utf-8")).hexdigest(),
        "file": source,
        "parsedAt": datetime.now(timezone.utc).isoformat(),
        "summary": data.get("manifest_summary") or data.get("summary") or {},
        "pathways": pathways,
        "routes": routes,
        "plugins": [],
        "suspicious": [],
        "antiPatterns": [],
        "metadata": {
            "file": source,
            "parsedAt": datetime.now(timezone.utc).isoformat(),
            "version": data.get("version"),
            "repo": data.get("repo"),
            "manifest_found": data.get("manifest_found"),
        },
    }


def _status_from_finding(check_name: str, finding: dict[str, Any]) -> str:
    if check_name == "ui-route-diff":
        return "UNWIRED"
    severity = str(finding.get("severity") or "").lower()
    if severity in {"critical", "high"}:
        return "INCOMPLETE"
    if severity == "medium":
        return "UNCLEAR"
    return "WIRED"


def _split_sections(raw: str) -> list[dict[str, Any]]:
    lines = raw.split("\n")
    sections: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    for line in lines:
        match = re.match(r"^(#{1,6})\s+(.+?)\s*$", line)
        if match:
            if current:
                sections.append(current)
            current = {"heading": match.group(2).strip(), "level": len(match.group(1)), "body": []}
        elif current is not None:
            current["body"].append(line)
        else:
            current = {"heading": None, "level": 0, "body": [line]}
    if current:
        sections.append(current)
    return sections


def _extract_summary(sections: list[dict[str, Any]]) -> dict[str, Any]:
    summary: dict[str, Any] = {
        "pathwaysTotal": None,
        "wired": None,
        "mocked": None,
        "unwired": None,
        "incomplete": None,
        "unclear": None,
        "routesCount": None,
        "pluginsCount": None,
        "raw": None,
    }
    for sec in sections:
        text = "\n".join(sec["body"])
        for match in re.finditer(
            r"\|\s*(WIRED|MOCKED|UNWIRED|INCOMPLETE|UNCLEAR|Wired|Mocked|Unwired|Incomplete|Unclear|Total pathways|TODO|FIXME)\s*\|?\s*(\d+)\s*\|",
            text,
            re.IGNORECASE,
        ):
            key = match.group(1).lower()
            val = int(match.group(2))
            if "wire" in key and "un" not in key:
                summary["wired"] = val
            elif "mock" in key:
                summary["mocked"] = val
            elif "unwire" in key:
                summary["unwired"] = val
            elif "incomplete" in key:
                summary["incomplete"] = val
            elif "unclear" in key:
                summary["unclear"] = val
            elif "total" in key:
                summary["pathwaysTotal"] = val
        routes_match = re.search(r"(\d+)\s+(?:FastAPI\s+)?routes?", text, re.IGNORECASE)
        if routes_match and summary["routesCount"] is None:
            summary["routesCount"] = int(routes_match.group(1))
        plugins_match = re.search(r"(\d+)\+?\s+(?:bundled\s+)?plugins?", text, re.IGNORECASE)
        if plugins_match and summary["pluginsCount"] is None:
            summary["pluginsCount"] = int(plugins_match.group(1))
    return summary


def _extract_pathways(sections: list[dict[str, Any]]) -> list[dict[str, Any]]:
    pathways: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for sec in sections:
        heading = str(sec.get("heading") or "")
        if re.search(r"suspicious|risk|anti-?pattern|open questions|plugin", heading, re.I):
            continue
        for table in _extract_tables("\n".join(sec["body"])):
            headers = [h.lower() for h in table["headers"]]
            if "rank" in headers:
                continue
            has_id = any(re.search(r"^id$|slug|element|pathway", h) for h in headers)
            has_status = any(h in {"status", "wired", "unwired flag", "unwired"} for h in headers)
            if not has_id or not has_status:
                continue
            for row in table["rows"]:
                row_map: dict[str, str] = {}
                for i, header in enumerate(headers):
                    row_map[header] = row[i] if i < len(row) else ""
                pid = (
                    row_map.get("id")
                    or row_map.get("slug")
                    or row_map.get("element")
                    or row_map.get("name")
                    or row_map.get("pathway")
                    or ""
                ).strip().strip("`")
                status_raw = (
                    row_map.get("status")
                    or row_map.get("wired")
                    or row_map.get("unwired flag")
                    or ""
                ).upper().strip()
                location = (
                    row_map.get("location")
                    or row_map.get("file:line")
                    or f"{row_map.get('file', '')}:{row_map.get('line', '')}".strip(":")
                ).strip()
                if not pid or pid in {"id", "slug", "element"} or len(pid) > 200:
                    continue
                if pid in seen_ids:
                    continue
                seen_ids.add(pid)
                pathways.append({
                    "id": pid,
                    "location": location,
                    "trigger": (row_map.get("trigger") or row_map.get("href") or "").strip(),
                    "selector": (row_map.get("selector") or row_map.get("css") or row_map.get("css selector") or "").strip().strip("`"),
                    "declaredIntent": (
                        row_map.get("declared intent")
                        or row_map.get("declared_intent")
                        or row_map.get("intent")
                        or ""
                    ).strip(),
                    "handler": (row_map.get("handler") or row_map.get("downstream call") or row_map.get("downstream_call") or "").strip(),
                    "downstreamCall": (
                        row_map.get("downstream call")
                        or row_map.get("downstream_call")
                        or row_map.get("network call")
                        or row_map.get("expected side effect")
                        or row_map.get("expected_side_effect")
                        or ""
                    ).strip(),
                    "expectedSideEffect": (
                        row_map.get("expected side effect")
                        or row_map.get("expected_side_effect")
                        or row_map.get("side effect")
                        or ""
                    ).strip(),
                    "evidence": (row_map.get("evidence") or "").strip(),
                    "status": _normalize_status(status_raw),
                    "rawStatus": status_raw,
                    "unwiredFlag": bool(re.search(r"\b(y|yes|⚠|true|⚠\s*yes)\b", row_map.get("unwired flag", ""), re.I)),
                })
    return pathways


def _normalize_status(status: str) -> str:
    text = (status or "").upper()
    if "WIRED" in text and "UN" not in text:
        return "WIRED"
    if "MOCK" in text:
        return "MOCKED"
    if "UNWIRE" in text or "UNWIRED" in text:
        return "UNWIRED"
    if "INCOMPLETE" in text:
        return "INCOMPLETE"
    if "UNCLEAR" in text:
        return "UNCLEAR"
    if text in {"YES", "TRUE", "⚠ YES", "⚠YES"}:
        return "UNWIRED"
    return text or "UNKNOWN"


def _extract_routes(sections: list[dict[str, Any]]) -> list[dict[str, Any]]:
    routes: list[dict[str, Any]] = []
    for sec in sections:
        for table in _extract_tables("\n".join(sec["body"])):
            headers = [h.lower() for h in table["headers"]]
            if not any(re.search(r"path|route|endpoint", h) for h in headers):
                continue
            for row in table["rows"]:
                row_map: dict[str, str] = {}
                for i, header in enumerate(headers):
                    row_map[header] = row[i] if i < len(row) else ""
                method = (row_map.get("method") or row_map.get("http method") or "GET").upper().strip()
                path = (row_map.get("path") or row_map.get("route") or row_map.get("endpoint") or "").strip().strip("`")
                if not path:
                    continue
                routes.append({
                    "method": method,
                    "path": path,
                    "handler": (row_map.get("handler") or row_map.get("handler file:line") or "").strip(),
                    "raw": row_map,
                })
    return routes


def _extract_plugins(sections: list[dict[str, Any]]) -> list[dict[str, Any]]:
    plugins: list[dict[str, Any]] = []
    for sec in sections:
        if not re.search(r"plugin", sec.get("heading") or "", re.I):
            continue
        for table in _extract_tables("\n".join(sec["body"])):
            headers = [h.lower() for h in table["headers"]]
            if not any(re.search(r"^name$|plugin|skill", h) for h in headers):
                continue
            for row in table["rows"]:
                row_map: dict[str, str] = {}
                for i, header in enumerate(headers):
                    row_map[header] = row[i] if i < len(row) else ""
                name = (row_map.get("name") or row_map.get("plugin") or row_map.get("skill") or "").strip()
                if not name or name.lower() == "name":
                    continue
                plugins.append({"name": name, "raw": row_map})
    return plugins


def _extract_suspicious(sections: list[dict[str, Any]]) -> list[dict[str, Any]]:
    suspicious: list[dict[str, Any]] = []
    for sec in sections:
        if not re.search(r"suspicious|top\s+\d|risk", sec.get("heading") or "", re.I):
            continue
        for table in _extract_tables("\n".join(sec["body"])):
            headers = [h.lower() for h in table["headers"]]
            if not headers:
                continue
            for row in table["rows"]:
                entry: dict[str, str] = {}
                for i, header in enumerate(headers):
                    entry[header] = row[i] if i < len(row) else ""
                if all(not v or v == headers[0] for v in entry.values()):
                    continue
                suspicious.append(entry)
    return suspicious


def _extract_anti_patterns(sections: list[dict[str, Any]]) -> list[dict[str, Any]]:
    patterns: list[dict[str, Any]] = []
    for sec in sections:
        if not re.search(r"anti-?pattern|finding|grep|catch", sec.get("heading") or "", re.I):
            continue
        for table in _extract_tables("\n".join(sec["body"])):
            headers = [h.lower() for h in table["headers"]]
            if not any(re.search(r"category|type|anti-?pattern|pattern", h) for h in headers):
                continue
            for row in table["rows"]:
                entry: dict[str, str] = {}
                for i, header in enumerate(headers):
                    entry[header] = row[i] if i < len(row) else ""
                cat = (
                    entry.get("category")
                    or entry.get("type")
                    or entry.get("anti-pattern")
                    or entry.get("pattern")
                    or ""
                ).strip()
                if not cat:
                    continue
                patterns.append({
                    "category": cat,
                    "count": int(entry.get("count") or "0") or None,
                    "file": entry.get("file") or "",
                    "line": entry.get("line") or "",
                    "quote": entry.get("quote") or entry.get("snippet") or entry.get("code") or "",
                })
    return patterns


def _extract_tables(text: str) -> list[dict[str, list[str]]]:
    tables: list[dict[str, list[str]]] = []
    lines = text.split("\n")
    i = 0
    while i < len(lines):
        header_line = lines[i]
        if "|" not in header_line:
            i += 1
            continue
        cells = _split_row(header_line)
        if len(cells) < 2:
            i += 1
            continue
        sep_line = lines[i + 1] if i + 1 < len(lines) else ""
        if not re.match(r"^\s*\|?[\s\-:|]+\|?\s*$", sep_line) or "-" not in sep_line:
            i += 1
            continue
        rows: list[list[str]] = []
        j = i + 2
        while j < len(lines) and "|" in lines[j]:
            row = _split_row(lines[j])
            if len(row) >= len(cells) - 1:
                rows.append(row)
            j += 1
        tables.append({"headers": [c.strip() for c in cells], "rows": rows})
        i = j
    return tables


def _split_row(line: str) -> list[str]:
    text = line.strip()
    if text.startswith("|"):
        text = text[1:]
    if text.endswith("|"):
        text = text[:-1]
    return [cell.strip() for cell in text.split("|")]


def _infer_view_from_location(location: str) -> str | None:
    file_part = (location or "").split(":")[0].lower().replace("\\", "/")
    base = file_part.rsplit("/", 1)[-1]
    compact = base.replace("_", "").replace("-", "")
    for key, view in _VIEW_FROM_FILE.items():
        if key.replace(".", "") in compact or key in base:
            return view
    return None


def _extract_route_path(text: str) -> str | None:
    for match in _ROUTE_METHOD_RE.finditer(text or ""):
        return match.group(2).strip()
    for match in re.finditer(r"(/api/[^\s`'\"]+)", text or ""):
        return match.group(1).strip()
    return None


def resolve_pathway_url(
    pathway: dict[str, Any],
    *,
    base_url: str,
    routes: list[dict[str, Any]] | None = None,
) -> str:
    """Resolve the page URL to load for a pathway."""
    for field in ("downstreamCall", "downstream_call", "trigger", "handler", "declaredIntent"):
        url_match = _URL_RE.search(str(pathway.get(field) or ""))
        if url_match:
            return normalize_url(url_match.group(0))

    view = _infer_view_from_location(str(pathway.get("location") or ""))
    root = base_url.rstrip("/")
    if view and view != "hub":
        return normalize_url(f"{root}/?view={view}")

    route_path = _extract_route_path(str(pathway.get("downstreamCall") or pathway.get("downstream_call") or ""))
    if route_path and routes and not route_path.startswith("/api/"):
        for route in routes:
            if route.get("path") == route_path and str(route.get("method", "GET")).upper() in {"GET", "WS"}:
                return normalize_url(urljoin(base_url.rstrip("/") + "/", route_path.lstrip("/")))

    return normalize_url(root or base_url)


def plan_targets_from_map(
    pathway_map: dict[str, Any],
    *,
    base_url: str,
    viewports: list[str],
    schemes: list[str],
) -> tuple[list[str], list[dict[str, Any]]]:
    """Expand a parsed map into unique URLs and per-pathway capture descriptors."""
    if not base_url.strip():
        raise RestedCaptureError("base_url is required when feeding a pathway map")

    pathways = pathway_map.get("pathways") or []
    if not pathways:
        raise RestedCaptureError("pathway map contains no pathways")

    routes = pathway_map.get("routes") or []
    url_by_pathway: list[tuple[str, dict[str, Any]]] = []
    for pathway in pathways:
        url = resolve_pathway_url(pathway, base_url=base_url, routes=routes)
        url_by_pathway.append((url, pathway))

    urls = []
    seen: set[str] = set()
    for url, _pathway in url_by_pathway:
        key = url
        if key not in seen:
            seen.add(key)
            urls.append(url)

    descriptors: list[dict[str, Any]] = []
    for index, (url, pathway) in enumerate(url_by_pathway, start=1):
        status = str(pathway.get("status") or "UNKNOWN")
        trigger = str(pathway.get("trigger") or "")
        for viewport_id in viewports:
            for scheme in schemes:
                descriptors.append({
                    "index": index,
                    "url": url,
                    "viewport_id": viewport_id,
                    "scheme": scheme,
                    "pathway_id": str(pathway.get("id") or f"pathway-{index}"),
                    "pathway_status": status,
                    "pathway_trigger": trigger,
                    "selector": str(pathway.get("selector") or "").strip(),
                    "pathway_location": str(pathway.get("location") or ""),
                    "pathway_handler": str(pathway.get("handler") or ""),
                    "pathway_downstream": str(pathway.get("downstreamCall") or pathway.get("downstream_call") or ""),
                })
    return urls, descriptors