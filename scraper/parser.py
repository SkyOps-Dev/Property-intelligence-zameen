"""PARSER: turns a Zameen search page (HTML) into clean Python dicts.

Zameen puts all page data as JSON in `window.state`. We read that JSON instead of
the visible HTML, so values are exact and survive design changes.

Everything is kept except pictures and videos: photos, their counts, the cover video and the
agency logo are removed, also from the stored raw record.
"""
import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone

STATE_RE = re.compile(r"window\.state\s*=\s*(\{.*?\});\s*window\.webpackBundles", re.S)
SQM_TO_SQFT = 10.7639
SQFT_PER_MARLA = 225

PICTURE_FIELDS = {"coverPhoto", "photos", "photoIDs", "photoCount", "coverVideo", "videos", "videoCount"}


class ParseError(Exception):
    pass


@dataclass
class SearchPage:
    listings: list = field(default_factory=list)
    agencies: list = field(default_factory=list)
    locations: list = field(default_factory=list)       # every location seen in listings (for the area tree)
    sub_locations: list = field(default_factory=list)   # areas inside the searched location (for splitting)
    total_hits: int = 0
    total_pages: int = 0
    page: int = 1
    purpose: str = None          # what Zameen says this page is, to check we got the page we asked for
    category_slug: str = None
    location_slug: str = None


def extract_state(html):
    m = STATE_RE.search(html or "")
    if not m:
        raise ParseError("window.state not found (blocked, captcha page, or layout changed)")
    return json.loads(m.group(1))


def _ts(ts):
    """Zameen's Unix timestamp -> a moment with its time zone (UTC)."""
    return datetime.fromtimestamp(ts, timezone.utc) if ts else None


def _numbers(*values):
    """Unique phone numbers, in order, joined with ', '."""
    out = []
    for v in values:
        for n in (v if isinstance(v, list) else [v]):
            if n and n not in out:
                out.append(n)
    return ", ".join(out) or None


def strip_pictures(hit):
    clean = {k: v for k, v in hit.items() if k not in PICTURE_FIELDS}
    if isinstance(clean.get("agency"), dict):
        clean["agency"] = {k: v for k, v in clean["agency"].items() if k != "logo"}
    return clean


def parse_agency(a):
    if not a or not a.get("externalID"):
        return None
    created = a.get("createdAt")
    return {
        "id": int(a["externalID"]),
        "name": a.get("name"),
        "slug": a.get("slug"),
        "product": a.get("product"),               # titanium plus / titanium / ...
        "tier": a.get("tier"),
        "product_score": a.get("productScore"),
        "zameen_created": datetime.fromisoformat(created).date() if created else None,
    }


def parse_listing(h):
    area_sqm = h.get("area")
    sqft = area_sqm * SQM_TO_SQFT if area_sqm else None
    locs = h.get("location") or []
    cats = h.get("category") or []
    geo = h.get("geography") or {}
    phones = h.get("phoneNumber") or {}
    agency = h.get("agency") or {}
    installments = h.get("installments") or {}
    return {
        "id": int(h["externalID"]),
        "object_id": h.get("id"),
        "purpose": h.get("purpose"),
        "category_group": cats[0].get("name") if cats else None,
        "type": cats[-1].get("name") if cats else None,
        "type_slug": cats[-1].get("slug") if cats else None,
        "title": h.get("title"),
        "title_ur": h.get("title_l1"),
        "short_description": h.get("shortDescription"),
        "price": int(h["price"]) if h.get("price") is not None else None,
        "remaining_installments": installments.get("remainingInstallments"),
        "beds": h.get("rooms"),
        "baths": h.get("baths"),
        "area_sqm": round(area_sqm, 2) if area_sqm else None,
        "area_sqft": round(sqft) if sqft else None,
        "area_marla": round(sqft / SQFT_PER_MARLA, 2) if sqft else None,
        "location_id": int(locs[-1]["externalID"]) if locs else None,     # deepest location
        "location_path": " > ".join(l.get("name") or "" for l in locs[1:]) or None,  # without "Pakistan"
        "lat": geo.get("lat"),
        "lng": geo.get("lng"),
        "exact_geography": h.get("hasExactGeography"),
        "agency_id": int(agency["externalID"]) if agency.get("externalID") else None,
        "agency_name": agency.get("name"),
        "contact_name": h.get("contactName"),
        "mobile": _numbers(phones.get("mobileNumbers"), phones.get("mobile"), h.get("mobilePhoneNumber")),
        "phone": _numbers(phones.get("phoneNumbers"), phones.get("phone"), h.get("primaryPhoneNumber")),
        "whatsapp": phones.get("whatsapp"),
        "verified": bool(h.get("isVerified")),
        "listing_tier": h.get("product"),              # hot / superhot / ...
        # not kept (2026-11-10, always empty or always the same): rentFrequency, permitNumber,
        # referenceNumber (Zameen sends them blank), propertyTour (always false), state (always active).
        # They're still in `raw`.
        "url": f"https://www.zameen.com/Property/{h['slug']}.html" if h.get("slug") else None,
        "zameen_created": _ts(h.get("createdAt")),
        "zameen_updated": _ts(h.get("updatedAt")),
        "raw": strip_pictures(h),
    }


def parse_locations(h):
    """Each listing carries its full path: Pakistan > Islamabad Capital > Islamabad > I-11 > I-11/2."""
    out, parent = [], None
    for loc in h.get("location") or []:
        lid = int(loc["externalID"])
        out.append({   # no lat/lng: Zameen sends no map position for areas (each ad has its own)
            "id": lid, "name": loc.get("name"), "name_ur": loc.get("name_l1"), "slug": loc.get("slug"),
            "level": loc.get("level"), "parent_id": parent,
        })
        parent = lid
    return out


def _filter_value(filters, key):
    return ((filters or {}).get(key) or {}).get("value")


def parse_search_page(html):
    state = extract_state(html)
    algolia = state.get("algolia") or {}
    content = algolia.get("content") or {}
    filters = algolia.get("filters") or {}
    hits = content.get("hits") or []

    locations, agencies = {}, {}
    for h in hits:
        for loc in parse_locations(h):
            locations[loc["id"]] = loc
        a = parse_agency(h.get("agency"))
        if a:
            agencies[a["id"]] = a

    links = ((state.get("searchPageInternalLinks") or {}).get("data") or {}).get("locations") or []
    sub_locations = [
        {"slug": l["location"]["slug"], "name": l["location"]["name"], "count": l.get("ad_active_count")}
        for l in links if (l.get("location") or {}).get("slug")
    ]

    category = _filter_value(filters, "category.slug") or {}
    location = _filter_value(filters, "location.slug")
    if isinstance(location, list):
        location = location[-1] if location else None
    city = _filter_value(filters, "city.slug") or {}
    return SearchPage(
        listings=[parse_listing(h) for h in hits],
        agencies=list(agencies.values()),
        locations=list(locations.values()),
        sub_locations=sub_locations,
        total_hits=content.get("nbHits", 0),
        total_pages=content.get("nbPages", 0),
        page=_filter_value(filters, "page") or 1,
        purpose=_filter_value(filters, "purpose"),
        category_slug=category.get("slug") if isinstance(category, dict) else None,
        location_slug=(location or {}).get("slug") or city.get("slug"),
    )
