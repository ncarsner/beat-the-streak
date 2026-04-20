import pytest
import requests
from datetime import datetime, timedelta
from main import is_within_past_week, binomial_probability, scrape_player_data


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


@pytest.fixture
def mock_response(monkeypatch):
    class MockResponse:
        def __init__(self, text):
            self.content = text.encode("utf-8")
            self.status_code = 200

        def raise_for_status(self):
            pass

    def mock_get(*args, **kwargs):
        recent_date = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
        html_content = f"""
        <div id="div_last5">
            <table>
                <tbody>
                    <tr>
                        <th data-stat="date_game">{recent_date}</th>
                        <td data-stat="AB">3</td>
                        <td data-stat="H">2</td>
                        <td data-stat="BB">1</td>
                        <td data-stat="SO">0</td>
                    </tr>
                    <tr>
                        <th data-stat="date_game">{recent_date}</th>
                        <td data-stat="AB">4</td>
                        <td data-stat="H">1</td>
                        <td data-stat="BB">0</td>
                        <td data-stat="SO">1</td>
                    </tr>
                    <tr>
                        <th data-stat="date_game">{recent_date}</th>
                        <td data-stat="AB">2</td>
                        <td data-stat="H">0</td>
                        <td data-stat="BB">1</td>
                        <td data-stat="SO">2</td>
                    </tr>
                </tbody>
            </table>
        </div>
        """
        return MockResponse(html_content)

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
