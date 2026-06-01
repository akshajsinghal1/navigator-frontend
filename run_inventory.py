"""CLI entry point — extract the Tableau inventory for the target workbook in .env."""

from __future__ import annotations

import logging
import os
import sys

from dotenv import load_dotenv

from tableau_inventory_extractor import TableauInventoryExtractor, WorkbookNotFoundError


REQUIRED = ("TABLEAU_PAT_NAME", "TABLEAU_PAT_SECRET", "TABLEAU_SERVER_URL", "TABLEAU_SITE_NAME")


def main() -> int:
    load_dotenv()

    missing = [k for k in REQUIRED if not os.environ.get(k)]
    if missing:
        sys.stderr.write(f"missing required env vars: {', '.join(missing)}\n")
        return 2

    content_url = os.environ.get("TARGET_WORKBOOK_CONTENT_URL", "Superstore")

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    extractor = TableauInventoryExtractor(
        server_url=os.environ["TABLEAU_SERVER_URL"],
        site_name=os.environ["TABLEAU_SITE_NAME"],
        pat_name=os.environ["TABLEAU_PAT_NAME"],
        pat_secret=os.environ["TABLEAU_PAT_SECRET"],
    )

    with extractor as ex:
        try:
            inventory = ex.extract_workbook_inventory(content_url)
        except WorkbookNotFoundError as e:
            sys.stderr.write(f"{e}\n")
            return 1
        path = ex.write_to_json(inventory)

    print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
