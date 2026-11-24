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
    lines = ["# Release candidates", "",
             "Use the same ZIP on both PCs. These builds are for coordinated testing and bug reports. "
             "[Installation instructions](../README.md#install-or-update)", "",
             "Candidates are listed by when they were published, newest first.", "",
             "| Published (UTC) | Release notes | ZIP |", "| --- | --- | --- |"]
    for row in releases:
        tag = row["tag_name"]
        url_tag = quote(tag, safe="")
        published = row["published_at"].replace("T", " ").removesuffix("Z")
        package = f"Silverfish-Coop-RC-{tag.removeprefix('rc-')}.zip"
        asset = next((asset["name"] for asset in row["assets"] if asset["name"] == package), None)
        download = f"[Download]({RELEASES}/download/{url_tag}/{quote(asset, safe='')})" if asset else "See release notes"
        lines.append(f"| {published} | [`{tag}`]({RELEASES}/tag/{url_tag}) | {download} |")
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    INDEX.write_text(render(published_prereleases()), encoding="utf-8", newline="\n")
