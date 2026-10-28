"""Download the multi-image evaluation set from Wikimedia (with provenance).

Creates ``backend/testdata/eval/`` containing one image per test case plus
``manifest.json`` recording, for every image: test id, expected subject,
the Wikipedia article page, the Wikimedia Commons file page, the direct
image URL, the license (from Commons ``extmetadata``), byte size, SHA-256
and whether the download succeeded.

Images are the *lead image* of the corresponding Wikipedia article, which
is a stable, reputable, citable choice for each expected subject. Nothing
here is invented: every URL is returned by the MediaWiki API at run time.

Usage::

    cd backend
    .venv/Scripts/python -m scripts.fetch_eval_images

Re-runnable and idempotent: existing files are kept unless ``--force``.
Assets stay local (testdata/ is not committed) — see README §P.
"""

from __future__ import annotations

import hashlib
import json
import pathlib
import urllib.parse
import urllib.request

BACKEND_DIR = pathlib.Path(__file__).resolve().parents[1]
OUT_DIR = BACKEND_DIR / "testdata" / "eval"
MANIFEST = OUT_DIR / "manifest.json"

USER_AGENT = "HeritageGraphAI-eval/1.0 (historical image pipeline test)"

#: (test id, expected subject, English Wikipedia article title)
CASES = [
    ("T1", "Rani ki Vav (stepwell, Gujarat)", "Rani ki Vav"),
    ("T2", "Qutb Minar (minaret, Delhi)", "Qutb Minar"),
    ("T3", "Konark Sun Temple (temple, Odisha)", "Konark Sun Temple"),
    ("T4", "Taj Mahal (mausoleum, Agra)", "Taj Mahal"),
    ("T5", "Historical document with readable text (OCR)", "Gutenberg Bible"),
    ("T6", "Artifact: Chola bronze Nataraja", "Nataraja"),
    ("T7", "Ambiguous: plain masonry, not a named monument", "Brick"),
    ("T8", "Non-historical modern object", "Laptop"),
]


def _get_json(url: str) -> dict:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.loads(response.read().decode("utf-8"))


def _lead_image(title: str) -> dict:
    """Lead image of an article: direct URL + the File: it points at."""
    query = urllib.parse.urlencode(
        {
            "action": "query",
            "prop": "pageimages",
            "piprop": "original",
            "redirects": "1",
            "titles": title,
            "format": "json",
        }
    )
    data = _get_json(f"https://en.wikipedia.org/w/api.php?{query}")
    page = next(iter(data["query"]["pages"].values()))
    original = page.get("original", {}).get("source")
    if not original:
        raise RuntimeError(f"no lead image for {title!r}")
    filename = urllib.parse.unquote(original.split("?")[0].rsplit("/", 1)[-1])
    return {"url": original, "file": filename}


def _commons_metadata(filename: str) -> dict:
    """License + description page for a Commons file (may be missing)."""
    query = urllib.parse.urlencode(
        {
            "action": "query",
            "titles": f"File:{filename}",
            "prop": "imageinfo",
            "iiprop": "url|extmetadata",
            "format": "json",
        }
    )
    try:
        data = _get_json(f"https://commons.wikimedia.org/w/api.php?{query}")
    except Exception:  # noqa: BLE001 — metadata is best-effort
        return {}
    page = next(iter(data["query"]["pages"].values()))
    info = (page.get("imageinfo") or [{}])[0]
    meta = info.get("extmetadata") or {}
    return {
        "commons_page": info.get("descriptionurl", ""),
        "license": (meta.get("LicenseShortName", {}) or {}).get("value", "unknown"),
        "artist": (meta.get("Artist", {}) or {}).get("value", "unknown"),
        "credit": (meta.get("Credit", {}) or {}).get("value", "unknown"),
    }


def _download(url: str, destination: pathlib.Path) -> tuple[int, str]:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=120) as response:
        blob = response.read()
    destination.write_bytes(blob)
    return len(blob), hashlib.sha256(blob).hexdigest()


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    existing: dict = {}
    if MANIFEST.is_file():
        existing = json.loads(MANIFEST.read_text(encoding="utf-8"))
        existing = {case["id"]: case for case in existing.get("cases", [])}

    manifest: dict = {"cases": [], "note": "Lead images of the named Wikipedia "
                      "articles; license/credit from Wikimedia Commons extmetadata."}
    ok = 0
    for test_id, expected, title in CASES:
        record = existing.get(test_id) or {}
        local_name = record.get("local_file")
        if local_name and (OUT_DIR / local_name).is_file():
            print(f"{test_id}: already downloaded ({local_name})")
            manifest["cases"].append(record)
            ok += 1
            continue
        case = {
            "id": test_id,
            "expected": expected,
            "article": title,
            "article_url": f"https://en.wikipedia.org/wiki/{urllib.parse.quote(title.replace(' ', '_'))}",
            "downloaded": False,
        }
        try:
            lead = _lead_image(title)
            case["direct_image_url"] = lead["url"]
            case.update(_commons_metadata(lead["file"]))
            suffix = pathlib.Path(lead["file"]).suffix.lower() or ".jpg"
            local_name = f"{test_id.lower()}{suffix}"
            size, digest = _download(lead["url"], OUT_DIR / local_name)
            case.update(
                downloaded=True,
                local_file=local_name,
                bytes=size,
                sha256=digest,
            )
            ok += 1
            print(
                f"{test_id}: {expected} -> {local_name} ({size // 1024} KB, "
                f"license: {case.get('license', '?')})"
            )
        except Exception as exc:  # noqa: BLE001 — record and continue
            case["error"] = f"{type(exc).__name__}: {exc}"
            print(f"{test_id}: FAILED — {case['error']}")
        manifest["cases"].append(case)

    MANIFEST.write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\n{ok}/{len(CASES)} images available; manifest -> {MANIFEST}")
    return 0 if ok == len(CASES) else 1


if __name__ == "__main__":
    raise SystemExit(main())