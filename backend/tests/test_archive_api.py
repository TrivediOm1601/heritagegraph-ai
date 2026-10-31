"""End-to-end API tests for the Heritage Archive Studio, research workspace
and heritage map.

The TestClient runs FastAPI background tasks before returning, so an upload
is fully processed by the time the next request is made. OCR is not configured
in these tests (no network/credentials), which is itself asserted: documents
with a text layer complete, and an image file is reported ``partial`` with an
explicit "OCR skipped" warning rather than a false success.
"""

from __future__ import annotations

import asyncio
import io
import zipfile

import pytest
from reportlab.pdfgen import canvas


def make_pdf(lines: list[str]) -> bytes:
    buffer = io.BytesIO()
    document = canvas.Canvas(buffer)
    for line in lines:
        document.drawString(72, 760, line)
        document.showPage()
    document.save()
    return buffer.getvalue()


def make_zip(entries: dict[str, bytes]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, payload in entries.items():
            archive.writestr(name, payload)
    return buffer.getvalue()


@pytest.fixture(autouse=True)
def archive_storage(tmp_path, monkeypatch):
    """Every test stores uploads in its own temp directory."""
    from app.services.storage_service import reset_storage_root

    monkeypatch.setenv("HG_STORAGE_DIR", str(tmp_path / "storage"))
    reset_storage_root()
    yield
    reset_storage_root()


def create_archive(client, name: str = "Gujarat Stepwell Heritage Collection") -> dict:
    response = client.post(
        "/api/archives",
        json={
            "name": name,
            "description": "Historical documentation of stepwells and regional architecture.",
            "region": "Gujarat",
            "tags": ["stepwell", "architecture"],
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def upload(client, archive_id: str, files: list[tuple[str, bytes, str]]):
    return client.post(
        f"/api/archives/{archive_id}/files",
        files=[("files", (name, payload, content_type)) for name, payload, content_type in files],
    )


# ---------------------------------------------------------------------------
# Archive CRUD and ownership
# ---------------------------------------------------------------------------


def test_create_list_update_and_delete_archive(auth_client):
    archive = create_archive(auth_client)
    assert archive["name"] == "Gujarat Stepwell Heritage Collection"
    assert archive["stats"]["files"] == 0
    assert archive["files"] == []

    listing = auth_client.get("/api/archives").json()
    assert listing["total"] == 1

    renamed = auth_client.patch(
        f"/api/archives/{archive['id']}", json={"name": "Patan Stepwells"}
    )
    assert renamed.status_code == 200
    assert renamed.json()["name"] == "Patan Stepwells"

    assert auth_client.delete(f"/api/archives/{archive['id']}").status_code == 204
    assert auth_client.get(f"/api/archives/{archive['id']}").status_code == 404


def test_another_account_cannot_read_upload_or_search(auth_client, other_client):
    archive = create_archive(auth_client)

    assert other_client.get(f"/api/archives/{archive['id']}").status_code == 404
    assert (
        other_client.get(f"/api/archives/{archive['id']}/search", params={"q": "vav"}).status_code
        == 404
    )
    response = upload(
        other_client, archive["id"], [("other.txt", b"not mine", "text/plain")]
    )
    assert response.status_code == 404


def test_another_account_cannot_touch_an_archived_file(auth_client, other_client):
    """A file id must not be a capability: download, reprocess and delete all
    answer 404 for an account that does not own the row."""
    archive = create_archive(auth_client)
    response = upload(
        auth_client,
        archive["id"],
        [("collection.pdf", make_pdf(["Rani ki Vav at Patan was built in 1063 CE."]), "application/pdf")],
    )
    assert response.status_code == 202, response.text
    file_id = response.json()["accepted"][0]["id"]

    assert auth_client.get(f"/api/archive-files/{file_id}/download").status_code == 200
    assert other_client.get(f"/api/archive-files/{file_id}").status_code == 404
    assert other_client.get(f"/api/archive-files/{file_id}/download").status_code == 404
    assert other_client.post(f"/api/archive-files/{file_id}/reprocess").status_code == 404
    assert other_client.delete(f"/api/archive-files/{file_id}").status_code == 404

    # The owner's file is untouched by the failed attempts.
    detail = auth_client.get(f"/api/archive-files/{file_id}").json()
    assert detail["file"]["status"] == "completed"
    assert detail["pages"][0]["text"].startswith("Rani ki Vav")


# ---------------------------------------------------------------------------
# Digitisation
# ---------------------------------------------------------------------------


def test_pdf_is_extracted_indexed_and_searchable(auth_client):
    archive = create_archive(auth_client)
    pdf = make_pdf(
        [
            "Rani ki Vav is a stepwell in Patan.",
            "The stepwell was built in 1485 CE and later silted over.",
        ]
    )
    response = upload(auth_client, archive["id"], [("rani-ki-vav.pdf", pdf, "application/pdf")])
    assert response.status_code == 202, response.text
    assert len(response.json()["accepted"]) == 1

    detail = auth_client.get(f"/api/archives/{archive['id']}").json()
    file_item = detail["files"][0]
    assert file_item["status"] == "completed", file_item
    assert file_item["pageCount"] == 2
    assert file_item["pageLabel"] == "Page"
    assert detail["stats"]["pages"] == 2
    assert detail["stats"]["characters"] > 0

    file_detail = auth_client.get(f"/api/archive-files/{file_item['id']}").json()
    assert "Rani ki Vav" in file_detail["pages"][0]["text"]
    assert file_detail["pages"][0]["pageNumber"] == 1
    assert any(entity["name"] == "Rani ki Vav" for entity in file_detail["entities"])
    assert file_detail["file"]["filename"] == "rani-ki-vav.pdf"

    search = auth_client.get("/api/archive-search", params={"q": "stepwell"}).json()
    assert search["mode"] == "keyword"
    assert search["total"] >= 1
    assert search["results"][0]["pageNumber"] in (1, 2)
    assert search["results"][0]["archiveName"] == archive["name"]
    assert "stepwell" in search["results"][0]["excerpt"].lower()

    timeline = auth_client.get(
        f"/api/archives/{archive['id']}/timeline"
    ).json()
    dated = [event for event in timeline["events"] if event["dateText"].startswith("1485")]
    assert dated, timeline
    assert dated[0]["precision"] == "exact"
    assert dated[0]["sourcePage"] == 2
    assert dated[0]["verificationStatus"] == "unverified"


def test_docx_and_pptx_are_digitised_independently(auth_client):
    archive = create_archive(auth_client)
    docx = io.BytesIO()
    with zipfile.ZipFile(docx, "w") as bundle:
        bundle.writestr(
            "word/document.xml",
            '<?xml version="1.0"?><w:document xmlns:w='
            '"http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body>'
            '<w:p><w:pPr><w:pStyle w:val="Heading1"/></w:pPr><w:r><w:t>Stepwell notes</w:t>'
            '</w:r></w:p><w:p><w:r><w:t>Adalaj was built in 1499 CE.</w:t></w:r></w:p>'
            "</w:body></w:document>",
        )
    pptx = io.BytesIO()
    with zipfile.ZipFile(pptx, "w") as bundle:
        bundle.writestr(
            "ppt/slides/slide1.xml",
            '<?xml version="1.0"?><p:sld xmlns:a='
            '"http://schemas.openxmlformats.org/drawingml/2006/main" xmlns:p='
            '"http://schemas.openxmlformats.org/presentationml/2006/main"><p:cSld>'
            '<p:spTree><p:sp><p:txBody><a:p><a:r><a:t>Sun Temple</a:t></a:r></a:p>'
            "</p:txBody></p:sp></p:spTree></p:cSld></p:sld>",
        )

    response = upload(
        auth_client,
        archive["id"],
        [
            ("notes.docx", docx.getvalue(), "application/octet-stream"),
            ("deck.pptx", pptx.getvalue(), "application/octet-stream"),
        ],
    )
    assert response.status_code == 202, response.text

    detail = auth_client.get(f"/api/archives/{archive['id']}").json()
    statuses = {item["filename"]: item["status"] for item in detail["files"]}
    assert statuses == {"notes.docx": "completed", "deck.pptx": "completed"}
    docx_id = next(item["id"] for item in detail["files"] if item["filename"] == "notes.docx")
    pptx_id = next(item["id"] for item in detail["files"] if item["filename"] == "deck.pptx")

    docx_detail = auth_client.get(f"/api/archive-files/{docx_id}").json()
    assert docx_detail["file"]["pageLabel"] == "Section"
    assert "Adalaj" in docx_detail["pages"][0]["text"]

    pptx_detail = auth_client.get(f"/api/archive-files/{pptx_id}").json()
    assert pptx_detail["file"]["pageLabel"] == "Slide"
    assert "Sun Temple" in pptx_detail["pages"][0]["text"]


def test_zip_expands_members_and_preserves_provenance(auth_client):
    archive = create_archive(auth_client)
    bundle = make_zip(
        {
            "field-notes/patan.txt": b"Rani ki Vav stands in Patan.",
            "reports/stepwell.pdf": make_pdf(["Adalaj stepwell, 1499 CE."]),
        }
    )
    response = upload(auth_client, archive["id"], [("collection.zip", bundle, "application/zip")])
    assert response.status_code == 202, response.text
    assert len(response.json()["accepted"]) == 3  # the ZIP + two members

    detail = auth_client.get(f"/api/archives/{archive['id']}").json()
    by_name = {item["filename"]: item for item in detail["files"]}
    assert by_name["collection.zip"]["kind"] == "zip"
    assert by_name["collection.zip"]["status"] == "completed"
    assert by_name["patan.txt"]["parentFileId"] == by_name["collection.zip"]["id"]
    assert by_name["patan.txt"]["archivePath"] == "field-notes/patan.txt"
    assert by_name["patan.txt"]["status"] == "completed"

    search = auth_client.get(
        f"/api/archives/{archive['id']}/search", params={"q": "Patan"}
    ).json()
    assert search["results"][0]["filename"] == "patan.txt"


def test_unsafe_zip_is_rejected_with_a_reason(auth_client):
    archive = create_archive(auth_client)
    response = upload(
        auth_client,
        archive["id"],
        [("evil.zip", make_zip({"../escape.txt": b"nope"}), "application/zip")],
    )
    assert response.status_code == 415
    assert "path traversal" in response.json()["error"]["message"].lower()


def test_one_failed_file_does_not_stop_the_others(auth_client):
    archive = create_archive(auth_client)
    response = upload(
        auth_client,
        archive["id"],
        [
            ("broken.pdf", b"%PDF-1.4 this is not a real pdf", "application/pdf"),
            ("good.txt", b"Champaner-Pavagadh is an archaeological park.", "text/plain"),
        ],
    )
    assert response.status_code == 202, response.text

    detail = auth_client.get(f"/api/archives/{archive['id']}").json()
    statuses = {item["filename"]: item["status"] for item in detail["files"]}
    assert statuses["broken.pdf"] == "failed"
    assert statuses["good.txt"] == "completed"
    broken = next(item for item in detail["files"] if item["filename"] == "broken.pdf")
    assert broken["error"]

    reprocessed = auth_client.post(f"/api/archive-files/{broken['id']}/reprocess")
    assert reprocessed.status_code == 202
    assert auth_client.get(f"/api/archive-files/{broken['id']}").json()["file"]["status"] == "failed"


def test_image_without_ocr_configuration_is_partial_not_completed(auth_client):
    from PIL import Image

    buffer = io.BytesIO()
    Image.new("RGB", (40, 24), "white").save(buffer, format="PNG")
    archive = create_archive(auth_client)
    response = upload(
        auth_client, archive["id"], [("photo.png", buffer.getvalue(), "image/png")]
    )
    assert response.status_code == 202, response.text

    detail = auth_client.get(f"/api/archives/{archive['id']}").json()
    file_item = detail["files"][0]
    assert file_item["status"] == "partial", file_item
    assert any("OCR skipped" in warning for warning in file_item["warnings"])
    file_detail = auth_client.get(f"/api/archive-files/{file_item['id']}").json()
    assert file_detail["pages"][0]["text"] == ""
    assert any("does not identify the monument" in note for note in file_detail["limitations"])


def test_same_file_twice_is_flagged_as_an_exact_duplicate(auth_client):
    archive = create_archive(auth_client)
    payload = b"Modhera Sun Temple, built in the 11th century."
    first = upload(auth_client, archive["id"], [("modhera.txt", payload, "text/plain")])
    assert first.status_code == 202
    second = upload(auth_client, archive["id"], [("modhera-copy.txt", payload, "text/plain")])
    assert second.status_code == 202

    detail = auth_client.get(f"/api/archives/{archive['id']}").json()
    rows = {item["filename"]: item for item in detail["files"]}
    assert rows["modhera.txt"]["duplicateOfId"] is None
    assert rows["modhera-copy.txt"]["duplicateOfId"] == rows["modhera.txt"]["id"]
    assert rows["modhera-copy.txt"]["duplicateKind"] == "exact"
    assert len(detail["files"]) == 2  # flagged, never deleted


def test_archive_report_is_a_real_pdf(auth_client):
    archive = create_archive(auth_client)
    upload(auth_client, archive["id"], [("patan.txt", b"Rani ki Vav was built in 1085 CE.", "text/plain")])
    response = auth_client.get(f"/api/archives/{archive['id']}/report.pdf")

    assert response.status_code == 200
    assert response.headers["content-type"] == "application/pdf"
    assert response.content.startswith(b"%PDF-")
    assert "attachment" in response.headers["content-disposition"]


def test_uploaded_original_can_be_downloaded_by_its_owner(auth_client):
    archive = create_archive(auth_client)
    payload = b"Adalaj stepwell field notes."
    upload(auth_client, archive["id"], [("notes.txt", payload, "text/plain")])
    detail = auth_client.get(f"/api/archives/{archive['id']}").json()
    file_id = detail["files"][0]["id"]

    response = auth_client.get(f"/api/archive-files/{file_id}/download")
    assert response.status_code == 200
    assert response.content == payload
    assert "attachment" in response.headers["content-disposition"]


# ---------------------------------------------------------------------------
# Research workspace
# ---------------------------------------------------------------------------


def test_research_collection_notes_and_report(auth_client, other_client):
    archive = create_archive(auth_client)
    upload(auth_client, archive["id"], [("patan.txt", b"Rani ki Vav, Patan, 11th century.", "text/plain")])

    created = auth_client.post(
        "/api/collections",
        json={"name": "Gujarat Stepwell Research", "description": "Working notes."},
    )
    assert created.status_code == 201, created.text
    collection_id = created.json()["id"]

    added = auth_client.post(
        f"/api/collections/{collection_id}/items",
        json={"type": "archive", "id": archive["id"]},
    )
    assert added.status_code == 200, added.text
    assert added.json()["itemCount"] == 1
    assert added.json()["items"][0]["name"] == archive["name"]

    note = auth_client.post(
        f"/api/collections/{collection_id}/notes",
        json={"body": "Compare the stepped corridors at Rani ki Vav and Adalaj.", "subjectQuery": "stepwells"},
    )
    assert note.status_code == 201
    assert note.json()["subjectQuery"] == "stepwells"

    detail = auth_client.get(f"/api/collections/{collection_id}").json()
    assert detail["noteCount"] == 1
    assert detail["notes"][0]["body"].startswith("Compare")

    report = auth_client.get(f"/api/collections/{collection_id}/report.pdf")
    assert report.status_code == 200
    assert report.content.startswith(b"%PDF-")

    forbidden = other_client.post(
        f"/api/collections/{collection_id}/items",
        json={"type": "archive", "id": archive["id"]},
    )
    assert forbidden.status_code == 404
    assert other_client.get(f"/api/collections/{collection_id}").status_code == 404


# ---------------------------------------------------------------------------
# Heritage map + approximate route
# ---------------------------------------------------------------------------


@pytest.fixture
def seeded_map(auth_url):
    """Seed the curated heritage sites into this test's database."""
    from app.db.session import session_scope
    from app.services.heritage_service import seed_heritage_sites

    async def _seed() -> int:
        async with session_scope() as session:
            return await seed_heritage_sites(session)

    return asyncio.run(_seed())


def test_heritage_sites_expose_sourced_coordinates(auth_client, seeded_map):
    response = auth_client.get("/api/heritage/sites")
    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 67
    assert body["dataset"] == "curated-demo-india"
    by_id = {site["id"]: site for site in body["sites"]}
    assert by_id["rani-ki-vav"]["coordinateAccuracy"] == "approximate"
    assert by_id["rani-ki-vav"]["sourceUrl"].startswith("https://en.wikipedia.org/")
    assert by_id["dholavira"]["latitude"] is not None
    assert by_id["rani-ki-vav"]["state"] == "Gujarat"
    assert by_id["rani-ki-vav"]["region"] == "West India"
    assert by_id["taj-mahal"]["unescoStatus"] is True

    filtered = auth_client.get("/api/heritage/sites", params={"category": "Stepwells"})
    assert {site["id"] for site in filtered.json()["sites"]} == {
        "rani-ki-vav",
        "adalaj-stepwell",
    }

    facets = body["facets"]
    assert len(facets["states"]) == 36
    assert {item["name"] for item in facets["states"] if item["isUnionTerritory"]} == {
        "Andaman and Nicobar Islands",
        "Chandigarh",
        "Dadra and Nagar Haveli and Daman and Diu",
        "Delhi",
        "Jammu and Kashmir",
        "Ladakh",
        "Lakshadweep",
        "Puducherry",
    }
    assert body["summary"]["totalSites"] == 67
    assert body["summary"]["statesRepresented"] == 36
    assert body["summary"]["verifiedRecords"] == 0


def test_national_filters_combine_and_paginate(auth_client, seeded_map):
    stepwells = auth_client.get("/api/heritage/sites", params={"category": "Stepwells"})
    assert stepwells.json()["total"] == 2

    gujarat = auth_client.get(
        "/api/heritage/sites", params={"state": "Gujarat", "limit": 2}
    ).json()
    assert gujarat["total"] == 5
    assert len(gujarat["sites"]) == 2

    south = auth_client.get("/api/heritage/sites", params={"region": "South India"})
    assert south.json()["total"] > 10
    assert all(site["region"] == "South India" for site in south.json()["sites"])

    medieval = auth_client.get(
        "/api/heritage/sites", params={"periodGroup": "Medieval"}
    ).json()
    assert medieval["sites"]
    assert all(site["periodGroup"] == "Medieval" for site in medieval["sites"])

    unesco = auth_client.get("/api/heritage/sites", params={"unesco": True}).json()
    assert unesco["total"] > 20
    assert all(site["unescoStatus"] is True for site in unesco["sites"])

    combined = auth_client.get(
        "/api/heritage/sites",
        params={
            "state": "Rajasthan",
            "category": "Forts and Palaces",
            "unesco": True,
        },
    ).json()
    assert {site["id"] for site in combined["sites"]} == {
        "jaisalmer-fort",
        "amer-fort",
        "kumbhalgarh",
    }

    search = auth_client.get("/api/heritage/sites", params={"q": "taj"}).json()
    assert [site["id"] for site in search["sites"]] == ["taj-mahal"]
    alias = auth_client.get("/api/heritage/sites", params={"q": "Harmandir"}).json()
    assert "golden-temple" in {site["id"] for site in alias["sites"]}


def test_route_is_ordered_by_distance_and_labelled_approximate(auth_client, seeded_map):
    response = auth_client.post(
        "/api/heritage/route",
        json={
            "siteIds": ["rani-ki-vav", "adalaj-stepwell", "modhera-sun-temple"],
            "startSiteId": "rani-ki-vav",
        },
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert [stop["id"] for stop in body["stops"]][0] == "rani-ki-vav"
    assert len(body["legs"]) == 2
    assert body["totalDistanceKm"] > 0
    assert body["distanceMethod"] == "great-circle"
    assert body["ordering"] == "distance"
    assert "not a road route" in body["notice"]



def test_route_can_preserve_selection_order_across_regions(auth_client, seeded_map):
    ids = ["taj-mahal", "hampi", "konark-sun-temple", "rani-ki-vav"]
    response = auth_client.post(
        "/api/heritage/route",
        json={"siteIds": ids, "order": "selection"},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert [stop["id"] for stop in body["stops"]] == ids
    assert body["ordering"] == "selection"
    assert len(body["legs"]) == 3
    # Cross-country legs are long; the total must reflect them, not a local hop.
    assert body["totalDistanceKm"] > 1000
