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


def _job(source, title, company, url, location="", description="", salary=0, email="", contact="", force=False):
    return {
        "id": _id(source, url), "source": source, "title": title, "company": company,
        "url": url, "location": location, "description": description,
        "salary": salary, "email": email, "contact": contact, "force": bool(force), "status": "new", "score": 0,
    }


def remotive(query: str, p: Profile | None = None) -> list[dict]:
    r = requests.get("https://remotive.com/api/remote-jobs", params={"search": query},
                     headers=UA, timeout=30)
    r.raise_for_status()
    return [
        _job("remotive", j["title"], j["company_name"], j["url"],
             j.get("candidate_required_location", ""), j.get("description", ""))
        for j in r.json().get("jobs", [])
    ]


def remoteok(query: str, p: Profile | None = None) -> list[dict]:
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
             r.get("description", ""), int(r.get("salary") or 0), r.get("email", ""), r.get("contact", ""),
             str(r.get("force", "")).lower() in ("1", "true", "yes"))
        for r in rows
    ]


def adzuna(query: str, p: Profile) -> list[dict]:
    """Adzuna job search API (free key: developer.adzuna.com). Env: ADZUNA_APP_ID, ADZUNA_APP_KEY."""
    import os
    app_id, app_key = os.environ.get("ADZUNA_APP_ID"), os.environ.get("ADZUNA_APP_KEY")
    if not (app_id and app_key):
        raise RuntimeError("set ADZUNA_APP_ID and ADZUNA_APP_KEY (free at developer.adzuna.com)")
    out = []
    for where in p.search_locations:
        r = requests.get("https://api.adzuna.com/v1/api/jobs/us/search/1", headers=UA, timeout=30,
                         params={"app_id": app_id, "app_key": app_key, "what": query, "where": where,
                                 "distance": 15, "max_days_old": 14, "results_per_page": 50})
        r.raise_for_status()
        for j in r.json().get("results", []):
            out.append(_job("adzuna", j["title"], j.get("company", {}).get("display_name", ""),
                            j["redirect_url"], j.get("location", {}).get("display_name", ""),
                            j.get("description", ""), int(j.get("salary_min") or 0)))
    return out


def from_inbox(folder: str) -> list[dict]:
    import glob
    out = []
    for f in sorted(glob.glob(f"{folder}/*.json") + glob.glob(f"{folder}/*.csv")):
        out += from_file(f)
    return out


SOURCES = {"remotive": remotive, "remoteok": remoteok, "adzuna": adzuna}


def _hits(job: dict, p: Profile) -> int:
    text = re.sub(r"<[^>]+>", " ", f"{job['title']} {job['description']}".lower())
    kws = dict.fromkeys(k.lower() for k in p.keywords + p.title_keywords)  # title words count too
    return sum(k in text for k in kws)


def score(job: dict, p: Profile) -> int:
    """-1 = rejected by filters, otherwise number of keyword hits."""
    if job.get("force"):  # user asked for this one by name: skip the filters
        return max(1, _hits(job, p))
    if p.title_keywords and not any(k.lower() in job["title"].lower() for k in p.title_keywords):
        return -1
    text = f"{job['title']} {job['description']}".lower()
    text = re.sub(r"<[^>]+>", " ", text)
    if any(x.lower() in text for x in p.exclude):
        return -1
    if p.locations and job["location"] and not any(
            l.lower() in job["location"].lower() for l in p.locations + ["anywhere", "worldwide"]):
        return -1
    if p.min_salary and job["salary"] and job["salary"] < p.min_salary:
        return -1
    return _hits(job, p)
