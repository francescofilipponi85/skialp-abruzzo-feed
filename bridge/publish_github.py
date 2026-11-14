#!/usr/bin/env python3
"""Pubblica soltanto latest.json. Il token resta sul NAS e non compare nei log."""
import argparse
import base64
import json
import re
import urllib.error
import urllib.request
from pathlib import Path

def request(url, token, method="GET", payload=None):
    headers = {
        "Authorization": "Bearer " + token,
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "SkiAlp-Abruzzo-Publisher",
    }
    if payload is not None:
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, headers=headers, data=payload, method=method)
    try:
        with urllib.request.urlopen(req, timeout=30) as response:
            return json.load(response)
    except urllib.error.HTTPError as err:
        if err.code == 404 and method == "GET":
            return None
        raise RuntimeError("GitHub API HTTP " + str(err.code)) from None

def publish(repo, branch, token_path, filename):
    if not re.fullmatch(r"[a-zA-Z0-9_.-]+/[a-zA-Z0-9_.-]+", repo):
        raise ValueError("Repository non valido")
    token = Path(token_path).read_text().strip()
    if len(token) < 20:
        raise ValueError("Token mancante")
    raw = Path(filename).read_bytes()
    if len(raw) > 512000:
        raise ValueError("Feed troppo grande")
    obj = json.loads(raw)
    if obj.get("schema_version") != 1 or len(obj.get("zones", [])) != 6:
        raise ValueError("Feed non valido")
    url = "https://api.github.com/repos/" + repo + "/contents/latest.json"
    existing = request(url + "?ref=" + branch, token)
    body = {
        "message": "Aggiorna osservazioni meteorologiche SkiAlp",
        "branch": branch,
        "content": base64.b64encode(raw).decode("ascii"),
    }
    if existing is not None:
        body["sha"] = existing["sha"]
    request(url, token, "PUT", json.dumps(body).encode())
    print("OK: feed meteo aggiornato su GitHub")

if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--repo", default="francescofilipponi85/skialp-abruzzo-feed")
    p.add_argument("--branch", default="main")
    p.add_argument("--token-file", default="/config/skialp_abruzzo/secrets/github_feed_token")
    p.add_argument("--source", default="/config/skialp_abruzzo/public/latest.json")
    a = p.parse_args()
    publish(a.repo, a.branch, a.token_file, a.source)
