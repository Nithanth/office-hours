from pathlib import Path


def test_results_errors_are_exempt_from_toast_conversion():
    root = Path(__file__).resolve().parents[2]
    source = (root / "static/js/settings.js").read_text()
    assert "if (el.closest('#search-results-container')) return;" in source


def test_history_guard_recognizes_persistent_and_legacy_errors():
    root = Path(__file__).resolve().parents[2]
    source = (root / "static/js/htmx-url-reset.js").read_text()
    assert "matches('.oh-toast, .error-message, .alert-error')" in source
