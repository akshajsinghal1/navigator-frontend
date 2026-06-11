"""
tableau/connector.py
─────────────────────
Thin wrapper around tableauserverclient (TSC) that manages the auth lifecycle.

Use as a context manager:

    with TableauConnector.from_env() as conn:
        views = conn.list_views(workbook_luid)
        data  = conn.get_view_data(view_luid)

All methods return plain Python dicts / lists (no TSC objects leak out).
"""

from __future__ import annotations

import logging
import os
from typing import Any

import tableauserverclient as TSC

log = logging.getLogger(__name__)


class TableauConnector:
    """Manages PAT-authenticated Tableau Cloud session."""

    def __init__(
        self,
        server_url: str,
        site_name: str,
        pat_name: str,
        pat_secret: str,
    ) -> None:
        self.server_url = server_url
        self.site_name  = site_name
        self.pat_name   = pat_name
        self.pat_secret = pat_secret
        self._server: TSC.Server | None = None

    # ── context manager ──────────────────────────────────────────────────────

    def __enter__(self) -> "TableauConnector":
        self._sign_in()
        return self

    def __exit__(self, *_) -> None:
        self._sign_out()

    # ── auth ─────────────────────────────────────────────────────────────────

    def _sign_in(self) -> None:
        auth = TSC.PersonalAccessTokenAuth(
            token_name=self.pat_name,
            personal_access_token=self.pat_secret,
            site_id=self.site_name,
        )
        server = TSC.Server(self.server_url, use_server_version=True)
        server.auth.sign_in(auth)
        self._server = server
        log.info("Signed in to %s (site: %s)", self.server_url, self.site_name)

    def _sign_out(self) -> None:
        if self._server:
            try:
                self._server.auth.sign_out()
                log.info("Signed out of Tableau")
            except Exception as exc:
                log.debug("Sign-out error (ignored): %s", exc)
            finally:
                self._server = None

    @property
    def server(self) -> TSC.Server:
        if not self._server:
            raise RuntimeError("Not signed in — use as a context manager.")
        return self._server

    # ── workbook helpers ──────────────────────────────────────────────────────

    def get_workbook_by_content_url(self, content_url: str) -> dict[str, Any]:
        """Return basic workbook metadata dict."""
        req = TSC.RequestOptions(pagesize=100)
        req.filter.add(TSC.Filter("contentUrl", TSC.RequestOptions.Operator.Equals, content_url))
        workbooks, _ = self.server.workbooks.get(req)
        if not workbooks:
            raise ValueError(f"Workbook not found: content_url={content_url!r}")
        wb = workbooks[0]
        return {
            "luid":        wb.id,
            "name":        wb.name,
            "content_url": wb.content_url,
            "project_name": wb.project_name,
            "updated_at":  str(wb.updated_at) if wb.updated_at else None,
        }

    def list_views(self, workbook_luid: str) -> list[dict[str, Any]]:
        """Return list of view dicts for a workbook."""
        wb = TSC.WorkbookItem(project_id="")
        wb._id = workbook_luid  # type: ignore[attr-defined]
        self.server.workbooks.populate_views(wb)
        return [
            {"luid": v.id, "name": v.name, "content_url": v.content_url}
            for v in wb.views
        ]

    # ── view data ────────────────────────────────────────────────────────────

    def get_view_data_by_name(
        self,
        workbook_luid: str,
        view_name: str,
        max_rows: int = 200,
    ) -> list[dict[str, Any]]:
        """
        Fetch data rows from a named view.
        Returns a list of dicts (one per row).
        """
        views = self.list_views(workbook_luid)
        match = [v for v in views if v["name"].lower() == view_name.lower()]
        if not match:
            raise ValueError(
                f"View {view_name!r} not found in workbook {workbook_luid}. "
                f"Available: {[v['name'] for v in views]}"
            )
        view_luid = match[0]["luid"]
        return self._fetch_csv_data(view_luid, max_rows)

    def get_view_data_by_luid(
        self,
        view_luid: str,
        max_rows: int = 200,
    ) -> list[dict[str, Any]]:
        return self._fetch_csv_data(view_luid, max_rows)

    def _fetch_csv_data(self, view_luid: str, max_rows: int) -> list[dict[str, Any]]:
        """
        Download the CSV underlying a view and parse into rows.
        Tableau's View Data endpoint returns CSV.
        """
        import csv
        import io

        view_item = TSC.ViewItem()
        view_item._id = view_luid  # type: ignore[attr-defined]

        csv_options = TSC.CSVRequestOptions()
        self.server.views.populate_csv(view_item, csv_options)

        # view_item.csv is bytes
        raw_bytes: bytes = b"".join(view_item.csv)  # type: ignore[arg-type]
        text = raw_bytes.decode("utf-8", errors="replace")

        reader = csv.DictReader(io.StringIO(text))
        rows = [dict(row) for row in reader]

        if max_rows and len(rows) > max_rows:
            rows = rows[:max_rows]

        log.debug("Fetched %d rows from view %s", len(rows), view_luid)
        return rows

    # ── factory ──────────────────────────────────────────────────────────────

    @classmethod
    def from_env(cls) -> "TableauConnector":
        """Create from environment variables (reads .env automatically)."""
        return cls(
            server_url  = os.environ["TABLEAU_SERVER_URL"],
            site_name   = os.environ["TABLEAU_SITE_NAME"],
            pat_name    = os.environ["TABLEAU_PAT_NAME"],
            pat_secret  = os.environ["TABLEAU_PAT_SECRET"],
        )

    @classmethod
    def from_dict(cls, creds: dict[str, str]) -> "TableauConnector":
        """Create from a credentials dict (e.g. from DB)."""
        return cls(
            server_url  = creds["tableau_server_url"],
            site_name   = creds["tableau_site_name"],
            pat_name    = creds["tableau_pat_name"],
            pat_secret  = creds["tableau_pat_secret"],
        )


class StubConnector:
    """
    Offline stub — used when Tableau is unreachable or in dry-run mode.
    Implements the same interface as TableauConnector but returns empty data.
    Domain agents will infer KPIs from field metadata alone (no live values).
    """

    def __enter__(self) -> "StubConnector":
        log.info("StubConnector: running in offline mode (no Tableau connection)")
        return self

    def __exit__(self, *_) -> None:
        pass

    def get_workbook_by_content_url(self, content_url: str) -> dict[str, Any]:
        return {
            "luid":         "offline",
            "name":         content_url,
            "content_url":  content_url,
            "project_name": None,
            "updated_at":   None,
        }

    def list_views(self, workbook_luid: str) -> list[dict[str, Any]]:
        return []

    def get_view_data_by_name(
        self,
        workbook_luid: str,
        view_name: str,
        max_rows: int = 200,
    ) -> list[dict[str, Any]]:
        log.info("StubConnector: fetch_view_data('%s') → empty (offline)", view_name)
        return []

    def get_view_data_by_luid(
        self,
        view_luid: str,
        max_rows: int = 200,
    ) -> list[dict[str, Any]]:
        return []
