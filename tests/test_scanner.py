"""Unit tests for Poliora's consent-safe availability scanner."""

from poliora.cost import scan_system_ai_environment


def test_system_ai_scanner_returns_valid_report(tmp_path):
    report = scan_system_ai_environment(tmp_path)
    assert report.total_active_tools >= 0
    assert report.measured_usage_available is False
    assert len(report.scanned_tools) >= 4
    assert "measure" in report.phase_recommendations
    assert "prove" in report.phase_recommendations

    report_dict = report.to_dict()
    assert "scanned_tools" in report_dict
    assert "next_action" in report_dict
    assert "potential_monthly_savings_usd" not in report_dict
