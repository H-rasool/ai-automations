"""Unit tests for the Smartsheet middleware (Smartsheet API is mocked)."""
import os
import sys
from unittest import mock

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import middleware  # noqa: E402

KEY = "test-key"
SHEET = {
    "columns": [{"id": 1, "title": "Task Name"}, {"id": 2, "title": "Percentage Complete"}],
    "rows": [
        {
            "id": 100,
            "rowNumber": 1,
            "cells": [
                {"columnId": 1, "value": "Design", "displayValue": "Design"},
                {"columnId": 2, "value": 0.5, "displayValue": "50%"},
            ],
        },
        {
            "id": 101,
            "rowNumber": 2,
            "parentId": 100,
            "cells": [{"columnId": 1, "value": "Sub task"}],
        },
    ],
}


@pytest.fixture()
def client(monkeypatch):
    monkeypatch.setenv("MIDDLEWARE_API_KEY", KEY)
    monkeypatch.setenv("SMARTSHEET_TOKEN", "dummy-token")
    middleware.app.config["TESTING"] = True
    return middleware.app.test_client()


def _resp(payload, status=200):
    r = mock.Mock()
    r.status_code = status
    r.json.return_value = payload
    r.raise_for_status.return_value = None
    return r


AUTH = {"X-API-KEY": KEY}


def test_health_needs_no_auth(client):
    assert client.get("/health").get_json() == {"status": "ok"}


@pytest.mark.parametrize("headers", [{}, {"X-API-KEY": "wrong"}])
def test_rejects_missing_or_wrong_key(client, headers):
    assert client.get("/api/v1/extract-sheet?sheetId=1", headers=headers).status_code == 403
    assert client.post("/api/v1/update-row", json={}, headers=headers).status_code == 403


def test_fails_closed_when_no_key_configured(client, monkeypatch):
    monkeypatch.delenv("MIDDLEWARE_API_KEY")
    # an empty/missing header must NOT match an unset key
    assert client.get("/api/v1/extract-sheet?sheetId=1").status_code == 403


def test_extract_requires_sheet_id(client):
    assert client.get("/api/v1/extract-sheet", headers=AUTH).status_code == 400


def test_extract_maps_columns_and_hierarchy(client):
    with mock.patch.object(middleware.requests, "get", return_value=_resp(SHEET)):
        body = client.get("/api/v1/extract-sheet?sheetId=42", headers=AUTH).get_json()
    assert body["total_rows"] == 2
    first, second = body["data"]
    assert first["Task Name"] == "Design"
    assert first["Percentage Complete"] == "50%"  # displayValue preferred
    assert first["_metadata"] == {"rowId": 100, "rowNumber": 1, "parentId": None, "sheetId": "42"}
    assert second["_metadata"]["parentId"] == 100


def test_update_row_validates_payload(client):
    assert client.post("/api/v1/update-row", json={"sheetId": "1"}, headers=AUTH).status_code == 400


def test_update_row_ignores_unknown_columns(client):
    with mock.patch.object(middleware.requests, "get", return_value=_resp(SHEET)), mock.patch.object(
        middleware.requests, "put", return_value=_resp({})
    ) as put:
        ok = client.post(
            "/api/v1/update-row",
            json={"sheetId": "42", "rowId": "100", "updates": {"Percentage Complete": 0.8, "Nope": 1}},
            headers=AUTH,
        )
    assert ok.get_json()["status"] == "Success"
    sent = put.call_args.kwargs["json"][0]
    assert sent == {"id": "100", "cells": [{"columnId": 2, "value": 0.8}]}


def test_update_row_with_only_unknown_columns_is_a_noop(client):
    with mock.patch.object(middleware.requests, "get", return_value=_resp(SHEET)), mock.patch.object(
        middleware.requests, "put"
    ) as put:
        r = client.post(
            "/api/v1/update-row",
            json={"sheetId": "42", "rowId": "100", "updates": {"Nope": 1}},
            headers=AUTH,
        )
    assert r.status_code == 200 and not put.called


def test_update_batch_limits_and_counts(client):
    too_many = [{"rowId": str(i), "updates": {"Task Name": "x"}} for i in range(501)]
    assert (
        client.post("/api/v1/update-batch", json={"sheetId": "1", "batchUpdates": too_many}, headers=AUTH).status_code
        == 400
    )
    batch = [
        {"rowId": "100", "updates": {"Task Name": "A"}},
        {"rowId": "101", "updates": {"Unknown": "B"}},  # skipped
    ]
    with mock.patch.object(middleware.requests, "get", return_value=_resp(SHEET)), mock.patch.object(
        middleware.requests, "put", return_value=_resp({})
    ):
        body = client.post("/api/v1/update-batch", json={"sheetId": "1", "batchUpdates": batch}, headers=AUTH).get_json()
    assert body == {"status": "Success", "sheetId": "1", "updated_count": 1}
