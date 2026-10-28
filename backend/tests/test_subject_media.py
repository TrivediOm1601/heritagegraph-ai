"""Repair R6 — subject media (photograph, thumbnail, license, credit).

The transport is a fixture (``httpx.MockTransport``): zero egress, same
seam as the rest of the provider tests. What these tests pin down is the
*honesty* contract of the media layer:

* every displayed field comes from the payload, never from a guess;
* a page with no page image yields no image (the UI shows a placeholder);
* a source outage yields ``[]`` rather than an unrelated picture;
* the file metadata is matched even when the file name differs only by
  underscores/case, and never attached to the wrong page.
"""

from __future__ import annotations

import httpx
import pytest

from app.integrations.wikimedia_media import (
    PageMedia,
    clear_cache,
    fetch_page_media,
    page_media_index,
)

pytestmark = pytest.mark.anyio


@pytest.fixture(autouse=True)
def _clear_media_cache():
    clear_cache()
    yield
    clear_cache()


# ---------------------------------------------------------------------------
# Fixture payloads (shapes taken from the live API, not invented)
# ---------------------------------------------------------------------------


def _media_payload(*pages: dict) -> dict:
    return {"query": {"pages": list(pages)}}


def _page(
    title: str,
    *,
    page_id: int = 1,
    description: str | None = "minaret in Delhi",
    thumbnail: str | None = "https://upload.wikimedia.org/thumb/qutb.jpg",
    original: str | None = "https://upload.wikimedia.org/qutb.jpg",
    file_name: str | None = "Qutb_Minar_2022.jpg",
    wikibase: str | None = "Q187635",
    missing: bool = False,
) -> dict:
    page: dict = {
        "pageid": page_id,
        "title": title,
        "canonicalurl": f"https://en.wikipedia.org/wiki/{title.replace(' ', '_')}",
    }
    if missing:
        page["missing"] = True
        return page
    if thumbnail:
        page["thumbnail"] = {"source": thumbnail}
    if original:
        page["original"] = {"source": original}
    if file_name:
        page["pageimage"] = file_name
    if description is not None:
        page["terms"] = {"description": [description]}
    if wikibase:
        page["pageprops"] = {"wikibase_item": wikibase}
    return page


def _fileinfo_payload(title: str, **fields) -> dict:
    defaults = {
        "descriptionurl": "https://commons.wikimedia.org/wiki/File:Qutb_Minar_2022.jpg",
        "LicenseShortName": "CC BY 4.0",
        "LicenseUrl": "https://creativecommons.org/licenses/by/4.0",
        "Artist": '<a href="https://commons.wikimedia.org/wiki/User:X">Wasir kasab</a>',
        "ImageDescription": "Qutb Minar seen from the ground",
    }
    defaults.update(fields)
    extmetadata = {
        key: {"value": value} for key, value in defaults.items() if key != "descriptionurl"
    }
    return {
        "query": {
            "pages": [
                {
                    "title": title,
                    "imageinfo": [
                        {
                            "descriptionurl": defaults["descriptionurl"],
                            "extmetadata": extmetadata,
                        }
                    ],
                }
            ]
        }
    }


def _client(media: dict, files: dict | None = None, *, status: int = 200) -> httpx.AsyncClient:
    """Transport answering the two API calls by shape of the request."""

    def handler(request: httpx.Request) -> httpx.Response:
        if status >= 400:
            return httpx.Response(status, json={"error": "boom"})
        titles = dict(httpx.QueryParams(request.url.query)).get("titles", "")
        if "commons.wikimedia.org" in str(request.url):
            return httpx.Response(200, json=_fileinfo_payload(titles))
        # The media call joins titles with "|"; a caller may key the fixture
        # by the whole joined string.
        key = titles
        if key not in media:
            return httpx.Response(200, json={"query": {"pages": [{"missing": True}]}})
        return httpx.Response(200, json=media[key])

    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------


async def test_image_and_attribution_come_from_the_payload():
    payload = _media_payload(_page("Qutb Minar"))
    client = _client({"Qutb Minar": payload})

    media = await fetch_page_media(["Qutb Minar"], client=client, environ={})

    assert len(media) == 1
    item = media[0]
    assert item.title == "Qutb Minar"
    assert item.page_id == "1"
    assert item.description == "minaret in Delhi"
    assert item.thumbnail_url == "https://upload.wikimedia.org/thumb/qutb.jpg"
    assert item.original_url == "https://upload.wikimedia.org/qutb.jpg"
    assert item.wikibase_item == "Q187635"
    # Attribution is what Commons says, with its HTML stripped — not a
    # summary of it.
    assert item.license_name == "CC BY 4.0"
    assert item.license_url == "https://creativecommons.org/licenses/by/4.0"
    assert item.credit == "Wasir kasab"
    assert item.image_description == "Qutb Minar seen from the ground"
    assert item.file_page_url.startswith("https://commons.wikimedia.org/wiki/File:")
    assert item.has_image is True


async def test_page_without_an_image_yields_no_image_urls():
    payload = _media_payload(_page("Modi ministry", thumbnail=None, original=None, file_name=None))
    client = _client({"Modi ministry": payload})

    media = await fetch_page_media(["Modi ministry"], client=client, environ={})

    assert len(media) == 1
    assert media[0].has_image is False
    assert media[0].thumbnail_url is None
    assert media[0].original_url is None


async def test_non_http_image_urls_are_ignored_not_displayed():
    payload = _media_payload(
        _page("Broken", thumbnail="//upload.wikimedia.org/x.jpg", original="//x.jpg")
    )
    client = _client({"Broken": payload})

    media = await fetch_page_media(["Broken"], client=client, environ={})

    assert media[0].thumbnail_url is None
    assert media[0].has_image is False


async def test_missing_pages_are_dropped():
    client = _client({"Gone": _media_payload(_page("Gone", missing=True))})

    assert await fetch_page_media(["Gone"], client=client, environ={}) == []


async def test_a_source_outage_yields_no_media_at_all():
    client = _client({}, status=503)

    assert await fetch_page_media(["Qutb Minar"], client=client, environ={}) == []


async def test_malformed_body_is_not_read_as_media():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="<html>not json</html>")

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))

    assert await fetch_page_media(["Qutb Minar"], client=client, environ={}) == []


# ---------------------------------------------------------------------------
# File metadata matching
# ---------------------------------------------------------------------------


async def test_file_metadata_matches_underscored_names():
    payload = _media_payload(_page("Qutb Minar", file_name="Qutb_Minar_2022.jpg"))

    def handler(request: httpx.Request) -> httpx.Response:
        if "commons.wikimedia.org" in str(request.url):
            # The API normalises the underscore away in the echo.
            return httpx.Response(200, json=_fileinfo_payload("File:Qutb Minar 2022.jpg"))
        return httpx.Response(200, json=payload)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))

    media = await fetch_page_media(["Qutb Minar"], client=client, environ={})

    assert media[0].license_name == "CC BY 4.0"
    assert media[0].credit == "Wasir kasab"


async def test_file_metadata_is_never_attached_to_a_different_file():
    payload = _media_payload(_page("Qutb Minar", file_name="Qutb_Minar_2022.jpg"))

    def handler(request: httpx.Request) -> httpx.Response:
        if "commons.wikimedia.org" in str(request.url):
            return httpx.Response(200, json=_fileinfo_payload("File:Something_Else.jpg"))
        return httpx.Response(200, json=payload)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))

    media = await fetch_page_media(["Qutb Minar"], client=client, environ={})

    # The image is still real; it simply carries no license/credit rather
    # than another file's attribution.
    assert media[0].thumbnail_url is not None
    assert media[0].license_name is None
    assert media[0].credit is None
    assert media[0].file_page_url is None


async def test_blank_titles_do_not_reach_the_source():
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        return httpx.Response(200, json=_media_payload())

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))

    assert await fetch_page_media(["", "   "], client=client, environ={}) == []
    assert calls == []


async def test_responses_are_cached_so_one_search_does_not_refetch():
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        return httpx.Response(200, json=_media_payload(_page("Qutb Minar")))

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))

    first = await fetch_page_media(["Qutb Minar"], client=client, environ={})
    second = await fetch_page_media(["Qutb Minar"], client=client, environ={})

    assert [item.title for item in first] == [item.title for item in second]
    # media + file metadata, once.
    assert len(calls) == 2


def test_page_media_index_keys_on_the_title_case_insensitively():
    media = [PageMedia(title="Qutb Minar", page_id="1")]
    index = page_media_index(media)

    assert index["qutb minar"].page_id == "1"
