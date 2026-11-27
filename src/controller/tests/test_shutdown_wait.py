from src.controller.shutdown_wait import DOWN_AFTER, wait_for_modules_down


class _Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t

    def sleep(self, s):
        self.t += s


def test_waits_until_every_module_stops_answering():
    clock = _Clock()
    up_until = {"10.0.0.2": 5.0, "10.0.0.3": 12.0}
    reports = []
    down = wait_for_modules_down(
        [{"id": "a", "name": "A", "ip": "10.0.0.2"}, {"id": "b", "ip": "10.0.0.3"}],
        timeout_s=60, on_change=reports.append,
        ping_fn=lambda ip: clock.t < up_until[ip], sleep=clock.sleep, clock=clock)
    assert down == {"a": True, "b": True}
    assert [[m["down"] for m in r] for r in reports] == [[True, False], [True, True]]
    assert reports[0][1]["name"] == "b"
    assert 12 <= clock.t < 12 + DOWN_AFTER + 2


def test_one_dropped_ping_is_not_a_halt():
    clock = _Clock()
    answers = iter([False, True] * 100)
    down = wait_for_modules_down([{"id": "a", "ip": "x"}], timeout_s=10,
                                 ping_fn=lambda ip: next(answers),
                                 sleep=clock.sleep, clock=clock)
    assert down == {"a": False}   # never DOWN_AFTER misses in a row: timed out


def test_no_ip_counts_as_down_and_no_modules_returns_at_once():
    clock = _Clock()
    assert wait_for_modules_down([{"id": "a", "ip": None}], ping_fn=lambda ip: True,
                                 sleep=clock.sleep, clock=clock) == {"a": True}
    assert wait_for_modules_down([], sleep=clock.sleep, clock=clock) == {}
