import pytest
import requests
from datetime import datetime, timedelta
from main import is_within_past_week, binomial_probability, scrape_player_data, compile_player_data, ServerError


def test_is_within_past_week():
    assert is_within_past_week(
        (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
    )
    assert not is_within_past_week(
        (datetime.now() - timedelta(days=8)).strftime("%Y-%m-%d")
    )


def test_binomial_probability():
    # avg=0.5, pa=13, exp=2.6 → 1 - 0.5**2.6
    assert binomial_probability(10, 5, 3) == pytest.approx(1 - 0.5 ** 2.6)
    # avg=0.5, pa=25, exp=5.0 → 1 - 0.5**5
    assert binomial_probability(20, 10, 5) == pytest.approx(1 - 0.5 ** 5.0)
    # zero at-bats guard
    assert binomial_probability(0, 0, 0) == 0.0


def _make_mock_html(date_str):
    return f"""
    <div id="div_last5">
        <table>
            <tbody>
                <tr>
                    <th data-stat="date_game">{date_str}</th>
                    <td data-stat="AB">3</td>
                    <td data-stat="H">2</td>
                    <td data-stat="BB">1</td>
                    <td data-stat="SO">0</td>
                </tr>
                <tr>
                    <th data-stat="date_game">{date_str}</th>
                    <td data-stat="AB">4</td>
                    <td data-stat="H">1</td>
                    <td data-stat="BB">0</td>
                    <td data-stat="SO">1</td>
                </tr>
                <tr>
                    <th data-stat="date_game">{date_str}</th>
                    <td data-stat="AB">2</td>
                    <td data-stat="H">0</td>
                    <td data-stat="BB">1</td>
                    <td data-stat="SO">2</td>
                </tr>
            </tbody>
        </table>
    </div>
    """


@pytest.fixture
def mock_response(monkeypatch):
    class MockResponse:
        def __init__(self, text, status_code=200):
            self.content = text.encode("utf-8")
            self.status_code = status_code

        def raise_for_status(self):
            pass

    recent_date = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")

    def mock_get(*args, **kwargs):
        return MockResponse(_make_mock_html(recent_date))

    monkeypatch.setattr(requests, "get", mock_get)


def test_scrape_player_data(mock_response):
    result = scrape_player_data("Test Player", "test_url")
    assert result == {
        "Player": "Test Player",
        "At Bats": 9,
        "Hits": 3,
        "Walks": 2,
        "Strikeouts": 3,
    }


def test_scrape_server_rejection_raises(monkeypatch):
    """A 403 or 429 response should raise ServerError."""
    class BlockedResponse:
        status_code = 403
        content = b""
        def raise_for_status(self):
            pass

    monkeypatch.setattr(requests, "get", lambda *a, **kw: BlockedResponse())
    with pytest.raises(ServerError):
        scrape_player_data("Test Player", "test_url")


def test_compile_player_data_stops_on_server_error(monkeypatch):
    """compile_player_data should stop the loop when the server rejects requests."""
    call_count = 0

    def mock_scrape(player, url):
        nonlocal call_count
        call_count += 1
        raise ServerError("HTTP 429")

    monkeypatch.setattr("main.scrape_player_data", mock_scrape)
    monkeypatch.setattr("main.sleep", lambda _: None)

    players = {"A": "a", "B": "b", "C": "c"}
    result = compile_player_data(players, limit=None)

    # Loop should stop after the first ServerError (only one call should be made)
    assert call_count == 1
    assert result == []


def test_compile_player_data_limit(monkeypatch):
    """compile_player_data should respect the limit parameter."""
    recent_date = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")

    class OkResponse:
        status_code = 200
        content = _make_mock_html(recent_date).encode("utf-8")
        def raise_for_status(self):
            pass

    monkeypatch.setattr(requests, "get", lambda *a, **kw: OkResponse())
    monkeypatch.setattr("main.sleep", lambda _: None)

    players = {f"Player{i}": f"p/player{i}.shtml" for i in range(10)}
    result = compile_player_data(players, limit=3)
    assert len(result) == 3
