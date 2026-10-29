"""Lookup tools for the locate agent: official, open Norwegian registers (no key needed).

  search_address   Kartverket's address register      e.g. "Storgata 5 Otta"   -> Sel
  search_place     Kartverket's place-name register   e.g. "Heidal"            -> Sel (+ other Heidals in Norway)
  search_business  Brønnøysund business register      e.g. "Trolltun hotell"   -> Dovre (region only)

Only the minimum is kept: the query, and for each hit its name, type and municipality. No street
addresses of businesses, no owner names. The locate agent may only search for words that are in the
submission. The business search is limited to the region's municipalities and only keeps names made
of the searched words plus a legal suffix (AS, ENK, ...) or a place name of the region. A business
named after a person ("Kari Nordmann ENK") is dropped unless the submission already had the full name.

Every lookup is saved under data/<dataset>/lookups/ so a run can be replayed offline.
"""

import hashlib
import http.client
import json
import urllib.error
import urllib.parse
import urllib.request

from common import norm_name, read_json, words, write_json

USER_AGENT = "aktivitetskalender-skeleton/0.1 (student project; community activity calendar)"
TOOLS = ("search_address", "search_place", "search_business")
LEGAL_WORDS = {"as", "asa", "ans", "da", "enk", "sa", "ba", "nuf", "ks", "avd", "avdeling"}
# Place-name types for where people live. A town beats a river with the same name ("Otta"),
# and a town beats a farm with the same name ("Gjøvik" is also a farm in Tingvoll).
TOWNS = {"by", "tettsted", "tettsteddel", "tettbebyggelse", "bydel"}
SETTLEMENTS = TOWNS | {"bygd", "grend", "gard", "bruk", "boligfelt"}


def lookup_key(tool, query):
    return hashlib.sha256(f"{tool}\n{query.strip().lower()}".encode("utf-8")).hexdigest()[:20]


class Lookups:
    def __init__(self, profile, cache_dir, offline=False):
        self.region = profile["municipalities"]
        self.number_to_name = {n: m for m, n in profile["municipality_numbers"].items()}
        self.region_words = words(" ".join(list(profile["municipalities"]) + list(profile["places"])))
        self.cache_dir = cache_dir
        self.offline = offline

    def run(self, tool, query):
        """Run one lookup. Returns {"tool", "query", "key", "results": [...], "error": str|None}."""
        query = " ".join(str(query or "").split())[:100]
        key = lookup_key(tool, query)
        path = self.cache_dir / f"{key}.json"
        if path.exists():
            return read_json(path)
        result = {"tool": tool, "query": query, "key": key, "results": [], "error": None}
        if tool not in TOOLS:
            result["error"] = f"unknown tool; use one of {', '.join(TOOLS)}"
            return result
        if not query:
            result["error"] = "empty query"
            return result
        if self.offline:
            result["error"] = "no saved lookup for this query (offline replay)"
            return result
        try:
            hits = getattr(self, "_" + tool.split("_", 1)[1])(query)
        except (urllib.error.URLError, http.client.HTTPException, OSError, ValueError, KeyError) as e:
            result["error"] = f"lookup failed: {e}"
            return result  # failures are not saved, so a later run can try again
        hits.sort(key=lambda h: not h["in_region"])
        result["results"] = hits[:8]
        write_json(path, result)
        return result

    # ----- helpers -----

    def _get(self, url, params):
        request = urllib.request.Request(url + "?" + urllib.parse.urlencode(params), headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(request, timeout=20) as response:
            return json.loads(response.read().decode("utf-8"))

    def _municipality(self, number, name):
        """Use the profile's spelling for the region; title-case others ('NORD-FRON' -> 'Nord-Fron')."""
        if number in self.number_to_name:
            return self.number_to_name[number]
        return "-".join(part.capitalize() for part in (name or "").lower().split("-")) or None

    def _hit(self, name, kind, number, municipality):
        municipality = self._municipality(number, municipality)
        return {"name": name, "type": kind, "municipality": municipality,
                "in_region": municipality in self.region}

    # ----- the three registers -----

    def _address(self, query):
        data = self._get("https://ws.geonorge.no/adresser/v1/sok", {"sok": query, "treffPerSide": 10})
        return [self._hit(f"{a['adressetekst']}, {a.get('postnummer', '')} {str(a.get('poststed', '')).title()}",
                          "address", a.get("kommunenummer"), a.get("kommunenavn"))
                for a in data.get("adresser", [])]

    def _place(self, query):
        data = self._get("https://api.kartverket.no/stedsnavn/v1/navn", {"sok": query, "treffPerSide": 20})
        return [self._hit(n["skrivemåte"], n.get("navneobjekttype", "place"), k.get("kommunenummer"), k.get("kommunenavn"))
                for n in data.get("navn", []) for k in n.get("kommuner", [])]

    def _business(self, query):
        data = self._get("https://data.brreg.no/enhetsregisteret/api/underenheter",
                         {"navn": query, "kommunenummer": ",".join(self.number_to_name), "size": 20})
        wanted = words(query)
        hits = []
        for unit in data.get("_embedded", {}).get("underenheter", []):
            name = unit.get("navn", "")
            extra = words(name) - wanted
            if not wanted or not wanted <= words(name) or not extra <= LEGAL_WORDS | self.region_words:
                continue  # privacy: no words beyond the search, a legal suffix and the region's place names
            address = unit.get("beliggenhetsadresse") or {}
            hits.append(self._hit(name.title(), "business", address.get("kommunenummer"), address.get("kommune")))
        return hits


def find_hit(lookup, municipality):
    """The first result of a saved lookup in the given municipality, or None."""
    target = norm_name(municipality)
    return next((h for h in lookup.get("results", []) if norm_name(h["municipality"]) == target), None)


def matching_hits(lookup):
    """The hits whose name contains every search word. For place names: towns if there are any,
    otherwise other settlements if there are any, otherwise all of them."""
    wanted = words(lookup.get("query"))
    hits = [h for h in lookup.get("results", []) if wanted and wanted <= words(h["name"])]
    towns = [h for h in hits if str(h.get("type", "")).lower() in TOWNS]
    settlements = [h for h in hits if str(h.get("type", "")).lower() in SETTLEMENTS]
    return towns or settlements or hits


def unambiguous_hit(lookup, municipality):
    """A hit in the given municipality, but only if every matching hit is in that municipality.
    "Otta" (a town in Sel, a river through Vågå) gives Sel; "Storgata 5" (many towns) gives None."""
    hits = matching_hits(lookup)
    if hits and {norm_name(h["municipality"]) for h in hits} == {norm_name(municipality)}:
        return hits[0]
    return None


def grounded(query, text):
    """Every word of the search (numbers aside) appears in the submission."""
    wanted = words(query)
    return bool(wanted) and wanted <= words(text)
