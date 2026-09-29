"""Tests for source review gate."""


from free_source_fixture import (
    REPO,
)
from free_source_fixture import (
    dbt_manifest as dbt_manifest,  # noqa: PLC0414 - pytest fixture
)
from free_source_fixture import (
    no_ledger as no_ledger,  # noqa: PLC0414 - pytest fixture
)


def test_tenant_material_never_passes_the_learning_gate():
    import subprocess

    def compile_gate(name, relation="ref('{}')"):
        return subprocess.run(
            ["uv", "run", "--project", str(REPO / "dbt"), "dbt", "compile", "--project-dir", str(REPO / "dbt"),
             "--profiles-dir", str(REPO / "dbt/profiles"), "--target", "ci", "--quiet", "--inline",
             f"select * from ({{{{ learning_gate({relation.format(name)}) }}}}) g"],
            capture_output=True, text=True, check=False)

    refused = compile_gate("fixture", "api.Relation.create(schema='tenant_tenant_a_marts', identifier='{}')")
    assert refused.returncode != 0 and "is tenant material" in refused.stdout + refused.stderr
    assert compile_gate("mart_shazam_chart_daily").returncode == 0
    assert compile_gate("shazam_chart_entries", "source('raw', '{}')").returncode == 0
    # Served tenant rows are annotated learn false whatever their sources (mdp_annotate).
    macro = (REPO / "dbt/macros/mdp_annotate.sql").read_text()
    assert "'scope:tenant' in (model.config.tags or [])" in macro
