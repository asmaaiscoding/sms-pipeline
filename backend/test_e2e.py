"""
End-to-end integration test — simulates a real user's journey through the API.

Uses an in-memory SQLite database (no PostgreSQL/Redis required).
Run:  python test_e2e.py   (from backend/)
"""

from __future__ import annotations

import asyncio
import sys
import os
import uuid as uuid_mod

# ── Patch PostgreSQL dialect types to work with SQLite ────────────────────────
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.dialects.postgresql import UUID as PG_UUID, JSON as PG_JSON

@compiles(PG_UUID, "sqlite")
def _compile_uuid_sqlite(element, compiler, **kw):
    return "VARCHAR(36)"

@compiles(PG_JSON, "sqlite")
def _compile_json_sqlite(element, compiler, **kw):
    return "TEXT"

# Monkey-patch bind/result processors so UUID columns work on SQLite
_orig_uuid_bind = PG_UUID.bind_processor
_orig_uuid_result = PG_UUID.result_processor

def _patched_uuid_bind(self, dialect):
    if dialect.name == "sqlite":
        def process(value):
            if value is None:
                return None
            if isinstance(value, uuid_mod.UUID):
                return str(value)
            return str(value)
        return process
    return _orig_uuid_bind(self, dialect)

def _patched_uuid_result(self, dialect, coltype):
    if dialect.name == "sqlite":
        def process(value):
            if value is None:
                return None
            if isinstance(value, uuid_mod.UUID):
                return value
            return uuid_mod.UUID(str(value))
        return process
    return _orig_uuid_result(self, dialect, coltype)

PG_UUID.bind_processor = _patched_uuid_bind
PG_UUID.result_processor = _patched_uuid_result

# Monkey-patch JSON bind/result for SQLite (store as JSON strings)
import json as _json

_orig_json_bind = PG_JSON.bind_processor
_orig_json_result = PG_JSON.result_processor

def _patched_json_bind(self, dialect):
    if dialect.name == "sqlite":
        def process(value):
            if value is None:
                return None
            return _json.dumps(value)
        return process
    return _orig_json_bind(self, dialect)

def _patched_json_result(self, dialect, coltype):
    if dialect.name == "sqlite":
        def process(value):
            if value is None:
                return None
            if isinstance(value, str):
                return _json.loads(value)
            return value
        return process
    return _orig_json_result(self, dialect, coltype)

PG_JSON.bind_processor = _patched_json_bind
PG_JSON.result_processor = _patched_json_result


# ── Imports ───────────────────────────────────────────────────────────────────
sys.path.insert(0, os.path.dirname(__file__))

from uuid import UUID
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession

from app.database import Base, get_db
from app.main import app


# ── Test database setup ──────────────────────────────────────────────────────
TEST_DB_URL = "sqlite+aiosqlite:///:memory:"

test_engine = create_async_engine(TEST_DB_URL, echo=False)
test_session_factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)


async def override_get_db():
    async with test_session_factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()


app.dependency_overrides[get_db] = override_get_db


# ── Helpers ──────────────────────────────────────────────────────────────────

PASS = "\033[92m✓\033[0m"
FAIL = "\033[91m✗\033[0m"
WARN = "\033[93m⚠\033[0m"

passed = 0
failed = 0
total_tests = 0


def check(test_name: str, condition: bool, detail: str = ""):
    global passed, failed, total_tests
    total_tests += 1
    if condition:
        passed += 1
        print(f"  {PASS} {test_name}")
    else:
        failed += 1
        msg = f"  {FAIL} {test_name}"
        if detail:
            msg += f" — {detail}"
        print(msg)


BASE = "http://testserver/api/v1"


# ══════════════════════════════════════════════════════════════════════════════
#  Test Scenarios
# ══════════════════════════════════════════════════════════════════════════════

async def run_all_tests():
    # Create tables
    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url=BASE) as client:

        # ── 1. Health Check ──────────────────────────────────────────────────
        print("\n🏥 Health & Root Endpoints")

        r = await client.get("http://testserver/health")
        check("GET /health returns 200", r.status_code == 200)
        check("Health status is 'healthy'", r.json().get("status") == "healthy")

        r = await client.get("http://testserver/")
        check("GET / returns app info", r.status_code == 200 and "SMS Pipeline" in r.json().get("name", ""))

        # ── 2. Swagger Docs ──────────────────────────────────────────────────
        print("\n📖 API Documentation")

        r = await client.get("http://testserver/docs")
        check("GET /docs returns 200 (Swagger UI)", r.status_code == 200)

        r = await client.get("http://testserver/redoc")
        check("GET /redoc returns 200 (ReDoc)", r.status_code == 200)

        r = await client.get("http://testserver/openapi.json")
        check("GET /openapi.json returns schema", r.status_code == 200)
        schema = r.json()
        check("OpenAPI has paths defined", len(schema.get("paths", {})) > 10)

        # ── 3. Register ─────────────────────────────────────────────────────
        print("\n👤 User Registration")

        r = await client.post(f"{BASE}/auth/register", json={
            "email": "alice@example.com",
            "password": "SecurePass123!",
            "full_name": "Alice Researcher",
        })
        check("Register new user → 201", r.status_code == 201, f"got {r.status_code}: {r.text[:200]}")
        user_data = r.json()
        check("Response has user ID (UUID)", "id" in user_data)
        check("Email returned correctly", user_data.get("email") == "alice@example.com")
        check("Full name returned", user_data.get("full_name") == "Alice Researcher")
        check("User is active", user_data.get("is_active") is True)

        # Duplicate registration
        r = await client.post(f"{BASE}/auth/register", json={
            "email": "alice@example.com",
            "password": "AnotherPass456!",
            "full_name": "Alice Clone",
        })
        check("Duplicate email → 409 Conflict", r.status_code == 409, f"got {r.status_code}")

        # Validation errors
        r = await client.post(f"{BASE}/auth/register", json={
            "email": "not-an-email",
            "password": "short",
            "full_name": "",
        })
        check("Invalid data → 422 Validation Error", r.status_code == 422)

        # ── 4. Login ─────────────────────────────────────────────────────────
        print("\n🔐 Login & JWT Authentication")

        r = await client.post(f"{BASE}/auth/login", json={
            "email": "alice@example.com",
            "password": "SecurePass123!",
        })
        check("Login → 200", r.status_code == 200, f"got {r.status_code}: {r.text[:200]}")
        tokens = r.json()
        check("Response has access_token", "access_token" in tokens)
        check("Response has refresh_token", "refresh_token" in tokens)
        check("Token type is 'bearer'", tokens.get("token_type") == "bearer")

        access_token = tokens["access_token"]
        refresh_token = tokens["refresh_token"]
        auth = {"Authorization": f"Bearer {access_token}"}

        # Wrong password
        r = await client.post(f"{BASE}/auth/login", json={
            "email": "alice@example.com",
            "password": "WrongPassword!",
        })
        check("Wrong password → 401", r.status_code == 401)

        # Non-existent user
        r = await client.post(f"{BASE}/auth/login", json={
            "email": "nobody@example.com",
            "password": "whatever123!",
        })
        check("Unknown user → 401", r.status_code == 401)

        # ── 5. Token Refresh ─────────────────────────────────────────────────
        print("\n🔄 Token Refresh")

        r = await client.post(f"{BASE}/auth/refresh", json={
            "refresh_token": refresh_token,
        })
        check("Refresh token → 200 with new tokens", r.status_code == 200)
        check("New access_token present", "access_token" in r.json())

        r = await client.post(f"{BASE}/auth/refresh", json={
            "refresh_token": "invalid.token.here",
        })
        check("Invalid refresh token → 401", r.status_code == 401)

        # ── 6. Get Current User ──────────────────────────────────────────────
        print("\n👤 Get Current User (authenticated)")

        r = await client.get(f"{BASE}/auth/me", headers=auth)
        check("GET /auth/me → 200", r.status_code == 200)
        me = r.json()
        check("Returns correct email", me.get("email") == "alice@example.com")
        check("Returns correct name", me.get("full_name") == "Alice Researcher")

        r = await client.get(f"{BASE}/auth/me")
        check("GET /auth/me without token → 403", r.status_code == 403)

        r = await client.get(f"{BASE}/auth/me", headers={"Authorization": "Bearer invalid"})
        check("GET /auth/me with bad token → 401", r.status_code == 401)

        # ── 7. Create Projects ───────────────────────────────────────────────
        print("\n📁 Project CRUD")

        # Create detection project with preset strings
        r = await client.post(f"{BASE}/projects", headers=auth, json={
            "name": "Vuln Detection 2020-2025",
            "description": "SMS on software vulnerability detection techniques",
            "topic": "detection",
            "year_start": 2020,
            "year_end": 2025,
            "sources": ["acm", "ieee", "springer"],
            "use_preset_strings": True,
        })
        check("Create detection project → 201", r.status_code == 201, f"got {r.status_code}: {r.text[:300]}")
        project1 = r.json()
        project1_id = project1["id"]
        check("Project has UUID id", len(project1_id) == 36)
        check("Project name set correctly", project1["name"] == "Vuln Detection 2020-2025")
        check("Topic is 'detection'", project1["topic"] == "detection")
        check("Status is 'draft'", project1["status"] == "draft")
        check("Year range correct", project1["year_start"] == 2020 and project1["year_end"] == 2025)
        check("Sources stored", "acm" in project1["sources"])
        check("Preset search strings loaded (DET-1 to DET-5)", len(project1.get("search_strings", [])) == 5, f"got {len(project1.get('search_strings', []))}")
        if project1.get("search_strings"):
            labels = [ss["label"] for ss in project1["search_strings"]]
            check("Search string labels: DET-1..DET-5", "DET-1" in labels and "DET-5" in labels, str(labels))

        # Create remediation project
        r = await client.post(f"{BASE}/projects", headers=auth, json={
            "name": "Vuln Remediation Survey",
            "description": "Remediation and patching techniques",
            "topic": "remediation",
            "year_start": 2015,
            "year_end": 2026,
            "sources": ["ieee", "springer"],
            "use_preset_strings": True,
        })
        check("Create remediation project → 201", r.status_code == 201)
        project2 = r.json()
        project2_id = project2["id"]
        check("Remediation preset strings loaded (REM-1 to REM-4)", len(project2.get("search_strings", [])) == 4)

        # Create custom project
        r = await client.post(f"{BASE}/projects", headers=auth, json={
            "name": "Custom Research",
            "description": "My own custom topic",
            "topic": "custom",
            "year_start": 2022,
            "year_end": 2026,
            "sources": ["acm"],
            "use_preset_strings": False,
            "search_strings": [
                {"label": "CUSTOM-1", "query": "machine learning AND security"},
                {"label": "CUSTOM-2", "query": "deep learning AND vulnerability"},
            ],
        })
        check("Create custom project → 201", r.status_code == 201)
        project3 = r.json()
        project3_id = project3["id"]
        check("Custom search strings saved", len(project3.get("search_strings", [])) == 2)

        # ── 8. List Projects ─────────────────────────────────────────────────
        print("\n📋 List Projects (paginated)")

        r = await client.get(f"{BASE}/projects", headers=auth)
        check("List projects → 200", r.status_code == 200)
        data = r.json()
        check("Returns paginated response", "items" in data and "total" in data)
        check("Total is 3 projects", data["total"] == 3, f"got {data['total']}")
        check("Page defaults to 1", data["page"] == 1)
        check("Items list has 3 projects", len(data["items"]) == 3)

        # Pagination
        r = await client.get(f"{BASE}/projects?page=1&per_page=2", headers=auth)
        data = r.json()
        check("Pagination: per_page=2 returns 2 items", len(data["items"]) == 2)
        check("Pagination: pages=2", data["pages"] == 2)

        r = await client.get(f"{BASE}/projects?page=2&per_page=2", headers=auth)
        data = r.json()
        check("Pagination: page 2 has 1 item", len(data["items"]) == 1)

        # Unauthorized
        r = await client.get(f"{BASE}/projects")
        check("List projects without auth → 403", r.status_code == 403)

        # ── 9. Get Single Project ────────────────────────────────────────────
        print("\n🔍 Get Project Detail")

        r = await client.get(f"{BASE}/projects/{project1_id}", headers=auth)
        check("Get project → 200", r.status_code == 200)
        detail = r.json()
        check("Returns full project with search_strings", len(detail.get("search_strings", [])) > 0)
        check("Has timestamps", "created_at" in detail and "updated_at" in detail)

        # Non-existent project
        r = await client.get(f"{BASE}/projects/00000000-0000-0000-0000-000000000000", headers=auth)
        check("Get non-existent project → 404", r.status_code == 404)

        # ── 10. Update Project ───────────────────────────────────────────────
        print("\n✏️ Update Project")

        r = await client.patch(f"{BASE}/projects/{project1_id}", headers=auth, json={
            "name": "Vulnerability Detection SMS (Updated)",
            "description": "Updated description with new scope",
            "year_end": 2026,
        })
        check("Patch project → 200", r.status_code == 200, f"got {r.status_code}: {r.text[:200]}")
        updated = r.json()
        check("Name updated", updated["name"] == "Vulnerability Detection SMS (Updated)")
        check("Year end updated to 2026", updated["year_end"] == 2026)

        # ── 11. Papers (empty project) ───────────────────────────────────────
        print("\n📄 Papers (empty project)")

        r = await client.get(f"{BASE}/projects/{project1_id}/papers", headers=auth)
        check("List papers → 200", r.status_code == 200)
        papers_data = r.json()
        check("Papers total is 0", papers_data["total"] == 0)
        check("Papers items is empty list", papers_data["items"] == [])

        # Paper stats
        r = await client.get(f"{BASE}/projects/{project1_id}/papers/stats", headers=auth)
        check("Paper stats → 200", r.status_code == 200)
        stats = r.json()
        check("Stats total is 0", stats["total"] == 0)
        check("Stats has by_source", "by_source" in stats)
        check("Stats has by_year", "by_year" in stats)
        check("Stats has by_relevance", "by_relevance" in stats)
        check("Stats has duplicates count", "duplicates" in stats)
        check("Stats has with_pdf count", "with_pdf" in stats)

        # ── 12. Manually insert papers to test filtering/export ──────────────
        print("\n📥 Insert Test Papers (direct DB)")

        from app.models.paper import Paper, RelevanceDecision

        async with test_session_factory() as db:
            papers = [
                Paper(project_id=UUID(project1_id), title="Deep Learning for Vulns", authors="John Doe, Jane Smith",
                      year=2023, doi="10.1145/111111", source="acm", relevance=RelevanceDecision.KEEP,
                      publisher="ACM", search_string_label="DET-1"),
                Paper(project_id=UUID(project1_id), title="Static Analysis Survey", authors="Bob Builder",
                      year=2022, doi="10.1109/222222", source="ieee", relevance=RelevanceDecision.KEEP,
                      publisher="IEEE", search_string_label="DET-2"),
                Paper(project_id=UUID(project1_id), title="Irrelevant Paper", authors="Nobody",
                      year=2021, doi="10.1007/333333", source="springer", relevance=RelevanceDecision.REMOVE,
                      publisher="Springer", search_string_label="DET-3"),
                Paper(project_id=UUID(project1_id), title="Duplicate Paper", authors="Clone Author",
                      year=2023, doi="10.1145/111111", source="acm", relevance=RelevanceDecision.PENDING,
                      is_duplicate=True, publisher="ACM", search_string_label="DET-1"),
                Paper(project_id=UUID(project1_id), title="Paper With PDF", authors="PDF Author",
                      year=2024, doi="10.1145/444444", source="acm", relevance=RelevanceDecision.KEEP,
                      pdf_link="https://arxiv.org/pdf/2024.12345.pdf", pdf_source="arxiv",
                      publisher="ACM", search_string_label="DET-5"),
            ]
            for p in papers:
                db.add(p)
            await db.commit()

        # ── 13. Papers with data ─────────────────────────────────────────────
        print("\n📄 Papers (with data)")

        r = await client.get(f"{BASE}/projects/{project1_id}/papers", headers=auth)
        check("Papers now has 5 total", r.json()["total"] == 5, f"got {r.json()['total']}")

        # Filter by source
        r = await client.get(f"{BASE}/projects/{project1_id}/papers?source=acm", headers=auth)
        check("Filter source=acm → 3 papers", r.json()["total"] == 3, f"got {r.json()['total']}")

        r = await client.get(f"{BASE}/projects/{project1_id}/papers?source=ieee", headers=auth)
        check("Filter source=ieee → 1 paper", r.json()["total"] == 1)

        # Filter by year
        r = await client.get(f"{BASE}/projects/{project1_id}/papers?year=2023", headers=auth)
        check("Filter year=2023 → 2 papers", r.json()["total"] == 2, f"got {r.json()['total']}")

        # Filter by relevance
        r = await client.get(f"{BASE}/projects/{project1_id}/papers?relevance=keep", headers=auth)
        check("Filter relevance=keep → 3 papers", r.json()["total"] == 3, f"got {r.json()['total']}")

        r = await client.get(f"{BASE}/projects/{project1_id}/papers?relevance=remove", headers=auth)
        check("Filter relevance=remove → 1 paper", r.json()["total"] == 1)

        # Filter by duplicate
        r = await client.get(f"{BASE}/projects/{project1_id}/papers?is_duplicate=true", headers=auth)
        check("Filter is_duplicate=true → 1 paper", r.json()["total"] == 1)

        # Filter by has_pdf
        r = await client.get(f"{BASE}/projects/{project1_id}/papers?has_pdf=true", headers=auth)
        check("Filter has_pdf=true → 1 paper", r.json()["total"] == 1)

        r = await client.get(f"{BASE}/projects/{project1_id}/papers?has_pdf=false", headers=auth)
        check("Filter has_pdf=false → 4 papers", r.json()["total"] == 4)

        # Search
        r = await client.get(f"{BASE}/projects/{project1_id}/papers?search=Deep+Learning", headers=auth)
        check("Search 'Deep Learning' → 1 paper", r.json()["total"] == 1)

        # Combined filters
        r = await client.get(f"{BASE}/projects/{project1_id}/papers?source=acm&relevance=keep", headers=auth)
        check("Combined filter: acm + keep → 2 papers", r.json()["total"] == 2, f"got {r.json()['total']}")

        # Pagination
        r = await client.get(f"{BASE}/projects/{project1_id}/papers?per_page=2&page=1", headers=auth)
        check("Paper pagination: 2 per page, page 1", len(r.json()["items"]) == 2)
        check("Paper pagination: 3 total pages", r.json()["pages"] == 3)

        # ── 14. Paper Stats (with data) ──────────────────────────────────────
        print("\n📊 Paper Stats (with data)")

        r = await client.get(f"{BASE}/projects/{project1_id}/papers/stats", headers=auth)
        stats = r.json()
        check("Stats total = 5", stats["total"] == 5, f"got {stats['total']}")
        check("Stats by_source has 'acm'", "acm" in stats["by_source"])
        check("Stats by_source['acm'] = 3", stats["by_source"].get("acm") == 3, str(stats["by_source"]))
        check("Stats by_year has 2023", 2023 in stats["by_year"] or "2023" in stats["by_year"])
        check("Stats duplicates = 1", stats["duplicates"] == 1)
        check("Stats with_pdf = 1", stats["with_pdf"] == 1)
        check("Stats by_relevance has 'keep'", "keep" in stats["by_relevance"])

        # ── 15. Get Single Paper & Update ────────────────────────────────────
        print("\n📝 Single Paper & Update")

        papers_list = (await client.get(f"{BASE}/projects/{project1_id}/papers", headers=auth)).json()["items"]
        first_paper_id = papers_list[0]["id"]

        r = await client.get(f"{BASE}/projects/{project1_id}/papers/{first_paper_id}", headers=auth)
        check("Get single paper → 200", r.status_code == 200)
        paper_detail = r.json()
        check("Paper has full fields (abstract, link, doi)", "abstract" in paper_detail and "doi" in paper_detail)

        # Update relevance
        r = await client.patch(f"{BASE}/projects/{project1_id}/papers/{first_paper_id}", headers=auth, json={
            "relevance": "remove",
        })
        check("Update paper relevance → 200", r.status_code == 200, f"got {r.status_code}: {r.text[:200]}")

        # Update PDF link
        r = await client.patch(f"{BASE}/projects/{project1_id}/papers/{first_paper_id}", headers=auth, json={
            "pdf_link": "https://example.com/paper.pdf",
        })
        check("Update paper PDF link → 200", r.status_code == 200)

        # ── 16. Export ───────────────────────────────────────────────────────
        print("\n📦 Export")

        r = await client.get(f"{BASE}/projects/{project1_id}/export?format=csv", headers=auth)
        check("Export CSV → 200", r.status_code == 200)
        check("CSV content-type", "text/csv" in r.headers.get("content-type", ""))
        csv_text = r.text
        check("CSV has header row", "Title" in csv_text and "DOI" in csv_text)
        # exclude_duplicates is default True, so 4 non-dup papers
        csv_lines = [l for l in csv_text.strip().split("\n") if l.strip()]
        check("CSV has 4 data rows (no duplicates)", len(csv_lines) == 5, f"got {len(csv_lines)} lines")  # header + 4

        r = await client.get(f"{BASE}/projects/{project1_id}/export?format=csv&include_duplicates=true", headers=auth)
        csv_lines = [l for l in r.text.strip().split("\n") if l.strip()]
        check("CSV with duplicates has 5 data rows", len(csv_lines) == 6, f"got {len(csv_lines)} lines")

        r = await client.get(f"{BASE}/projects/{project1_id}/export?format=csv&relevance_filter=keep", headers=auth)
        csv_lines = [l for l in r.text.strip().split("\n") if l.strip()]
        # 3 keeps originally, but we changed one to remove → 2 keeps
        check("CSV with relevance_filter=keep", len(csv_lines) >= 2, f"got {len(csv_lines)} lines")

        r = await client.get(f"{BASE}/projects/{project1_id}/export?format=xlsx", headers=auth)
        check("Export XLSX → 200", r.status_code == 200)
        check("XLSX content-type", "spreadsheet" in r.headers.get("content-type", ""))
        check("XLSX has content", len(r.content) > 100)

        # ── 17. Jobs (no Celery, so just test the API shape) ─────────────────
        print("\n⚙️ Jobs API")

        r = await client.get(f"{BASE}/projects/{project1_id}/jobs", headers=auth)
        check("List jobs → 200", r.status_code == 200)
        jobs_data = r.json()
        check("Jobs returns paginated response", "items" in jobs_data and "total" in jobs_data)
        check("Jobs total is 0", jobs_data["total"] == 0)

        # Job creation will fail because Celery isn't running, but we test the validation
        r = await client.post(f"{BASE}/projects/{project1_id}/jobs", headers=auth, json={
            "job_type": "invalid_type",
        })
        check("Invalid job type → 422", r.status_code == 422)

        # ── 18. Cross-user isolation ─────────────────────────────────────────
        print("\n🔒 Cross-User Isolation")

        # Register second user
        r = await client.post(f"{BASE}/auth/register", json={
            "email": "bob@example.com",
            "password": "BobPassword123!",
            "full_name": "Bob Attacker",
        })
        check("Register second user → 201", r.status_code == 201)

        r = await client.post(f"{BASE}/auth/login", json={
            "email": "bob@example.com",
            "password": "BobPassword123!",
        })
        bob_token = r.json()["access_token"]
        bob_auth = {"Authorization": f"Bearer {bob_token}"}

        # Bob can't see Alice's projects
        r = await client.get(f"{BASE}/projects", headers=bob_auth)
        check("Bob's project list is empty", r.json()["total"] == 0)

        # Bob can't access Alice's project
        r = await client.get(f"{BASE}/projects/{project1_id}", headers=bob_auth)
        check("Bob can't access Alice's project → 403", r.status_code == 403)

        # Bob can't access Alice's papers
        r = await client.get(f"{BASE}/projects/{project1_id}/papers", headers=bob_auth)
        check("Bob can't list Alice's papers → 403", r.status_code == 403)

        # Bob can't export Alice's data
        r = await client.get(f"{BASE}/projects/{project1_id}/export?format=csv", headers=bob_auth)
        check("Bob can't export Alice's data → 403", r.status_code == 403)

        # ── 19. Delete Project ───────────────────────────────────────────────
        print("\n🗑️ Delete Project")

        r = await client.delete(f"{BASE}/projects/{project3_id}", headers=auth)
        check("Delete project → 204", r.status_code == 204)

        r = await client.get(f"{BASE}/projects/{project3_id}", headers=auth)
        check("Deleted project → 404", r.status_code == 404)

        r = await client.get(f"{BASE}/projects", headers=auth)
        check("Remaining projects = 2", r.json()["total"] == 2)

        # ── 20. Middleware & Headers ─────────────────────────────────────────
        print("\n🔧 Middleware & Response Headers")

        r = await client.get("http://testserver/health")
        check("X-Request-ID header present", "x-request-id" in r.headers)
        check("X-Process-Time header present", "x-process-time" in r.headers)

    # Cleanup
    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)


# ══════════════════════════════════════════════════════════════════════════════
#  Main
# ══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    # Redirect output to a file as well as stdout
    import io
    from pathlib import Path
    
    output_file = Path(__file__).parent / "test_results.txt"
    
    class TeeWriter:
        def __init__(self, *streams):
            self.streams = streams
        def write(self, data):
            for s in self.streams:
                s.write(data)
                s.flush()
        def flush(self):
            for s in self.streams:
                s.flush()
    
    f = open(output_file, "w", encoding="utf-8")
    sys.stdout = TeeWriter(sys.__stdout__, f)
    sys.stderr = TeeWriter(sys.__stderr__, f)
    
    print("=" * 60)
    print("  SMS Pipeline — End-to-End Integration Test")
    print("  Simulating a real user's journey through the API")
    print("=" * 60)

    asyncio.run(run_all_tests())

    print("\n" + "=" * 60)
    if failed == 0:
        print(f"  ALL {passed}/{total_tests} TESTS PASSED")
    else:
        print(f"  {failed} FAILED  |  {passed} PASSED  |  Total: {total_tests}")
    print("=" * 60)

    f.close()
    sys.exit(1 if failed > 0 else 0)
