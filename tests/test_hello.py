from src.main import hello


def test_hello_returns_expected_string():
    assert hello() == "hello"
