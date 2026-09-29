from jobapply import queue, sources
from jobapply.__main__ import main
from jobapply.profile import Profile

P = Profile(name="A", email="a@x.com", keywords=["python", "remote"], exclude=["clearance"], min_salary=50000)


def job(**kw):
    return sources._job("t", kw.get("title", "Python Dev"), "Co", kw.get("url", "u"),
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
    (tmp_path / "profile.toml").write_text('[profile]\nname="A"\nemail="a@x.com"\nkeywords=["python"]\n')
    (tmp_path / "l.json").write_text('[{"title":"Python Dev","company":"Co","url":"http://j/1"}]')
    monkeypatch.setattr(sources, "SOURCES", {})
    main(["search", "-f", "l.json"])
    main(["review", "--approve-above", "1"])
    main(["apply", "--dry-run"])
    assert "Python Dev" in capsys.readouterr().out
    assert queue.load()
