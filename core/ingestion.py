from abc import ABC, abstractmethod
from typing import List, Dict
import requests
from bs4 import BeautifulSoup


class TenderSource(ABC):
    source_name: str = "base"

    @abstractmethod
    def fetch(self) -> List[Dict]:
        raise NotImplementedError


class ManualSource(TenderSource):
    source_name = "manual"

    def fetch(self) -> List[Dict]:
        return []


class JsonApiSource(TenderSource):
    source_name = "json_api"

    def __init__(self, endpoint: str):
        self.endpoint = endpoint

    def fetch(self) -> List[Dict]:
        if not self.endpoint:
            return []
        try:
            resp = requests.get(self.endpoint, timeout=20)
            resp.raise_for_status()
            data = resp.json()
            if isinstance(data, list):
                return data
            if isinstance(data, dict):
                return data.get("items", []) or data.get("results", []) or []
        except Exception:
            return []
        return []


class CpppFeedSource(TenderSource):
    """
    CPPP-style connector.
    Works with RSS/Atom/XML endpoints or HTML pages containing tender rows.
    """
    source_name = "cppp_feed"

    def __init__(self, endpoint: str):
        self.endpoint = endpoint

    def fetch(self) -> List[Dict]:
        if not self.endpoint:
            return []
        try:
            resp = requests.get(self.endpoint, timeout=25)
            resp.raise_for_status()
            content_type = (resp.headers.get("content-type") or "").lower()
            text = resp.text or ""

            # RSS / XML handling
            if "xml" in content_type or "<rss" in text.lower() or "<feed" in text.lower():
                soup = BeautifulSoup(text, "xml")
                items = soup.find_all(["item", "entry"])
                out = []
                for it in items[:500]:
                    title = (it.find("title").text if it.find("title") else "").strip()
                    ext_id = (
                        (it.find("guid").text if it.find("guid") else "")
                        or (it.find("id").text if it.find("id") else "")
                        or title
                    ).strip()
                    out.append(
                        {
                            "id": ext_id[:255],
                            "title": title[:1000],
                            "org_name": (it.find("author").text if it.find("author") else "")[:255],
                            "deadline": (it.find("pubDate").text if it.find("pubDate") else "")[:120],
                            "reference_no": "",
                            "description": (it.find("description").text if it.find("description") else "")[:5000],
                        }
                    )
                return out

            # HTML fallback
            return StatePortalTableSource(self.endpoint).fetch()
        except Exception:
            return []


class StatePortalTableSource(TenderSource):
    """
    Generic state portal adapter that parses HTML tables and maps columns heuristically.
    """
    source_name = "state_portal_table"

    def __init__(self, endpoint: str):
        self.endpoint = endpoint

    def fetch(self) -> List[Dict]:
        if not self.endpoint:
            return []
        try:
            resp = requests.get(self.endpoint, timeout=25)
            resp.raise_for_status()
            soup = BeautifulSoup(resp.text, "html.parser")
            rows = []

            table = soup.find("table")
            if not table:
                return []

            header_cells = table.find_all("th")
            headers = [h.get_text(" ", strip=True).lower() for h in header_cells]

            for tr in table.find_all("tr")[1:800]:
                tds = tr.find_all("td")
                if not tds:
                    continue
                vals = [td.get_text(" ", strip=True) for td in tds]

                def pick(possible_names, default_idx):
                    for name in possible_names:
                        for i, h in enumerate(headers):
                            if name in h and i < len(vals):
                                return vals[i]
                    return vals[default_idx] if default_idx < len(vals) else ""

                title = pick(["title", "brief", "tender"], 1)
                ext_id = pick(["id", "nit", "tender id", "sr"], 0) or title
                org_name = pick(["department", "organization", "authority"], 2)
                deadline = pick(["deadline", "due", "closing"], 3)
                ref_no = pick(["reference", "ref", "bid no"], 4)

                rows.append(
                    {
                        "id": str(ext_id)[:255],
                        "title": str(title)[:1000],
                        "org_name": str(org_name)[:255],
                        "deadline": str(deadline)[:120],
                        "reference_no": str(ref_no)[:255],
                        "description": " ".join(vals)[:5000],
                    }
                )
            return rows
        except Exception:
            return []


class IngestionRegistry:
    def __init__(self):
        self._sources: dict[str, TenderSource] = {}

    def register(self, source: TenderSource):
        self._sources[source.source_name] = source

    def list_sources(self) -> list[str]:
        return sorted(self._sources.keys())


registry = IngestionRegistry()
registry.register(ManualSource())
registry.register(JsonApiSource(endpoint=""))
registry.register(CpppFeedSource(endpoint=""))
registry.register(StatePortalTableSource(endpoint=""))
