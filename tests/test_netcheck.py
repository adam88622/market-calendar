from core.netcheck import is_online


class Resp:
    def close(self):
        pass


class Sess:
    def __init__(self, ok_urls=(), head_fail=False):
        self.ok, self.head_fail, self.calls = set(ok_urls), head_fail, []

    def _do(self, m, url, **kw):
        self.calls.append((m, url, kw["timeout"]))
        if url in self.ok and not (m == "head" and self.head_fail):
            return Resp()
        raise OSError("down")

    def head(self, url, **kw):
        return self._do("head", url, **kw)

    def get(self, url, **kw):
        return self._do("get", url, **kw)


def test_all_fail():
    s = Sess()
    assert is_online(("http://a", "http://b"), 2.0, s) is False
    assert all(c[2] == 2.0 for c in s.calls)


def test_any_success():
    assert is_online(("http://a", "http://b"), session=Sess({"http://b"})) is True


def test_get_fallback():
    assert is_online(("http://a",), session=Sess({"http://a"}, head_fail=True)) is True
