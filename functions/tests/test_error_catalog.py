"""Recovery guidance survives unknown errors and packaged service responses."""

from mdp_functions.errors import LeaseLost, ServiceError, error_catalog


def test_service_errors_have_recovery_guidance():
    for code, hint in error_catalog().items():
        error = ServiceError(code, "Exact cause")
        assert error.next_step == hint["next_step"]
        assert error.runbook == (
            f"/runbooks/{hint['runbook']}" if hint["runbook"] else None
        )
        assert str(error) == "Exact cause"
    assert (
        ServiceError("future_code", "Cause").next_step
        == error_catalog()["unmapped"]["next_step"]
    )
    assert LeaseLost().runbook == "/runbooks/dump-late"


def test_catalog_runbook_links_are_available_after_setup():
    from mdp_functions.runbooks import seed_runbooks

    slugs = set()

    class SeedConnection:
        def execute(self, query, params):
            slugs.add(params[0])

    seed_runbooks(SeedConnection())
    linked = {hint["runbook"] for hint in error_catalog().values() if hint["runbook"]}
    assert linked <= slugs, "Register the runbook during setup or use an explicit null catalog runbook"
