"""Derive the shareable artifact page from templates/handbook.html.

The handbook is served at /docs as a complete HTML document; the Artifact host wraps
page content in its own skeleton and rejects a second <html>/<head>/<body>. Rather
than keep two copies that quietly diverge, this strips the wrapper from the one
source of truth.

    .\\venv\\Scripts\\python _build_handbook_artifact.py
"""
from __future__ import annotations

import os
import re

SRC = os.path.join("templates", "handbook.html")
OUT = os.path.join(
    os.environ.get("SCRATCH", os.path.join(os.path.expanduser("~"), "handbook")),
    "handbook-artifact.html")


def main(out_path: str = OUT) -> str:
    html = open(SRC, encoding="utf-8").read()

    # keep <title> and <style> from the head, drop the document scaffolding
    title = re.search(r"<title>.*?</title>", html, re.S)
    style = re.search(r"<style>.*?</style>", html, re.S)
    body = re.search(r"<body>(.*)</body>", html, re.S)
    if not (title and style and body):
        raise SystemExit("handbook.html is not shaped as expected")

    page = f"{title.group(0)}\n{style.group(0)}\n{body.group(1).strip()}\n"

    # sanity: the artifact host forbids these outright
    for banned in ("<!doctype", "<html", "<head", "<body", "</html>"):
        if banned in page.lower():
            raise SystemExit(f"stripped output still contains {banned!r}")

    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as fh:
        fh.write(page)
    print(f"wrote {out_path}  ({len(page):,} bytes)")
    return out_path


if __name__ == "__main__":
    main()
