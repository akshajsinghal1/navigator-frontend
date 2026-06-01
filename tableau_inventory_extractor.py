"""Tableau workbook metadata inventory extractor.

Pulls a rich, content-free inventory of a single Tableau workbook by combining
the REST API (basic identity, views, connections, owner) with the Metadata API
GraphQL (fields, calculated-field formulas, parameters, upstream lineage,
data-quality warnings, extract refresh timestamps).

The resulting JSON is intended to feed an agent that reasons over schema and
lineage without ever seeing the underlying row data.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import tableauserverclient as TSC

log = logging.getLogger(__name__)


class WorkbookNotFoundError(LookupError):
    """Raised when no workbook on the site matches the requested content_url."""


_DQW_FRAGMENT = """
dataQualityWarnings {
  id
  message
  isActive
  isSevere
  createdAt
  updatedAt
}
"""

# Field selection used inside both embeddedDatasources and upstreamDatasources.
# Every concrete Field subtype the Metadata API exposes is unioned here so the
# agent sees not just columns and calculations but also hierarchies, groups,
# bins, sets, and combined fields with their composition. Fields that don't
# match any fragment still come back with the common header (name, description,
# isHidden, folderName) plus __typename.
_FIELD_FRAGMENT = """
fields {
  __typename
  id
  name
  description
  isHidden
  folderName
  ... on ColumnField      { dataType role aggregation }
  ... on CalculatedField  { dataType role formula }
  ... on HierarchyField   { fields { name __typename } }
  ... on GroupField       { dataType role dataCategory hasOther fields { name __typename } }
  ... on BinField         { dataType role formula binSize fields { name __typename } }
  ... on SetField         { fields { name __typename } }
  ... on CombinedField    { fields { name __typename } }
  ... on CombinedSetField { delimiter combinationType fields { name __typename } }
}
"""

WORKBOOK_GRAPHQL = """
query WorkbookInventory($luid: String!) {
  workbooks(filter: { luid: $luid }) {
    luid
    name
    projectName
    description
    vizportalUrlId
    createdAt
    updatedAt
    owner { luid name username email }
    tags { name }
    parameters {
      id
      name
      parentName
      referencedByCalculations { id name }
    }
    views { __typename luid name }
    embeddedDatasources {
      id
      name
      hasExtracts
      extractLastRefreshTime
      extractLastIncrementalUpdateTime
      extractLastUpdateTime
      """ + _FIELD_FRAGMENT + """
      upstreamDatabases { id name connectionType isEmbedded }
      upstreamTables {
        id
        name
        schema
        fullName
        description
        """ + _DQW_FRAGMENT + """
        columns { id name remoteType description isNullable }
      }
    }
    upstreamDatasources {
      luid
      id
      name
      projectName
      hasExtracts
      extractLastRefreshTime
      extractLastUpdateTime
      owner { luid name username email }
      """ + _DQW_FRAGMENT + """
      """ + _FIELD_FRAGMENT + """
      upstreamDatabases { id name connectionType }
      upstreamTables {
        id
        name
        schema
        fullName
        """ + _DQW_FRAGMENT + """
        columns { id name remoteType description }
      }
    }
  }
}
"""


class TableauInventoryExtractor:
    """Sign-in / extract / sign-out lifecycle for one Tableau site.

    Usage:
        with TableauInventoryExtractor(url, site, pat_name, pat_secret) as ex:
            inventory = ex.extract_workbook_inventory("Superstore")
            ex.write_to_json(inventory)
    """

    def __init__(
        self,
        server_url: str,
        site_name: str,
        pat_name: str,
        pat_secret: str,
    ) -> None:
        self._server_url = server_url
        self._site_name = site_name
        # Third positional arg is the site content URL (site_id in TSC terms).
        self._auth = TSC.PersonalAccessTokenAuth(pat_name, pat_secret, site_name)
        self._server = TSC.Server(server_url, use_server_version=True)
        self._signed_in = False

    # ------------------------------------------------------------------
    # context manager — guarantees sign_out even on exceptions
    # ------------------------------------------------------------------
    def __enter__(self) -> "TableauInventoryExtractor":
        self._server.auth.sign_in(self._auth)
        self._signed_in = True
        log.info(
            "signed in to %s (site=%s, api=%s)",
            self._server_url,
            self._site_name,
            self._server.version,
        )
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        if self._signed_in:
            try:
                self._server.auth.sign_out()
                log.info("signed out")
            except Exception as e:
                log.warning("sign_out failed: %s", e)
            self._signed_in = False

    # ------------------------------------------------------------------
    # public API
    # ------------------------------------------------------------------
    def extract_workbook_inventory(self, content_url: str) -> dict[str, Any]:
        wb = self._resolve_workbook(content_url)
        rest_block = self._enrich_rest(wb)
        graphql_block = self._fetch_workbook_graphql(wb.id)
        return self._assemble(rest_block, graphql_block)

    def write_to_json(self, inventory: dict[str, Any], output_dir: str = "output") -> str:
        out = Path(output_dir)
        out.mkdir(parents=True, exist_ok=True)
        name = inventory.get("workbook", {}).get("content_url") or "workbook"
        ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        path = out / f"inventory_{name}_{ts}.json"
        path.write_text(json.dumps(inventory, indent=2, default=str))
        log.info("inventory written to %s", path)
        return str(path)

    # ------------------------------------------------------------------
    # private — workbook resolution
    # ------------------------------------------------------------------
    def _resolve_workbook(self, content_url: str):
        opts = TSC.RequestOptions()
        opts.filter.add(
            TSC.Filter(
                TSC.RequestOptions.Field.Name,
                TSC.RequestOptions.Operator.Equals,
                content_url,
            )
        )
        matches, _ = self._server.workbooks.get(opts)
        for wb in matches:
            if wb.content_url == content_url or wb.name == content_url:
                log.info("resolved workbook by name filter: %s (luid=%s)", wb.name, wb.id)
                return wb

        log.info("name filter miss — scanning all workbooks for content_url=%s", content_url)
        for wb in TSC.Pager(self._server.workbooks):
            if wb.content_url == content_url:
                log.info("resolved workbook by scan: %s (luid=%s)", wb.name, wb.id)
                return wb

        raise WorkbookNotFoundError(
            f"no workbook with name or content_url == {content_url!r} on site {self._site_name!r}"
        )

    # ------------------------------------------------------------------
    # private — REST enrichment
    # ------------------------------------------------------------------
    def _enrich_rest(self, wb) -> dict[str, Any]:
        self._server.workbooks.populate_views(wb)
        self._server.workbooks.populate_connections(wb)

        owner = self._resolve_user(wb.owner_id) if wb.owner_id else None

        def _dt(v):
            return v.isoformat() if v is not None else None

        views = [
            {
                "luid": v.id,
                "name": v.name,
                "content_url": v.content_url,
                "created_at": _dt(v.created_at),
                "updated_at": _dt(v.updated_at),
                "tags": sorted(v.tags) if v.tags else [],
                "owner_id": v.owner_id,
            }
            for v in (wb.views or [])
        ]

        connections = [
            {
                "id": c.id,
                "datasource_id": c.datasource_id,
                "datasource_name": c.datasource_name,
                "connection_type": c.connection_type,
                "server_address": c.server_address,
                "server_port": c.server_port,
                "username": c.username,
                "embed_password": c.embed_password,
            }
            for c in (wb.connections or [])
        ]

        return {
            "workbook": {
                "luid": wb.id,
                "name": wb.name,
                "content_url": wb.content_url,
                "project_id": wb.project_id,
                "project_name": wb.project_name,
                "description": wb.description,
                "size": wb.size,
                "webpage_url": wb.webpage_url,
                "owner_id": wb.owner_id,
                "owner": owner,
                "tags": sorted(wb.tags) if wb.tags else [],
                "created_at": _dt(wb.created_at),
                "updated_at": _dt(wb.updated_at),
                "show_tabs": wb.show_tabs,
            },
            "views": views,
            "connections": connections,
        }

    def _resolve_user(self, user_id: str) -> dict[str, Any]:
        try:
            u = self._server.users.get_by_id(user_id)
            return {
                "luid": u.id,
                "name": getattr(u, "fullname", None) or u.name,
                "username": u.name,
                "email": getattr(u, "email", None),
                "site_role": getattr(u, "site_role", None),
            }
        except Exception as e:
            log.warning("could not resolve user %s: %s", user_id, e)
            return {"luid": user_id}

    # ------------------------------------------------------------------
    # private — Metadata API (GraphQL)
    # ------------------------------------------------------------------
    def _fetch_workbook_graphql(self, luid: str) -> dict[str, Any]:
        try:
            result = self._server.metadata.query(
                WORKBOOK_GRAPHQL,
                variables={"luid": luid},
                abort_on_error=False,
            )
        except Exception as e:
            log.warning("metadata graphql query raised: %s", e)
            return {}

        if isinstance(result, dict) and result.get("errors"):
            log.warning("metadata graphql partial errors: %s", result["errors"])

        data = (result or {}).get("data") or {}
        workbooks = data.get("workbooks") or []
        if not workbooks:
            log.warning("metadata returned no workbook for luid=%s", luid)
            return {}
        return workbooks[0]

    # ------------------------------------------------------------------
    # private — assemble final inventory dict
    # ------------------------------------------------------------------
    def _assemble(
        self,
        rest_block: dict[str, Any],
        graphql_block: dict[str, Any],
    ) -> dict[str, Any]:
        embedded = graphql_block.get("embeddedDatasources") or []
        published = graphql_block.get("upstreamDatasources") or []

        # Roll up unique upstream databases and tables across all datasources
        # so the agent can scan physical lineage without walking each datasource.
        databases: dict[str, dict[str, Any]] = {}
        tables: dict[str, dict[str, Any]] = {}
        # Roll up DQWs from each carrier (datasource or table) and tag the source
        # so a downstream agent can attribute the warning.
        dqws: list[dict[str, Any]] = []

        def _collect_dqws(carrier_kind: str, carrier: dict[str, Any]) -> None:
            for w in carrier.get("dataQualityWarnings") or []:
                dqws.append(
                    {
                        **w,
                        "attached_to_kind": carrier_kind,
                        "attached_to_id": carrier.get("id") or carrier.get("luid"),
                        "attached_to_name": carrier.get("name") or carrier.get("fullName"),
                    }
                )

        for ds in embedded:
            _collect_dqws("embedded_datasource", ds)
            for tbl in ds.get("upstreamTables") or []:
                _collect_dqws("upstream_table", tbl)
        for ds in published:
            _collect_dqws("published_datasource", ds)
            for tbl in ds.get("upstreamTables") or []:
                _collect_dqws("upstream_table", tbl)

        for ds in embedded + published:
            for db in ds.get("upstreamDatabases") or []:
                key = db.get("id") or db.get("name")
                if key:
                    databases[key] = db
            for tbl in ds.get("upstreamTables") or []:
                key = tbl.get("id") or tbl.get("fullName") or tbl.get("name")
                if key:
                    tables[key] = tbl

        wb_block = rest_block["workbook"]
        if graphql_block:
            wb_block["vizportal_url_id"] = graphql_block.get("vizportalUrlId")
            wb_block["created_at_metadata"] = graphql_block.get("createdAt")
            wb_block["updated_at_metadata"] = graphql_block.get("updatedAt")
            if graphql_block.get("owner"):
                wb_block["owner_metadata"] = graphql_block["owner"]

        return {
            "extracted_at": datetime.now(timezone.utc).isoformat(),
            "server": {
                "url": self._server_url,
                "site": self._site_name,
                "api_version": self._server.version,
            },
            "workbook": wb_block,
            "parameters": graphql_block.get("parameters") or [],
            "views": rest_block["views"],
            "graphql_views": graphql_block.get("views") or [],
            "connections": rest_block["connections"],
            "embedded_datasources": embedded,
            "published_datasources": published,
            "upstream_databases": list(databases.values()),
            "upstream_tables": list(tables.values()),
            "data_quality_warnings": dqws,
            "refresh_schedules": None,
            "notes": [
                "Tableau Cloud — full schedule objects not exposed; "
                "per-datasource extractLastRefreshTime / extractLastUpdateTime captured.",
                "Embedded vs published split derived from the Metadata API "
                "(embeddedDatasources vs upstreamDatasources).",
            ],
        }
