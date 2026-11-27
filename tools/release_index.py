"""Build the repository's publication-ordered prerelease download index."""

import json
from pathlib import Path
from urllib.parse import quote
from urllib.request import Request, urlopen


REPOSITORY = "hypnotoadtrance/silverfish-coop"
RELEASES = f"https://github.com/{REPOSITORY}/releases"
INDEX = Path(__file__).resolve().parents[1] / "docs" / "RELEASES.md"


def published_prereleases():
    page = 1
    releases = []
    while True:
        url = f"https://api.github.com/repos/{REPOSITORY}/releases?per_page=100&page={page}"
        request = Request(url, headers={"Accept": "application/vnd.github+json", "User-Agent": "SilverfishCoop-ReleaseIndex"})
        with urlopen(request, timeout=15) as response:
            batch = json.load(response)
        releases.extend(row for row in batch if row["prerelease"] and not row["draft"] and row["published_at"])
        if len(batch) < 100:
            break
        page += 1
    if not releases:
        raise RuntimeError("GitHub returned no published prereleases")
    return sorted(releases, key=lambda row: (row["published_at"], row["id"]), reverse=True)


def render(releases):
    history = []
    for row in releases:
        tag = row["tag_name"]
        release_notes = f"[{row.get('name') or tag}]({RELEASES}/tag/{quote(tag, safe='')})"
        asset = next((asset for asset in row["assets"]
                      if asset["name"].startswith("Silverfish-Coop-RC-") and asset["name"].endswith(".zip")), None)
        published = row["published_at"].replace("T", " ").removesuffix("Z")
        history.append((published, release_notes, asset))

    _, latest_notes, latest_asset = history[0]
    latest_links = [latest_notes]
    if latest_asset:
        latest_links.insert(0, f"[Download {latest_asset['name']}]({latest_asset['browser_download_url']})")

    lines = ["# Release candidates", "",
             "Use the same ZIP on both PCs. These builds are for coordinated testing and bug reports. "
             "[Installation instructions](../README.md#install-or-update)", "",
             "## Latest test build", "", " · ".join(latest_links), "",
             "## Download history", "",
             "Candidates are listed by publication time, newest first. When older candidates are pruned, we keep "
             "the newest three from the current day and the newest candidate from each previous release day, "
             "using Mountain Time.", "",
             "| Published (UTC) | Release notes | ZIP |", "| --- | --- | --- |"]
    for published, release_notes, asset in history:
        download = f"[Download]({asset['browser_download_url']})" if asset else "See release notes"
        lines.append(f"| {published} | {release_notes} | {download} |")
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    INDEX.write_text(render(published_prereleases()), encoding="utf-8", newline="\n")
