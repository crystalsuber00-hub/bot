from __future__ import annotations

import csv
import hashlib
import json
import re

import requests

from .profile import Profile

UA = {"User-Agent": "jobapply/0.1"}


def _id(source: str, url: str) -> str:
    return hashlib.sha1(f"{source}|{url}".encode()).hexdigest()[:12]


def _job(source, title, company, url, location="", description="", salary=0, email=""):
    return {
        "id": _id(source, url), "source": source, "title": title, "company": company,
        "url": url, "location": location, "description": description,
        "salary": salary, "email": email, "status": "new", "score": 0,
    }


def remotive(query: str) -> list[dict]:
    r = requests.get("https://remotive.com/api/remote-jobs", params={"search": query},
                     headers=UA, timeout=30)
    r.raise_for_status()
    return [
        _job("remotive", j["title"], j["company_name"], j["url"],
             j.get("candidate_required_location", ""), j.get("description", ""))
        for j in r.json().get("jobs", [])
    ]


def remoteok(query: str) -> list[dict]:
    r = requests.get("https://remoteok.com/api", headers=UA, timeout=30)
    r.raise_for_status()
    q = query.lower()
    out = []
    for j in r.json():
        if "position" not in j:  # first element is a legal notice
            continue
        blob = f"{j['position']} {' '.join(j.get('tags', []))}".lower()
        if q in blob:
            out.append(_job("remoteok", j["position"], j.get("company", ""), j["url"],
                            j.get("location", ""), j.get("description", ""),
                            int(j.get("salary_min") or 0)))
    return out


def from_file(path: str) -> list[dict]:
    """Import listings from JSON (list of objects) or CSV with title,company,url,... columns."""
    if path.endswith(".json"):
        rows = json.load(open(path))
    else:
        rows = list(csv.DictReader(open(path, newline="")))
    return [
        _job("file", r["title"], r.get("company", ""), r["url"], r.get("location", ""),
             r.get("description", ""), int(r.get("salary") or 0), r.get("email", ""))
        for r in rows
    ]


SOURCES = {"remotive": remotive, "remoteok": remoteok}


def score(job: dict, p: Profile) -> int:
    """-1 = rejected by filters, otherwise number of keyword hits."""
    text = f"{job['title']} {job['description']}".lower()
    text = re.sub(r"<[^>]+>", " ", text)
    if any(x.lower() in text for x in p.exclude):
        return -1
    if p.locations and job["location"] and not any(
            l.lower() in job["location"].lower() for l in p.locations + ["anywhere", "worldwide"]):
        return -1
    if p.min_salary and job["salary"] and job["salary"] < p.min_salary:
        return -1
    return sum(k.lower() in text for k in p.keywords)
