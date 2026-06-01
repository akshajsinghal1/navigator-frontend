"""
tableau/vds.py
──────────────
VizQL Data Service (VDS) client for Tableau Cloud / Server.

VDS lets us query published data sources directly — no view needed.
Field names returned match the data source metadata exactly, so the
agent pipeline can pick exact field captions and the frontend can
look them up with zero fuzzy matching.

Usage:

    with VdsClient.from_env() as vds:
        sources = vds.list_workbook_datasources(workbook_luid)
        for ds in sources:
            meta = vds.read_datasource_metadata(ds["luid"])
            rows = vds.query_datasource(
                ds["luid"],
                fields=[
                    {"fieldCaption": "Category"},
                    {"fieldCaption": "Sales", "function": "SUM"},
                ],
            )

Endpoints
─────────
  POST /api/{ver}/auth/signin
  GET  /api/{ver}/sites/{site}/workbooks/{wb}/connections
  GET  /api/{ver}/sites/{site}/datasources
  POST /api/v1/vizql-data-service/read-metadata
  POST /api/v1/vizql-data-service/query-datasource
  POST /api/{ver}/auth/signout
"""

from __future__ import annotations

import logging
import os
from typing import Any, Optional

import requests

log = logging.getLogger(__name__)


# Tableau Cloud current as of 2026-05. VDS itself uses /api/v1/vizql-data-service
# independent of REST API version, but we need REST for auth + workbook listing.
DEFAULT_REST_API_VERSION = "3.22"


class VdsError(RuntimeError):
    """Raised when a VDS or REST call fails."""

    def __init__(self, message: str, status: int = 0, body: str = "") -> None:
        super().__init__(message)
        self.status = status
        self.body   = body


class VdsClient:
    """
    HTTP client for Tableau REST + VizQL Data Service.

    Auth is handled via PAT (Personal Access Token). The same token works for
    both the REST API and VDS endpoints — no separate VDS auth needed.

    Use as a context manager:
        with VdsClient.from_env() as vds:
            ...

    Or manually:
        vds = VdsClient(...)
        vds.sign_in()
        try:
            ...
        finally:
            vds.sign_out()
    """

    def __init__(
        self,
        server_url:  str,
        site_name:   str,
        pat_name:    str,
        pat_secret:  str,
        api_version: str = DEFAULT_REST_API_VERSION,
        timeout:     int = 60,
    ) -> None:
        self.server_url  = server_url.rstrip("/")
        self.site_name   = site_name
        self.pat_name    = pat_name
        self.pat_secret  = pat_secret
        self.api_version = api_version
        self.timeout     = timeout

        # Populated by sign_in()
        self._token:     Optional[str] = None
        self._site_luid: Optional[str] = None

    # ── factory ──────────────────────────────────────────────────────────────

    @classmethod
    def from_env(cls) -> "VdsClient":
        return cls(
            server_url = os.environ["TABLEAU_SERVER_URL"],
            site_name  = os.environ["TABLEAU_SITE_NAME"],
            pat_name   = os.environ["TABLEAU_PAT_NAME"],
            pat_secret = os.environ["TABLEAU_PAT_SECRET"],
        )

    @classmethod
    def from_dict(cls, creds: dict[str, str]) -> "VdsClient":
        return cls(
            server_url = creds["tableau_server_url"],
            site_name  = creds["tableau_site_name"],
            pat_name   = creds["tableau_pat_name"],
            pat_secret = creds["tableau_pat_secret"],
        )

    # ── context manager ──────────────────────────────────────────────────────

    def __enter__(self) -> "VdsClient":
        self.sign_in()
        return self

    def __exit__(self, *_) -> None:
        self.sign_out()

    # ── auth ─────────────────────────────────────────────────────────────────

    def sign_in(self) -> None:
        url  = f"{self.server_url}/api/{self.api_version}/auth/signin"
        body = {
            "credentials": {
                "personalAccessTokenName":   self.pat_name,
                "personalAccessTokenSecret": self.pat_secret,
                "site": {"contentUrl": self.site_name},
            }
        }
        r = requests.post(url, json=body, headers={"Accept": "application/json"}, timeout=self.timeout)
        if r.status_code != 200:
            raise VdsError(f"VDS sign-in failed ({r.status_code})", r.status_code, r.text)
        creds = r.json()["credentials"]
        self._token     = creds["token"]
        self._site_luid = creds["site"]["id"]
        log.info("VDS signed in: site=%s site_luid=%s", self.site_name, self._site_luid)

    def sign_out(self) -> None:
        if not self._token:
            return
        url = f"{self.server_url}/api/{self.api_version}/auth/signout"
        try:
            requests.post(url, headers=self._headers(), timeout=self.timeout)
        except Exception as exc:
            log.warning("VDS sign-out error (ignored): %s", exc)
        self._token     = None
        self._site_luid = None

    # ── headers helper ───────────────────────────────────────────────────────

    def _headers(self) -> dict[str, str]:
        if not self._token:
            raise VdsError("Not signed in — call sign_in() first")
        return {
            "X-Tableau-Auth": self._token,
            "Accept":         "application/json",
            "Content-Type":   "application/json",
        }

    @property
    def site_luid(self) -> str:
        if not self._site_luid:
            raise VdsError("Not signed in")
        return self._site_luid

    # ── workbook → data source discovery ─────────────────────────────────────

    def list_workbook_datasources(self, workbook_luid: str) -> list[dict[str, Any]]:
        """
        Return the published data sources a workbook depends on.

        Uses the workbook connections endpoint, then dedupes by datasource LUID.
        Embedded (non-published) data sources are skipped — VDS only works with
        published data sources.

        Returns:
            list of {"luid", "name", "type", "project"}
        """
        url = f"{self.server_url}/api/{self.api_version}/sites/{self.site_luid}/workbooks/{workbook_luid}/connections"
        r = requests.get(url, headers=self._headers(), timeout=self.timeout)
        if r.status_code != 200:
            raise VdsError(f"list_workbook_datasources failed ({r.status_code})", r.status_code, r.text)

        conns = r.json().get("connections", {}).get("connection", []) or []

        # A connection has a `datasource` block with id + name when it's a published source.
        seen: set[str] = set()
        results: list[dict[str, Any]] = []
        for c in conns:
            ds = c.get("datasource") or {}
            luid = ds.get("id")
            if not luid or luid in seen:
                continue
            seen.add(luid)
            results.append({
                "luid":    luid,
                "name":    ds.get("name") or "",
                "type":    c.get("type") or "",
                "project": (ds.get("project") or {}).get("name") if isinstance(ds.get("project"), dict) else None,
            })

        log.info("Workbook %s uses %d published data source(s)", workbook_luid, len(results))
        return results

    def find_workbook_by_content_url(self, content_url: str) -> Optional[dict[str, Any]]:
        """Return workbook metadata dict {luid, name, content_url, project_name, updated_at} or None."""
        url = (f"{self.server_url}/api/{self.api_version}/sites/{self.site_luid}/workbooks"
               f"?filter=contentUrl:eq:{content_url}")
        r = requests.get(url, headers=self._headers(), timeout=self.timeout)
        if r.status_code != 200:
            raise VdsError(f"find_workbook failed ({r.status_code})", r.status_code, r.text)
        wbs = r.json().get("workbooks", {}).get("workbook", []) or []
        if not wbs:
            return None
        wb = wbs[0]
        return {
            "luid":         wb["id"],
            "name":         wb["name"],
            "content_url":  wb.get("contentUrl"),
            "project_name": (wb.get("project") or {}).get("name"),
            "updated_at":   wb.get("updatedAt"),
        }

    def list_workbook_views(self, workbook_luid: str) -> list[dict[str, Any]]:
        """List the views/sheets inside a workbook."""
        url = f"{self.server_url}/api/{self.api_version}/sites/{self.site_luid}/workbooks/{workbook_luid}/views"
        r = requests.get(url, headers=self._headers(), timeout=self.timeout)
        if r.status_code != 200:
            raise VdsError(f"list_workbook_views failed ({r.status_code})", r.status_code, r.text)
        views = r.json().get("views", {}).get("view", []) or []
        return [{"luid": v["id"], "name": v["name"], "content_url": v.get("contentUrl")} for v in views]

    def fetch_view_csv(
        self,
        view_luid: str,
        max_rows:  int = 5,
    ) -> list[dict[str, Any]]:
        """Download a view's data as CSV, return as a list of row dicts."""
        import csv as _csv
        import io  as _io
        url = f"{self.server_url}/api/{self.api_version}/sites/{self.site_luid}/views/{view_luid}/data"
        # Tableau's view data endpoint returns CSV — only the auth header is needed.
        # Don't send Accept: application/json (that yields 406).
        headers = {"X-Tableau-Auth": self._token}
        r = requests.get(url, headers=headers, timeout=self.timeout)
        if r.status_code != 200:
            raise VdsError(f"fetch_view_csv failed ({r.status_code})", r.status_code, r.text[:300])
        text = r.text
        reader = _csv.DictReader(_io.StringIO(text))
        rows = [dict(row) for row in reader]
        if max_rows and len(rows) > max_rows:
            rows = rows[:max_rows]
        return rows

    def fetch_view_csv_by_name(
        self,
        workbook_luid: str,
        view_name:     str,
        max_rows:      int = 5,
    ) -> list[dict[str, Any]]:
        """Convenience: look up the view by name within a workbook, then fetch CSV."""
        views = self.list_workbook_views(workbook_luid)
        match = next((v for v in views if v["name"].lower() == view_name.lower()), None)
        if not match:
            raise VdsError(f"view {view_name!r} not in workbook {workbook_luid}")
        return self.fetch_view_csv(match["luid"], max_rows=max_rows)

    # ── compatibility shims: lets VdsClient act as a drop-in for TableauConnector ──

    def get_workbook_by_content_url(self, content_url: str) -> dict[str, Any]:
        """Drop-in compatible with TableauConnector.get_workbook_by_content_url."""
        wb = self.find_workbook_by_content_url(content_url)
        if not wb:
            raise VdsError(f"Workbook not found: content_url={content_url!r}")
        return wb

    def list_views(self, workbook_luid: str) -> list[dict[str, Any]]:
        """Drop-in compatible alias for list_workbook_views."""
        return self.list_workbook_views(workbook_luid)

    def get_view_data_by_name(
        self,
        workbook_luid: str,
        view_name:     str,
        max_rows:      int = 200,
    ) -> list[dict[str, Any]]:
        """Drop-in compatible with TableauConnector.get_view_data_by_name."""
        return self.fetch_view_csv_by_name(workbook_luid, view_name, max_rows=max_rows)

    def list_site_datasources(self) -> list[dict[str, Any]]:
        """
        List ALL published data sources on the site (paged, up to 1000).
        Useful as a fallback when workbook→datasource mapping is empty.
        """
        url = f"{self.server_url}/api/{self.api_version}/sites/{self.site_luid}/datasources?pageSize=200"
        r = requests.get(url, headers=self._headers(), timeout=self.timeout)
        r.raise_for_status()
        ds_list = r.json().get("datasources", {}).get("datasource", [])
        return [
            {
                "luid":    d["id"],
                "name":    d["name"],
                "type":    d.get("type"),
                "project": (d.get("project") or {}).get("name"),
            }
            for d in ds_list
        ]

    # ── VDS endpoints ────────────────────────────────────────────────────────

    def read_datasource_metadata(self, datasource_luid: str) -> list[dict[str, Any]]:
        """
        Return the queryable field list for a published data source.

        Each field dict typically contains:
            fieldName     : internal name (e.g. "Calculation_12345" for calc fields)
            fieldCaption  : display name (e.g. "Profit Ratio")        ← USE THIS
            dataType      : "STRING" | "REAL" | "INTEGER" | "DATE" | "DATETIME" | "BOOLEAN"
            defaultAggregation : "SUM" / "AVG" / "NONE"
            logicalTableId
        """
        url = f"{self.server_url}/api/v1/vizql-data-service/read-metadata"
        body = {"datasource": {"datasourceLuid": datasource_luid}}
        r = requests.post(url, json=body, headers=self._headers(), timeout=self.timeout)
        if r.status_code != 200:
            raise VdsError(f"read-metadata failed ({r.status_code})", r.status_code, r.text)
        return r.json().get("data", []) or []

    def query_datasource(
        self,
        datasource_luid: str,
        fields:          list[dict[str, Any]],
        filters:         Optional[list[dict[str, Any]]] = None,
        return_format:   str = "OBJECTS",
        debug:           bool = False,
    ) -> list[dict[str, Any]]:
        """
        Execute a VDS query and return rows as a list of dicts.

        Args:
            datasource_luid : LUID of the published data source
            fields          : list of {"fieldCaption": "...", "function": "SUM"|"AVG"|...}
                              Function is optional — omit for raw values or for
                              already-aggregated calculated fields.
            filters         : optional list of filter dicts per VDS schema
            return_format   : "OBJECTS" → list of dicts (default), "ARRAYS" → list of lists
            debug           : pass debug=True for more verbose VDS error info

        Returns:
            list of row dicts. Keys are field captions (or SUM(caption) when aggregated).

        Raises:
            VdsError on non-200 response (preserves status + body for debugging).
        """
        url = f"{self.server_url}/api/v1/vizql-data-service/query-datasource"
        body: dict[str, Any] = {
            "datasource": {"datasourceLuid": datasource_luid},
            "query": {"fields": fields},
            "options": {"returnFormat": return_format, "debug": debug},
        }
        if filters:
            body["query"]["filters"] = filters

        r = requests.post(url, json=body, headers=self._headers(), timeout=self.timeout)
        if r.status_code != 200:
            raise VdsError(
                f"query-datasource failed ({r.status_code}): {r.text[:300]}",
                r.status_code,
                r.text,
            )
        return r.json().get("data", []) or []
