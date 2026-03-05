import argparse
import re
from pathlib import Path
from typing import Dict, Iterable, List
from urllib.parse import urljoin
from urllib.request import Request, urlopen

SEED_PAGES = ["https://result.election.gov.np/"]

ELECTION_DEFAULTS: Dict[str, List[str]] = {
    "2082": [
        "https://result.election.gov.np/JSONFiles/ElectionResultCentral2082.txt",
        "https://result.election.gov.np/JSONFiles/Election2082/Local/Lookup/states.json",
        "https://result.election.gov.np/JSONFiles/Election2082/Local/Lookup/districts.json",
    ],
    "2079": [
        "https://result.election.gov.np/JSONFiles/ElectionResultCentral2079.txt",
        "https://result.election.gov.np/JSONFiles/ElectionResultState2079.txt",
    ],
}

PATTERN = re.compile(r"JSONFiles/[A-Za-z0-9_./-]+")
BASE_DIR = Path(__file__).resolve().parents[1]


def endpoints_file_for(election_id: str) -> Path:
    return BASE_DIR / "data" / f"endpoints_{election_id}.txt"


def fetch_html(url: str, timeout: int = 20) -> str:
    req = Request(url, headers={"User-Agent": "ElectionDiscover/1.0"})
    with urlopen(req, timeout=timeout) as response:
        return response.read().decode("utf-8", errors="replace")


def is_relevant(url: str, election_id: str) -> bool:
    return (
        f"Election{election_id}" in url
        or url.endswith(f"{election_id}.txt")
        or f"Election{election_id}/Local/Lookup/" in url
    )


def defaults_for_election(election_id: str) -> List[str]:
    return list(ELECTION_DEFAULTS.get(election_id, []))


def discover_for_election(seed_pages: Iterable[str], election_id: str) -> List[str]:
    discovered: set[str] = set()
    for seed in seed_pages:
        try:
            html = fetch_html(seed)
        except Exception:
            continue

        for rel in PATTERN.findall(html):
            abs_url = urljoin(seed, rel)
            if is_relevant(abs_url, election_id):
                discovered.add(abs_url)

    defaults = defaults_for_election(election_id)
    if not discovered:
        discovered.update(defaults)

    for endpoint in defaults:
        discovered.add(endpoint)

    return sorted(discovered)


def write_endpoints(endpoints: Iterable[str], out_path: Path) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", encoding="utf-8") as handle:
        for url in sorted(set(endpoints)):
            handle.write(f"{url}\n")


def _parse_elections(raw: str) -> List[str]:
    values = [item.strip() for item in raw.split(",") if item.strip()]
    return values or ["2082", "2079"]


def main() -> None:
    parser = argparse.ArgumentParser(description="Discover Election JSON endpoints")
    parser.add_argument(
        "--seed",
        action="append",
        default=None,
        help="Seed page URL (can be provided multiple times)",
    )
    parser.add_argument(
        "--election",
        action="append",
        default=None,
        help="Election id (can be provided multiple times), e.g. 2082",
    )
    args = parser.parse_args()

    seeds = args.seed or SEED_PAGES
    elections = args.election or _parse_elections("2082,2079")

    for election_id in elections:
        endpoints = discover_for_election(seeds, election_id)
        out = endpoints_file_for(election_id)
        write_endpoints(endpoints, out)
        print(f"Wrote {len(endpoints)} endpoints to {out}")


if __name__ == "__main__":
    main()
