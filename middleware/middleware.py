"""Smartsheet middleware for the n8n automations.

A small Flask API (served with Waitress) that sits between n8n and the Smartsheet
REST API. n8n workflows call it instead of talking to Smartsheet directly so that:

* the Smartsheet token never leaves this container (n8n only knows a shared API key),
* column *titles* are used instead of opaque column IDs,
* any number of sheets can be handled by passing ``sheetId`` per request.

Endpoints (all except /health require the ``X-API-KEY`` header):

    GET  /health                    liveness probe
    GET  /api/v1/extract-sheet      ?sheetId=...  -> every row, keyed by column title
    POST /api/v1/update-row         {"sheetId", "rowId", "updates": {...}}
    POST /api/v1/update-batch       {"sheetId", "batchUpdates": [{"rowId", "updates"}]}

Configuration (environment variables):

    SMARTSHEET_TOKEN     Smartsheet API access token
    MIDDLEWARE_API_KEY   shared secret n8n must send in the X-API-KEY header
"""
import hmac
import os

import requests
from flask import Flask, jsonify, request
from waitress import serve

SMARTSHEET_API = "https://api.smartsheet.com/2.0"
HTTP_TIMEOUT = 30  # seconds, for every outbound Smartsheet call
MAX_BATCH = 500  # Smartsheet limit for rows per update request

app = Flask(__name__)


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
def _authorized() -> bool:
    """Constant-time API-key check. Fails closed if no key is configured."""
    expected = os.getenv("MIDDLEWARE_API_KEY", "")
    provided = request.headers.get("X-API-KEY", "")
    return bool(expected) and hmac.compare_digest(provided.encode(), expected.encode())


def _smartsheet_headers(json_body: bool = False) -> dict:
    headers = {"Authorization": f"Bearer {os.getenv('SMARTSHEET_TOKEN', '')}"}
    if json_body:
        headers["Content-Type"] = "application/json"
    return headers


def _column_ids_by_title(sheet_id: str) -> dict:
    """Return {column title: column id} for a sheet."""
    response = requests.get(
        f"{SMARTSHEET_API}/sheets/{sheet_id}",
        headers=_smartsheet_headers(),
        timeout=HTTP_TIMEOUT,
    )
    response.raise_for_status()
    return {col["title"]: col["id"] for col in response.json()["columns"]}


def _cells(updates: dict, name_to_id: dict) -> list:
    """Translate {title: value} into Smartsheet cells, ignoring unknown titles."""
    return [
        {"columnId": name_to_id[title], "value": value}
        for title, value in updates.items()
        if title in name_to_id
    ]


# ---------------------------------------------------------------------------
# routes
# ---------------------------------------------------------------------------
@app.get("/health")
def health():
    return jsonify({"status": "ok"})


@app.get("/api/v1/extract-sheet")
def extract_sheet():
    """Fetch ALL rows of the sheet given by ?sheetId=..., keyed by column title.

    Every row carries a ``_metadata`` object (rowId, rowNumber, parentId, sheetId);
    ``parentId`` preserves the task hierarchy.
    """
    if not _authorized():
        return jsonify({"error": "Unauthorized Access"}), 403

    sheet_id = request.args.get("sheetId")
    if not sheet_id:
        return jsonify({"error": "Missing required query parameter: sheetId"}), 400

    try:
        response = requests.get(
            f"{SMARTSHEET_API}/sheets/{sheet_id}?includeAll=true",
            headers=_smartsheet_headers(),
            timeout=HTTP_TIMEOUT,
        )
        response.raise_for_status()
        data = response.json()

        col_titles = {col["id"]: col["title"] for col in data["columns"]}
        rows = []
        for row in data["rows"]:
            item = {
                "_metadata": {
                    "rowId": row["id"],
                    "rowNumber": row.get("rowNumber"),
                    "parentId": row.get("parentId"),
                    "sheetId": sheet_id,
                }
            }
            for cell in row["cells"]:
                title = col_titles.get(cell["columnId"])
                if title:
                    # prefer the formatted value over the raw one
                    item[title] = cell.get("displayValue", cell.get("value", ""))
            rows.append(item)

        return jsonify({"total_rows": len(rows), "sheetId": sheet_id, "data": rows})
    except requests.exceptions.HTTPError as err:
        status = err.response.status_code
        if status == 404:
            return jsonify({"error": f"Sheet {sheet_id} not found"}), 404
        return jsonify({"error": str(err)}), status
    except Exception as err:  # noqa: BLE001 - surface any failure to the caller
        return jsonify({"error": str(err)}), 500


@app.post("/api/v1/update-row")
def update_row():
    """Update one row. Body: {"sheetId": "...", "rowId": "...", "updates": {"Status": "Done"}}"""
    if not _authorized():
        return jsonify({"error": "Unauthorized Access"}), 403

    payload = request.get_json(silent=True) or {}
    sheet_id = payload.get("sheetId")
    row_id = payload.get("rowId")
    updates = payload.get("updates") or {}
    if not sheet_id or not row_id or not updates:
        return jsonify({"error": "Missing sheetId, rowId, or updates"}), 400

    try:
        cells = _cells(updates, _column_ids_by_title(sheet_id))
        if not cells:
            return jsonify({"status": "No valid columns to update"}), 200

        response = requests.put(
            f"{SMARTSHEET_API}/sheets/{sheet_id}/rows",
            headers=_smartsheet_headers(json_body=True),
            json=[{"id": row_id, "cells": cells}],
            timeout=HTTP_TIMEOUT,
        )
        response.raise_for_status()
        return jsonify(
            {
                "status": "Success",
                "sheetId": sheet_id,
                "updatedRow": row_id,
                "updatedFields": list(updates.keys()),
            }
        )
    except Exception as err:  # noqa: BLE001
        return jsonify({"error": str(err)}), 500


@app.post("/api/v1/update-batch")
def update_batch():
    """Update many rows at once (max 500).

    Body: {"sheetId": "...", "batchUpdates": [{"rowId": "...", "updates": {"Critical": true}}]}
    """
    if not _authorized():
        return jsonify({"error": "Unauthorized Access"}), 403

    payload = request.get_json(silent=True) or {}
    sheet_id = payload.get("sheetId")
    batch = payload.get("batchUpdates") or []
    if not sheet_id or not batch:
        return jsonify({"error": "Missing sheetId or batchUpdates"}), 400
    if len(batch) > MAX_BATCH:
        return jsonify({"error": f"batchUpdates exceeds the {MAX_BATCH}-row limit"}), 400

    try:
        name_to_id = _column_ids_by_title(sheet_id)
        rows = []
        for item in batch:
            cells = _cells(item.get("updates") or {}, name_to_id)
            if cells:
                rows.append({"id": item.get("rowId"), "cells": cells})

        if not rows:
            return jsonify({"status": "No valid updates found"}), 200

        response = requests.put(
            f"{SMARTSHEET_API}/sheets/{sheet_id}/rows",
            headers=_smartsheet_headers(json_body=True),
            json=rows,
            timeout=HTTP_TIMEOUT,
        )
        response.raise_for_status()
        return jsonify({"status": "Success", "sheetId": sheet_id, "updated_count": len(rows)})
    except Exception as err:  # noqa: BLE001
        return jsonify({"error": str(err)}), 500


if __name__ == "__main__":
    port = int(os.getenv("PORT", "5001"))
    print(f"Smartsheet middleware listening on 0.0.0.0:{port} (inside Docker: http://middleware:{port})")
    # Waitress handles concurrent requests with a small thread pool
    serve(app, host="0.0.0.0", port=port, threads=10)
