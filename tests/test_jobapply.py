from pathlib import Path
from jobapply import queue, sources
from jobapply.__main__ import main
from jobapply.profile import Profile

P = Profile(name="A", email="a@x.com", keywords=["payroll", "remote"], exclude=["clearance"], min_salary=50000)


def job(**kw):
    return sources._job("t", kw.get("title", "Payroll Specialist"), "Co", kw.get("url", "u"),
                        description=kw.get("description", ""), salary=kw.get("salary", 0))


def test_score_and_filters():
    assert sources.score(job(description="remote work"), P) == 2
    assert sources.score(job(description="needs clearance"), P) == -1
    assert sources.score(job(salary=40000), P) == -1


def test_merge_dedupes():
    jobs = {}
    assert queue.merge(jobs, [job(), job()]) == 1


def test_cli_flow(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "profile.toml").write_text('[profile]\nname="A"\nemail="a@x.com"\nkeywords=["payroll"]\n')
    (tmp_path / "l.json").write_text('[{"title":"Payroll Specialist","company":"Co","url":"http://j/1"}]')
    monkeypatch.setattr(sources, "SOURCES", {})
    main(["search", "-f", "l.json"])
    main(["review", "--approve-above", "1"])
    main(["apply", "--dry-run"])
    assert "Payroll Specialist" in capsys.readouterr().out
    assert queue.load()


def test_intro_and_cap(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "profile.toml").write_text('[profile]\nname="A"\nemail="a@x.com"\nkeywords=["payroll"]\ndaily_limit=1\ndelay_seconds=0\n')
    (tmp_path / "l.csv").write_text("title,company,url,email,contact\nPayroll Specialist,Co,http://j/1,jobs@co.com,hr@co.com\n"
                                    "Payroll Specialist,Co2,http://j/2,jobs@co2.com,hr@co2.com\n")
    monkeypatch.setattr(sources, "SOURCES", {})
    main(["search", "-f", "l.csv"])
    main(["review", "--approve-above", "1"])
    main(["apply", "--dry-run"])
    assert "[intro email to hr@co.com]" in capsys.readouterr().out
    sent = []
    from jobapply import __main__ as m
    monkeypatch.setattr(m, "send_email", lambda j, p, l, to="", subject="": sent.append(to or j["email"]))
    main(["apply"])
    assert sent == ["jobs@co.com", "hr@co.com"]  # cap of 1 application (+its intro)


def test_preview_uses_headline_and_bullets(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "profile.toml").write_text(
        '[profile]\nname="A"\nemail="a@x.com"\nphone="1"\nheadline="I am great."\n'
        'highlights=["Did X","Did Y"]\n')
    main(["preview"])
    out = capsys.readouterr().out
    assert "- Did X\n- Did Y" in out and "I am great." in out
    assert "Subject: Application: Payroll Specialist - A" in out
    assert "Subject: Introduction:" in out
    assert "\n\n\n" not in out


def test_no_highlights_falls_back_to_summary(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "profile.toml").write_text('[profile]\nname="A"\nemail="a@x.com"\nsummary="Old summary."\n')
    main(["preview"])
    assert "Old summary." in capsys.readouterr().out


def test_failed_intro_does_not_crash_and_is_retried(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "profile.toml").write_text('[profile]\nname="A"\nemail="a@x.com"\nkeywords=["payroll"]\ndelay_seconds=0\n')
    (tmp_path / "l.csv").write_text("title,company,url,email,contact\nPayroll Specialist,Co,http://j/1,jobs@co.com,hr@co.com\n")
    monkeypatch.setattr(sources, "SOURCES", {})
    main(["search", "-f", "l.csv"])
    main(["review", "--approve-above", "1"])
    from jobapply import __main__ as m
    def boom(j, p, l, to="", subject=""):
        if to:
            raise RuntimeError("bad password")
    monkeypatch.setattr(m, "send_email", boom)
    main(["apply"])  # application ok, intro fails -> must not raise
    job = next(iter(queue.load().values()))
    assert job["status"] == "applied" and not job.get("intro_sent")
    sent = []
    monkeypatch.setattr(m, "send_email", lambda j, p, l, to="", subject="": sent.append(to))
    main(["apply"])  # retry
    assert sent == ["hr@co.com"] and next(iter(queue.load().values()))["intro_sent"]


def test_contacts_file_fills_intro_and_watch_only_alerts(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "profile.toml").write_text('[profile]\nname="A"\nemail="a@x.com"\nkeywords=["payroll"]\nsources=[]\n')
    (tmp_path / "contacts.csv").write_text("company,email\nFormco,hr@f.com\n")
    (tmp_path / "inbox").mkdir()
    (tmp_path / "inbox" / "l.csv").write_text("title,company,url,email,contact\nPayroll Clerk,Formco,http://j/2,,\n")
    from jobapply import __main__ as m
    alerts, sent = [], []
    monkeypatch.setattr(m, "alert", lambda t, b="": alerts.append(t))
    monkeypatch.setattr(m, "send_email", lambda *a, **k: sent.append(1))
    main(["watch", "--once"])
    job = next(iter(queue.load().values()))
    assert job["contact"] == "hr@f.com" and job["status"] == "new"   # not approved, not sent
    assert len(alerts) == 1 and sent == []
    main(["watch", "--once"])
    assert len(alerts) == 1                                        # no repeat alert


def test_rescore_reopens_location_rejects(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    prof = '[profile]\nname="A"\nemail="a@x.com"\nkeywords=["payroll"]\nsources=[]\nlocations=["Oakland"]\n'
    (tmp_path / "profile.toml").write_text(prof)
    (tmp_path / "l.csv").write_text("title,company,url,location\nPayroll Clerk,Co,http://j/1,\"Los Gatos, CA\"\n")
    main(["search", "-f", "l.csv"])
    assert next(iter(queue.load().values()))["status"] == "rejected"
    (tmp_path / "profile.toml").write_text(prof.replace('["Oakland"]', '["Oakland", "Los Gatos"]'))
    main(["review", "--rescore", "--approve-above", "1"])
    assert next(iter(queue.load().values()))["status"] == "approved"


def test_headlines_rotate_per_job_but_stay_stable():
    from jobapply.apply import cover_letter
    p = Profile(name="A", email="a@x.com", headlines=["H0.", "H1.", "H2."])
    jobs = [sources._job("t", "Payroll Clerk", "Co", f"http://j/{i}") for i in range(12)]
    used = {next(h for h in p.headlines if h in cover_letter(j, p)) for j in jobs}
    assert len(used) > 1                                         # variety across jobs
    assert all(cover_letter(j, p) == cover_letter(j, p) for j in jobs)  # same job, same text


def test_cover_letter_cap_makes_the_rest_resume_only(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "profile.toml").write_text(
        '[profile]\nname="A"\nemail="a@x.com"\nkeywords=["payroll"]\nsources=[]\ncover_letters_per_day=2\n')
    rows = "".join(f"Payroll Clerk,Co{i},http://j/{i}\n" for i in range(4))
    (tmp_path / "l.csv").write_text("title,company,url\n" + rows)
    from jobapply import __main__ as m
    copied = []
    monkeypatch.setattr(m, "open_manual", lambda j, l, d, copy=True: copied.append(copy) or Path("x"))
    monkeypatch.setattr("builtins.input", lambda *_: "")
    main(["search", "-f", "l.csv"])
    main(["review", "--approve-above", "1"])
    main(["apply"])
    assert copied == [True, True, False, False]
    assert "resume only" in capsys.readouterr().out


def test_accounts_payable_titles_are_accepted_and_scored():
    p = Profile(name="A", email="a@x.com", keywords=["payroll"])
    j = sources._job("t", "Accounts Payable Specialist", "Co", "u")
    assert sources.score(j, p) >= 1
    assert sources.score(sources._job("t", "Office Manager", "Co", "u2"), p) == -1
