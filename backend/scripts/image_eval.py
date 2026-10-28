"""Run every evaluation image through the application's real analysis API.

This does **not** call the vision model directly: it submits each image to
``POST /api/analysis`` exactly like the frontend does (multipart field
``file``), polls ``GET /api/analysis/{id}`` to a terminal state, and
captures what the application actually produced — status, warnings, the
vision interpretation, extracted text, entities, the historical briefing
with its cited sources, and matches.

Outputs ``testdata/eval/report.json`` plus a markdown table on stdout.

Usage (backend running first — ideally via ``python -m scripts.run_server``)::

    .venv/Scripts/python -m scripts.image_eval                 # all cases
    .venv/Scripts/python -m scripts.image_eval --only T1,T4    # subset
    .venv/Scripts/python -m scripts.image_eval --base-url http://127.0.0.1:8125

Images above the API's 10 MiB limit are downscaled to a *separate* upload
copy (the original download and its hash stay in the manifest); the report
records that this happened.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import pathlib
import time
import urllib.error
import urllib.request

BACKEND_DIR = pathlib.Path(__file__).resolve().parents[1]
EVAL_DIR = BACKEND_DIR / "testdata" / "eval"
MANIFEST = EVAL_DIR / "manifest.json"
REPORT = EVAL_DIR / "report.json"

MAX_UPLOAD_BYTES = 10 * 1024 * 1024  # matches the API's limit


def _multipart(path: pathlib.Path) -> tuple[bytes, str, str]:
    """Bytes actually submitted, plus filename/content type."""
    blob = path.read_bytes()
    note = ""
    if len(blob) > MAX_UPLOAD_BYTES - 512 * 1024:
        from PIL import Image

        image = Image.open(io.BytesIO(blob)).convert("RGB")
        image.thumbnail((4000, 4000), Image.LANCZOS)
        buffer = io.BytesIO()
        image.save(buffer, format="JPEG", quality=90)
        blob = buffer.getvalue()
        note = f"resized for upload ({image.size[0]}x{image.size[1]})"
    return blob, path.name, "image/jpeg" if path.suffix.lower() in {".jpg", ".jpeg"} else "image/png"


def _request(url: str, data: bytes | None = None, headers: dict | None = None, timeout: int = 60):
    request = urllib.request.Request(url, data=data, headers=headers or {})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.status, response.read()


def _post_analysis(base: str, blob: bytes, filename: str, content_type: str) -> str:
    boundary = "----heritageeval7f3a"
    body = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="file"; filename="{filename}"\r\n'
        f"Content-Type: {content_type}\r\n\r\n"
    ).encode() + blob + f"\r\n--{boundary}--\r\n".encode()
    _, payload = _request(
        f"{base}/api/analysis",
        data=body,
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
        timeout=120,
    )
    return json.loads(payload)["analysisId"]


def _poll(base: str, analysis_id: str, timeout: float) -> dict:
    deadline = time.time() + timeout
    while time.time() < deadline:
        _, payload = _request(f"{base}/api/analysis/{analysis_id}", timeout=30)
        data = json.loads(payload)
        if data.get("status") in {"completed", "failed"}:
            return data
        time.sleep(2)
    return {"status": "timeout"}


def _briefing_view(briefing) -> dict | None:
    if not briefing:
        return None
    return {
        "query": briefing.get("query"),
        "method": briefing.get("method"),
        "summary": (briefing.get("summary") or "")[:400],
        "key_fact_count": len(briefing.get("keyFacts") or []),
        "timeline_count": len(briefing.get("timeline") or []),
        "sources": [
            {"index": s.get("index"), "title": s.get("title"), "url": s.get("url")}
            for s in (briefing.get("sources") or [])[:6]
        ],
        "uncertainty": (briefing.get("uncertainty") or [])[:3],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Multi-image real-pipeline evaluation.")
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--only", default="", help="comma separated test ids, e.g. T1,T4")
    parser.add_argument("--case-timeout", type=float, default=240.0)
    parser.add_argument(
        "--pause",
        type=float,
        default=0.0,
        help="seconds to wait between cases (free-tier rate limits)",
    )
    args = parser.parse_args()

    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    wanted = {t.strip().upper() for t in args.only.split(",") if t.strip()}
    # Merge with earlier runs so the report always describes the whole
    # set — re-running a subset must not drop the other cases.
    results: dict[str, dict] = {}
    if REPORT.is_file():
        try:
            old = json.loads(REPORT.read_text(encoding="utf-8"))
            results = {case["id"]: case for case in old.get("cases", [])}
        except (OSError, ValueError):
            results = {}
    report: dict = {"base_url": args.base_url, "cases": []}

    for case in manifest["cases"]:
        if wanted and case["id"].upper() not in wanted:
            continue
        if not case.get("downloaded"):
            results[case["id"]] = {**case, "ran": False, "reason": "image unavailable"}
            report["cases"] = [
                results[c["id"]] for c in manifest["cases"] if c["id"] in results
            ]
            print(f"{case['id']}: SKIPPED (image unavailable)")
            continue

        path = EVAL_DIR / case["local_file"]
        blob, filename, content_type = _multipart(path)
        uploaded_sha = hashlib.sha256(blob).hexdigest()
        entry: dict = {
            "id": case["id"],
            "expected": case["expected"],
            "article_url": case.get("article_url"),
            "direct_image_url": case.get("direct_image_url"),
            "commons_page": case.get("commons_page"),
            "license": case.get("license"),
            "original_sha256": case.get("sha256"),
            "uploaded_sha256": uploaded_sha,
            "uploaded_bytes": len(blob),
        }
        started = time.time()
        try:
            analysis_id = _post_analysis(args.base_url, blob, filename, content_type)
            entry["analysis_id"] = analysis_id
            result = _poll(args.base_url, analysis_id, args.case_timeout)
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace")[:300]
            entry.update(ran=True, status=f"http_{exc.code}", error_body=detail)
            results[case["id"]] = entry
            report["cases"] = [
                results[c["id"]] for c in manifest["cases"] if c["id"] in results
            ]
            print(f"{case['id']}: HTTP {exc.code} — {detail}")
            continue
        except Exception as exc:  # noqa: BLE001
            entry.update(ran=True, status="client_error", error=f"{type(exc).__name__}: {exc}")
            results[case["id"]] = entry
            report["cases"] = [
                results[c["id"]] for c in manifest["cases"] if c["id"] in results
            ]
            print(f"{case['id']}: FAILED — {entry['error']}")
            continue

        entry.update(
            ran=True,
            elapsed_s=round(time.time() - started, 1),
            status=result.get("status"),
            error=result.get("error"),
            warnings=result.get("warnings") or [],
            interpretation=(result.get("interpretation") or "")[:700],
            extracted_text=(result.get("extractedText") or "")[:400],
            entities=[
                {"name": e["name"], "type": e["type"], "confidence": e.get("confidence")}
                for e in (result.get("entities") or [])
            ],
            matches=[
                {"title": m["title"], "score": m.get("score"), "url": m.get("url")}
                for m in (result.get("matches") or [])[:6]
            ],
            briefing=_briefing_view(result.get("briefing")),
            demo_fixture_used=any("DEMO MODE" in w for w in (result.get("warnings") or [])),
        )
        results[case["id"]] = entry
        report["cases"] = [
            results[c["id"]] for c in manifest["cases"] if c["id"] in results
        ]
        REPORT.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
        names = ", ".join(e["name"] for e in entry["entities"]) or "(none)"
        print(
            f"{case['id']}: {entry['status']} in {entry['elapsed_s']}s | "
            f"entities: {names} | briefing: "
            f"{(entry.get('briefing') or {}).get('query') or '-'} | "
            f"sources: {len((entry.get('briefing') or {}).get('sources') or [])} | "
            f"fixture: {entry['demo_fixture_used']}"
        )
        if args.pause:
            time.sleep(args.pause)

    REPORT.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\nreport -> {REPORT}")

    print("\n| Test | Expected | Actual vision result | Sources found | Fixture? | Status |")
    print("|---|---|---|---|---|---|")
    for entry in report["cases"]:
        if not entry.get("ran"):
            continue
        interpretation = (entry.get("interpretation") or entry.get("error") or "")[:80].replace("|", "/")
        briefing = entry.get("briefing") or {}
        print(
            f"| {entry['id']} | {entry['expected'][:40]} | {interpretation}… | "
            f"{len(briefing.get('sources') or [])} ({briefing.get('query') or '-'}) | "
            f"{'YES' if entry.get('demo_fixture_used') else 'no'} | {entry.get('status')} |"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())