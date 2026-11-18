"""Stage A7 updater: release picking, verification and hand-off, with a fake GitHub."""
import datetime
import hashlib
import io
import zipfile

import pytest

from jarvis.core import updater
from jarvis.core.updater import UpdateError, UpdateInfo

DL = f"https://github.com/{updater.REPO}/releases/download"
PAGE = f"https://github.com/{updater.REPO}/releases/tag"


def make_zip(version="0.2.0", root="Jarvis/", extra=None):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr(f"{root}Jarvis.pyw", "import jarvis\n")
        z.writestr(f"{root}jarvis/__init__.py", f'"""Jarvis."""\n__version__ = "{version}"\n')
        z.writestr(f"{root}requirements.txt", "requests==1\n")
        for name, data in (extra or {}).items():
            z.writestr(name, data)
    return buf.getvalue()


def release(tag="v0.2.0", assets=None, draft=False, pre=False, body="Fixed things."):
    return {"tag_name": tag, "draft": draft, "prerelease": pre, "body": body,
            "html_url": f"{PAGE}/{tag}", "zipball_url": f"{updater.API}/zipball/{tag}",
            "assets": assets if assets is not None else []}


def asset(name="Jarvis-0.2.0.zip", data=b"", digest=None):
    a = {"name": name, "size": len(data), "browser_download_url": f"{DL}/v0.2.0/{name}"}
    if digest:
        a["digest"] = digest
    return a


class FakeHttp:
    def __init__(self, releases=None, files=None, final=None, fail=None):
        self.releases, self.files, self.final, self.fail = releases, files or {}, final, fail
        self.calls = []

    def get_json(self, url):
        self.calls.append(url)
        if self.fail:
            raise self.fail
        return self.releases

    def get_text(self, url):
        self.calls.append(url)
        return self.files[url].decode()

    def download(self, url, dest, progress=None):
        self.calls.append(url)
        data = self.files[url]
        dest.write_bytes(data)
        if progress:
            progress(len(data), len(data))
        return len(data), self.final or url


# ---------------------------------------------------------------- versions + picking
def test_versions():
    assert updater.parse_version("v0.1.3") == (0, 1, 3, 0)
    assert updater.parse_version("1.2") == (1, 2, 0, 0)
    assert updater.parse_version("0.2-beta") is None
    assert updater.is_newer("v0.1.10", "0.1.9")
    assert not updater.is_newer("0.1.2", "0.1.2")
    assert not updater.is_newer("v0.1.1", "0.1.2")
    assert not updater.is_newer("nightly", "0.1.2")


def test_pick_skips_drafts_prereleases_and_older():
    rels = [release("v0.3.0", draft=True), release("v0.4.0", pre=True), release("v0.2.0"), release("v0.1.0")]
    assert updater.pick_release(rels, "0.1.2")["tag_name"] == "v0.2.0"
    assert updater.pick_release(rels, "0.2.0") is None          # same version: nothing offered
    assert updater.pick_release(rels, "0.5.0") is None          # never offers a downgrade
    assert updater.pick_release([release("v0.1.0")], "0.1.2") is None


def test_check_returns_attached_zip_with_digest():
    data = make_zip()
    digest = "sha256:" + hashlib.sha256(data).hexdigest()
    http = FakeHttp([release(assets=[asset(data=data, digest=digest)])])
    info = updater.check(http, "0.1.2")
    assert info.version == "0.2.0" and info.notes == "Fixed things."
    assert info.url == f"{DL}/v0.2.0/Jarvis-0.2.0.zip"
    assert info.size == len(data) and info.sha256 == hashlib.sha256(data).hexdigest()
    assert http.calls[0].startswith(f"https://api.github.com/repos/{updater.REPO}/releases")


def test_check_reads_sha256_file():
    data = make_zip()
    h = hashlib.sha256(data).hexdigest()
    sha = asset("Jarvis-0.2.0.zip.sha256", b"x")
    http = FakeHttp([release(assets=[asset(data=data), sha])],
                    files={sha["browser_download_url"]: f"{h}  Jarvis-0.2.0.zip\n".encode()})
    assert updater.check(http, "0.1.2").sha256 == h


def test_check_falls_back_to_source_zip():
    info = updater.check(FakeHttp([release()]), "0.1.2")
    assert info.url == f"{updater.API}/zipball/v0.2.0" and info.size is None and info.sha256 is None


def test_check_none_when_up_to_date():
    assert updater.check(FakeHttp([release("v0.1.2")]), "0.1.2") is None


def test_check_rejects_other_repo():
    r = release(assets=[asset(data=b"x")])
    r["assets"][0]["browser_download_url"] = "https://github.com/someone/else/releases/download/v0.2.0/J.zip"
    with pytest.raises(UpdateError):
        updater.check(FakeHttp([r]), "0.1.2")
    r = release()
    r["html_url"] = "https://evil.example/releases/v0.2.0"
    with pytest.raises(UpdateError):
        updater.check(FakeHttp([r]), "0.1.2")


def test_check_offline_is_friendly():
    with pytest.raises(UpdateError, match="couldn't reach GitHub"):
        updater.check(FakeHttp(fail=OSError("no net")), "0.1.2")


def test_once_a_day():
    assert updater.due_today()
    updater.check(FakeHttp([]), "0.1.2")
    assert not updater.due_today()
    assert updater.due_today(datetime.date.today() + datetime.timedelta(days=1))


# ---------------------------------------------------------------- download + verify
def info_for(data, **kw):
    d = dict(version="0.2.0", notes="", url=f"{DL}/v0.2.0/Jarvis-0.2.0.zip", size=len(data),
             sha256=hashlib.sha256(data).hexdigest(), page=f"{PAGE}/v0.2.0")
    d.update(kw)
    return UpdateInfo(**d)


def test_download_ok(tmp_path):
    data = make_zip()
    info = info_for(data)
    folder = updater.download(info, tmp_path / "w", FakeHttp(files={info.url: data},
                              final="https://objects.githubusercontent.com/x"))
    assert (folder / "Jarvis.pyw").exists() and (folder / "jarvis" / "__init__.py").exists()


def test_download_github_source_zip(tmp_path):
    data = make_zip(root="Blink3644-Jarvis-abc123/")
    info = info_for(data, url=f"{updater.API}/zipball/v0.2.0", size=None, sha256=None)
    folder = updater.download(info, tmp_path / "w", FakeHttp(files={info.url: data},
                              final="https://codeload.github.com/x"))
    assert folder.name == "Blink3644-Jarvis-abc123"


@pytest.mark.parametrize("change, match", [
    (dict(size=5), "incomplete"),
    (dict(sha256="0" * 64), "checksum"),
    (dict(version="0.3.0"), "files say version 0.2.0"),      # forgot the version bump
])
def test_download_refuses_bad(tmp_path, change, match):
    data = make_zip()
    info = info_for(data, **change)
    with pytest.raises(UpdateError, match=match):
        updater.download(info, tmp_path / "w", FakeHttp(files={info.url: data}))


def test_download_refuses_bad_host_damaged_and_unsafe(tmp_path):
    data = make_zip()
    info = info_for(data)
    with pytest.raises(UpdateError, match="unexpected website"):
        updater.download(info, tmp_path / "w", FakeHttp(files={info.url: data}, final="https://evil.example/z"))
    junk = b"not a zip at all"
    with pytest.raises(UpdateError, match="damaged"):
        updater.download(info_for(junk), tmp_path / "w", FakeHttp(files={info.url: junk}))
    bad = make_zip(extra={"Jarvis/../../evil.txt": "x"})
    with pytest.raises(UpdateError, match="unsafe"):
        updater.download(info_for(bad), tmp_path / "w", FakeHttp(files={info.url: bad}))
    assert not (tmp_path / "evil.txt").exists()
    other = make_zip(root="Something/").replace(b"Jarvis.pyw", b"Other.pyw!")
    with pytest.raises(UpdateError):
        updater.download(info_for(other), tmp_path / "w", FakeHttp(files={info.url: other}))


# ---------------------------------------------------------------- install hand-off
def test_why_cant_update(tmp_path):
    assert "setup.bat" in updater.why_cant_update(tmp_path)
    (tmp_path / ".venv" / "Scripts").mkdir(parents=True)
    (tmp_path / ".venv" / "Scripts" / "python.exe").write_text("")
    assert "update.ps1" in updater.why_cant_update(tmp_path)
    (tmp_path / "update.ps1").write_text("")
    assert updater.why_cant_update(tmp_path) is None
    (tmp_path / ".git").mkdir()
    assert "git" in updater.why_cant_update(tmp_path)


def test_launch_installer_uses_a_copy_of_installed_script(tmp_path):
    inst = tmp_path / "inst"
    inst.mkdir()
    (inst / "update.ps1").write_text("# installed")
    new = tmp_path / "work" / "files" / "Jarvis"
    new.mkdir(parents=True)
    seen = {}

    def popen(args, **kw):
        seen["args"], seen["kw"] = args, kw
    updater.launch_installer(new, inst, pid=1234, popen=popen)
    script = tmp_path / "work" / "update.ps1"
    assert script.read_text() == "# installed"
    a = seen["args"]
    assert a[a.index("-File") + 1] == str(script)
    assert a[a.index("-Source") + 1] == str(new) and a[a.index("-Dest") + 1] == str(inst)
    assert "-Auto" in a and a[a.index("-WaitPid") + 1] == "1234"
