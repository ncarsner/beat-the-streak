import pytest
import requests
from datetime import datetime, timedelta
from main import is_within_past_week, binomial_probability, scrape_player_data

# Sample headers to include in the requests
headers = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/58.0.3029.110 Safari/537.3"
}


def test_is_within_past_week():
    assert is_within_past_week(
        (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
    )
    assert not is_within_past_week(
        (datetime.now() - timedelta(days=8)).strftime("%Y-%m-%d")
    )


def test_binomial_probability():
    assert binomial_probability(10, 5, 3) == pytest.approx(0.03125)
    assert binomial_probability(20, 10, 5) == pytest.approx(0.0009765625)


@pytest.fixture
def mock_response(monkeypatch):
    class MockResponse:
        def __init__(self, text):
            self.content = text.encode("utf-8")

    def mock_get(*args, **kwargs):
        html_content = """
        <div id="div_last5">
            <table>
                <tbody>
                    <tr data-row="4">
                        <th data-stat="date" scope="row" class="left">2024-08-01</th>
                    </tr>
                    <tr><td></td><td></td><td></td><td></td><td></td><td>3</td><td></td><td>2</td><td></td><td></td><td></td><td>1</td></tr>
                    <tr><td></td><td></td><td></td><td></td><td></td><td>4</td><td></td><td>1</td><td></td><td></td><td></td><td>2</td></tr>
                    <tr><td></td><td></td><td></td><td></td><td></td><td>2</td><td></td><td>3</td><td></td><td></td><td></td><td>3</td></tr>
                    <tr><td></td><td></td><td></td><td></td><td></td><td>5</td><td></td><td>4</td><td></td><td></td><td></td><td>2</td></tr>
                    <tr><td></td><td></td><td></td><td></td><td></td><td>1</td><td></td><td>5</td><td></td><td></td><td></td><td>1</td></tr>
                </tbody>
            </table>
        </div>
        """
        return MockResponse(html_content)

    monkeypatch.setattr(requests, "get", mock_get)


def test_scrape_player_data(mock_response):
    result = scrape_player_data("Test Player", "test_url", headers=headers)
    assert result == {"Player": "Test Player", "At Bats": 15, "Hits": 15, "Walks": 9}
