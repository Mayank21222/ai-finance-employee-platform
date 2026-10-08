"""Phase 6 section 9: no-lock-in export/import + read-only data sources.

Everything here is local: the zip is built in memory, the import runs
against a throwaway DASH_DB, and the data sources are temp CSV/SQLite
files. No network and no real services involved.
"""

import json
import os
import sqlite3
import tempfile
from pathlib import Path

os.environ.setdefault("DASH_DB",
                      str(Path(tempfile.mkdtemp()) / "dash_export.db"))

import pytest  # noqa: E402

from ai_operator.graph import DEFAULT_FLOW_PATH  # noqa: E402
from ai_operator.tracing import RUNS_ROOT  # noqa: E402
from platform.dashboard import db, flowcfg, exporter  # noqa: E402
from platform.dashboard.app import app  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

db.init_db()
client = TestClient(app)

CONFIG_PATH = Path(DEFAULT_FLOW_PATH)


@pytest.fixture()
def flow_bytes():
    """The shipped flow config must survive every test."""
    before = CONFIG_PATH.read_bytes()
    yield
    CONFIG_PATH.write_bytes(before)


@pytest.fixture()
def clean_sources():
    yield
    with db.connect() as con:
        con.execute("DELETE FROM saved_queries")
        con.execute("DELETE FROM data_sources")
    flowcfg.publish_datasources()


def _mk_source(tmp: Path, name: str, kind: str, content) -> int:
    if kind == "csv":
        path = tmp / f"{name}.csv"
        path.write_text(content)
    else:
        path = tmp / f"{name}.db"
        con = sqlite3.connect(path)
        con.executescript(content)
        con.commit()
        con.close()
    return db.add_data_source(name, kind, str(path))


# --- export / import ---------------------------------------------------------


def test_export_endpoint_returns_a_zip():
    resp = client.get("/export/acme")
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "application/zip"
    assert "comp_ops_acme_export.zip" in \
        resp.headers.get("content-disposition", "")
    import io
    import zipfile

    with zipfile.ZipFile(io.BytesIO(resp.content)) as z:
        names = set(z.namelist())
        assert "manifest.json" in names
        assert "flow.json" in names
        assert "sessions.json" in names
        assert "runs.json" in names
        manifest = json.loads(z.read("manifest.json"))
        assert manifest["kind"] == "tenant-export"
        assert manifest["tenant"] == "acme"
        assert "api_keys" in manifest["omitted"]
        sessions = json.loads(z.read("sessions.json"))
        assert all(s["tenant"] == "acme" for s in sessions)


def test_export_unknown_tenant_is_404():
    resp = client.get("/export/no-such-tenant")
    assert resp.status_code == 404
    assert "no sessions for tenant" in resp.text


def test_connector_secrets_are_redacted_in_the_export():
    db.add_connector("secretconn", "https://api.example.com",
                     {"Authorization": "Bearer super-secret",
                      "Accept": "application/json"})
    try:
        data = exporter.export_zip("acme")
        import io
        import zipfile

        with zipfile.ZipFile(io.BytesIO(data)) as z:
            conns = json.loads(z.read("connectors.json"))
        hit = next(c for c in conns if c["name"] == "secretconn")
        assert hit["headers"]["Authorization"] == "***REDACTED***"
        assert hit["headers"]["Accept"] == "application/json"
        assert b"super-secret" not in data
    finally:
        with db.connect() as con:
            con.execute("DELETE FROM connectors WHERE name = ?",
                        ("secretconn",))
        flowcfg.publish_connectors()


def _seed_run(task: str = "seeded export task") -> str:
    """One run under an acme session so the archive really has runs."""
    sid = next(s["id"] for s in db.list_sessions()
               if s["tenant"] == "acme")
    run_id = "t_export_seed_" + os.urandom(4).hex()
    db.record_run(run_id, sid, task)
    return run_id


def _drop_run(run_id: str) -> None:
    import shutil

    with db.connect() as con:
        con.execute("DELETE FROM runs WHERE run_id = ?", (run_id,))
    shutil.rmtree(RUNS_ROOT / run_id, ignore_errors=True)


def test_round_trip_into_empty_db_keeps_flow_and_run_counts(flow_bytes):
    import io
    import zipfile

    seed = _seed_run()
    try:
        data = exporter.export_zip("acme")
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            zip_runs = json.loads(z.read("runs.json"))
            zip_flow = json.loads(z.read("flow.json"))
        assert any(r["run_id"] == seed for r in zip_runs)
        flow_before = flowcfg.load_cfg()

        old_db = os.environ.get("DASH_DB")
        fresh = Path(tempfile.mkdtemp()) / "restored.db"
        try:
            os.environ["DASH_DB"] = str(fresh)
            db.init_db()
            assert db.list_runs(100_000) == []  # genuinely empty
            summary = exporter.import_zip(data)
            assert summary["runs"] == len(zip_runs)
            assert len(db.list_runs(100_000)) == len(zip_runs)
            assert flowcfg.load_cfg() == zip_flow  # identical flow config
            assert flowcfg.load_cfg() == flow_before  # source untouched
            # importing twice is refused: not an empty database any more
            with pytest.raises(ValueError, match="already has runs"):
                exporter.import_zip(data)
        finally:
            os.environ["DASH_DB"] = old_db
    finally:
        _drop_run(seed)


def test_import_refuses_a_non_export_archive(flow_bytes):
    import io

    bogus = io.BytesIO()
    import zipfile

    with zipfile.ZipFile(bogus, "w") as z:
        z.writestr("readme.txt", "hello")
    with pytest.raises(ValueError, match="tenant export"):
        exporter.import_zip(bogus.getvalue())


# --- fin CLI -----------------------------------------------------------------


def test_cli_export_and_import_round_trip(flow_bytes, tmp_path):
    from platform import cli

    seed = _seed_run("cli seed task")
    try:
        out = tmp_path / "tenant.zip"
        assert cli.main(["export", "--tenant", "acme",
                         "-o", str(out)]) == 0
        assert out.is_file() and out.read_bytes()[:2] == b"PK"
        assert cli.main(["export", "--tenant", "ghost"]) == 1

        import io
        import zipfile

        with zipfile.ZipFile(out) as z:
            zip_runs = json.loads(z.read("runs.json"))
        assert any(r["run_id"] == seed for r in zip_runs)
        run_count = len(zip_runs)

        old_db = os.environ.get("DASH_DB")
        fresh = Path(tempfile.mkdtemp()) / "cli_restored.db"
        try:
            os.environ["DASH_DB"] = str(fresh)
            assert cli.main(["import", str(out)]) == 0
            assert len(db.list_runs(100_000)) == run_count
            assert cli.main(["import", str(out)]) == 1  # refused: runs exist
        finally:
            os.environ["DASH_DB"] = old_db
    finally:
        _drop_run(seed)


# --- data sources page -------------------------------------------------------


def test_data_sources_page_crud_and_reserved_names(clean_sources):
    body = client.get("/connectors").text
    assert "Data sources (read-only)" in body
    assert "Saved queries" in body
    # invalid source type refused
    bad = client.post("/connectors/sources",
                      data={"name": "x", "type": "excel", "path": "x.xlsx"})
    assert bad.status_code == 400
    assert "csv or sqlite" in bad.text
    # add a source
    with tempfile.TemporaryDirectory() as tmp:
        csv_path = Path(tmp) / "v.csv"
        csv_path.write_text("vendor,amount\nAcme,10\n")
        ok = client.post("/connectors/sources", data={
            "name": "vendors", "type": "csv", "path": str(csv_path)},
            follow_redirects=False)
        assert ok.status_code == 303
        page = client.get("/connectors").text
        assert "vendors" in page
        # duplicate name refused
        dup = client.post("/connectors/sources", data={
            "name": "vendors", "type": "csv", "path": str(csv_path)})
        assert dup.status_code == 400 and "already exists" in dup.text
        source_id = db.list_data_sources()[0]["id"]
        # reserved builtin tool name refused for a query
        reserved = client.post("/connectors/queries", data={
            "source_id": source_id, "name": "read_memory",
            "sql": "SELECT 1"})
        assert reserved.status_code == 400
        assert "already exists" in reserved.text or "reserved" in reserved.text
        # a valid query registers as a tool immediately
        added = client.post("/connectors/queries", data={
            "source_id": source_id, "name": "vendor_list",
            "sql": "SELECT vendor FROM data"},
            follow_redirects=False)
        assert added.status_code == 303
        from ai_operator.tools import datasources

        assert "vendor_list" in datasources.tool_names()
        assert "vendor_list" in client.get("/connectors").text
        # deleting the source removes its queries and unregisters the tool
        client.post(f"/connectors/sources/{source_id}/delete")
        assert db.list_saved_queries() == []
        assert "vendor_list" not in datasources.tool_names()


# --- saved queries as read tools --------------------------------------------


def test_saved_query_runs_with_bound_params_and_no_agent_sql(clean_sources,
                                                              tmp_path):
    from ai_operator.tools import datasources, registry
    from ai_operator.tools import connectors as conn_tools

    csv_path = tmp_path / "vendors.csv"
    csv_path.write_text("vendor,amount\nAcme,100\nBeta,200\n")
    source_id = db.add_data_source("v", "csv", str(csv_path))
    db.add_saved_query(source_id, "big_vendors",
                       "SELECT vendor, amount FROM data "
                       "WHERE amount > {{vars.min}}")
    assert flowcfg.publish_datasources() == []

    spec = registry.get("big_vendors")
    assert spec.level.value == "read"          # same role checks as reads

    conn_tools.set_runtime({"min": "150"}, {})
    res = registry.run("big_vendors", {})
    assert res.ok
    assert "Beta" in res.output and "Acme" not in res.output

    # the agent cannot smuggle SQL: extra args are rejected before the handler
    res = registry.run("big_vendors", {"sql": "DELETE FROM data"})
    assert not res.ok
    assert "INVALID_ARGUMENTS" in res.output

    # a query that was never saved does not exist as a tool
    with pytest.raises(registry.UnknownToolError):
        registry.run("vendor_list", {})

    # a variable full of quotes cannot break out (bound parameter): as SQL
    # text it would be a syntax error; bound as a value it simply matches
    # nothing lexicographically
    conn_tools.set_runtime({"min": "ZZZ' OR '1'='1"}, {})
    res = registry.run("big_vendors", {})
    assert res.ok
    assert "no rows matched" in res.output
    conn_tools.set_runtime({}, {})


def test_sqlite_source_is_read_only(clean_sources, tmp_path):
    from ai_operator.tools import registry

    db_path = tmp_path / "erp.db"
    con = sqlite3.connect(db_path)
    con.execute("CREATE TABLE invoices (id INTEGER, vendor TEXT)")
    con.execute("INSERT INTO invoices VALUES (1, 'Acme')")
    con.commit()
    con.close()
    source_id = db.add_data_source("erp", "sqlite", str(db_path))
    db.add_saved_query(source_id, "invoice_rows",
                       "SELECT vendor FROM invoices")
    flowcfg.publish_datasources()
    res = registry.run("invoice_rows", {})
    assert res.ok and "Acme" in res.output
    # even a human-authored write query cannot write: the source opens read-only
    db.add_saved_query(source_id, "wipe", "DELETE FROM invoices")
    flowcfg.publish_datasources()
    res = registry.run("wipe", {})
    assert not res.ok and "OperationalError" in res.output
    # rows are still there
    con = sqlite3.connect(db_path)
    assert con.execute("SELECT COUNT(*) FROM invoices").fetchone()[0] == 1
    con.close()


def test_missing_source_file_is_a_visible_error(clean_sources, tmp_path):
    from ai_operator.tools import registry

    source_id = db.add_data_source("ghost", "csv",
                                   str(tmp_path / "nope.csv"))
    db.add_saved_query(source_id, "ghost_rows", "SELECT 1 AS x")
    flowcfg.publish_datasources()
    res = registry.run("ghost_rows", {})
    assert not res.ok
    assert "not found" in res.output
