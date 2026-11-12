"""The list of things to scrape: cities, and in each city every property type x (for sale, for rent).

A category key looks like 'for-rent/Houses_Property'; together with a city it's one search:
    for sale: https://www.zameen.com/Houses_Property/Lahore-1-1.html
    for rent: https://www.zameen.com/Rentals_Houses_Property/Lahore-1-1.html

A city is a dict: {"name": "Lahore", "slug": "/Lahore-1", "skip": [...]}.
Every city is included in new-ad checks (the one on/off switch is NEW_CHECKS_ENABLED in .env);
'skip' lists categories that don't exist in that city. Anything without a city uses 'default_city' (Islamabad).
"""
import json
import os
from dataclasses import dataclass

from . import config

BASE = "https://www.zameen.com"


@dataclass(frozen=True)
class Category:
    purpose: str        # for-sale / for-rent
    group: str          # Homes / Plots / Commercial
    name: str           # Houses
    slug: str           # Houses_Property
    confirmed: bool
    enabled: bool

    @property
    def key(self):
        return f"{self.purpose}/{self.slug}"

    @property
    def label(self):
        return f"{self.name} for {'rent' if self.purpose == 'for-rent' else 'sale'}"

    @property
    def path(self):
        """'/Rentals_Houses_Property' or '/Houses_Property'"""
        return ("/Rentals_" if self.purpose == "for-rent" else "/") + self.slug

    def url(self, location_slug, page=1):
        """location_slug like '/Islamabad-3' or '/Islamabad_I_11-706'"""
        return f"{BASE}{self.path}{location_slug}-{page}.html"


@dataclass(frozen=True)
class Group:
    """A whole group in one list, e.g. "Homes for sale" (houses, flats, portions... mixed together).
    Each ad in it still says its own type. Used by the new-ad check: 6 lists per city instead of 36."""
    purpose: str        # for-sale / for-rent
    name: str           # Homes / Plots / Commercial

    @property
    def key(self):
        return f"{self.purpose}/{self.name}"

    @property
    def label(self):
        return f"{self.name} for {'rent' if self.purpose == 'for-rent' else 'sale'}"

    def url(self, location_slug, page=1):
        """e.g. https://www.zameen.com/Rentals_Homes/Lahore-1-2.html"""
        return f"{BASE}{'/Rentals_' if self.purpose == 'for-rent' else '/'}{self.name}{location_slug}-{page}.html"


def groups_of(cats):
    """{Group: [its categories]} for the given categories, in their order."""
    out = {}
    for c in cats:
        out.setdefault(Group(c.purpose, c.group), []).append(c)
    return out


_cache = {}


def _config(path=None):
    """categories.json, read again only when the file changes (the viewer asks for it many times a page)."""
    path = path or config.CATEGORIES_FILE
    mtime = os.path.getmtime(path)
    if _cache.get(path, (None,))[0] != mtime:
        with open(path, encoding="utf-8") as f:
            _cache[path] = (mtime, json.load(f))
    return _cache[path][1]


def cities(path=None):
    """Every city in categories.json, in its order."""
    return [{"skip": [], **c} for c in _config(path)["cities"]]


def city(name=None, path=None):
    """One city by name (any case); no name = the default city. Unknown names raise ValueError."""
    name = name or _config(path)["default_city"]
    found = [c for c in cities(path) if c["name"].lower() == name.lower()]
    if not found:
        raise ValueError(f"unknown city '{name}'. See: python main.py cities")
    return found[0]


def load(path=None, city_name=None):
    """(city, categories) for one city (default: the default city), without the categories it skips."""
    cfg = _config(path)
    c = city(city_name, path)
    cats = [Category(purpose, t["group"], t["name"], t["slug"], t.get("confirmed", False), t.get("enabled", True))
            for t in cfg["types"] for purpose in t.get("purposes", cfg["purposes"])]
    return c, [cat for cat in cats if cat.key not in c["skip"]]


def select(keys=None, groups=None, path=None, city_name=None):
    """Pick categories by key ('for-rent/Houses_Property'), by slug ('Houses_Property', both purposes)
    or by group ('Homes'). No filter = every enabled category. Unknown names raise ValueError."""
    c, cats = load(path, city_name)
    if not keys and not groups:
        return c, [cat for cat in cats if cat.enabled]
    out = []
    for k in keys or []:
        found = [cat for cat in cats if k.lower() in (cat.key.lower(), cat.slug.lower())]
        if not found:
            raise ValueError(f"unknown category '{k}'. See: python main.py categories")
        out += found
    for g in groups or []:
        found = [cat for cat in cats if cat.group.lower() == g.lower()]
        if not found:
            raise ValueError(f"unknown group '{g}'. Use Homes, Plots or Commercial")
        out += found
    seen, unique = set(), []
    for cat in out:
        if cat.key not in seen:
            seen.add(cat.key)
            unique.append(cat)
    return c, unique
