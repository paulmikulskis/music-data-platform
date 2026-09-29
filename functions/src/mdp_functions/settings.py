"""Environment configuration; compatible with secret_store run without a second secret path."""

from decimal import Decimal
from pathlib import Path

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

PACKAGE = Path(__file__).resolve().parent
FUNCTIONS = PACKAGE.parents[1]
REPO = FUNCTIONS.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="MDP_", extra="ignore")
    control_url: str = ""
    warehouse_url: str = ""
    service_read_url: str = ""
    service_token: str = ""
    control_rt_url: str = ""
    control_api_url: str = ""
    control_api_key: str = ""
    litellm_base_url: str = ""
    litellm_admin_key: str = ""
    litellm_keys: dict[str, str] = Field(default_factory=dict)
    typesafe_api_key: SecretStr = Field(SecretStr(""), validation_alias="TYPESAFE_API_KEY", repr=False)
    jev_cassette: str = ""
    workbench_url: str = "http://127.0.0.1:8085"
    workbench_wh_url: str = ""
    workbench_admin_url: str = ""
    reader_url: str = ""
    workbench_row_cap: int = 100
    workbench_timeout_s: int = 30
    workbench_schema_cap_bytes: int = 104857600
    dump_root: str = f"file://{FUNCTIONS / '.dumps'}"
    dev_db: str = str(FUNCTIONS / "dev.duckdb")
    schema_root: Path = FUNCTIONS / "schemas"
    dbt_cloud_verify: bool = True
    image_digest: str = "local"
    trace_url_template: str = "local://trace/{trace_id}"
    lease_s: float = 30
    load_timeout_s: float = 60
    fixture: bool = False
    fixture_scenario: str = Field("normal", pattern=r"^[a-zA-Z0-9_-]+$")
    host_block_s: float = 3600
    # The warehouse pgdata volume and the share of it a landing may take use to.
    pgdata_volume_bytes: int = 20 * 10**9
    pgdata_ceiling: float = 0.6
    webshare_api_key: SecretStr = Field(
        SecretStr(""), validation_alias="WEBSHARE_API_KEY", repr=False
    )
    webshare_microcents_per_byte: Decimal = Field(Decimal(0), ge=0)
    http_backoff_s: float = 0.25
    vendor_estimates: dict[str, int] = Field(default_factory=dict)
    r2_endpoint: str = Field("", validation_alias="R2_ENDPOINT")
    r2_account_id: str = Field("", validation_alias="R2_ACCOUNT_ID")
    r2_bucket: str = Field("", validation_alias="R2_BUCKET")
    r2_access_key_id: str = Field("", validation_alias="R2_ACCESS_KEY_ID")
    r2_secret_access_key: str = Field("", validation_alias="R2_SECRET_ACCESS_KEY")
    otlp_endpoint: str = Field("", validation_alias="OTLP_ENDPOINT")
    otlp_headers: str = Field("", validation_alias="OTLP_HEADERS")
    dbt_cloud_host: str = Field(
        "https://cloud.getdbt.com", validation_alias="DBT_CLOUD_HOST"
    )
    dbt_cloud_account_id: str = Field("", validation_alias="DBT_CLOUD_ACCOUNT_ID")
    dbt_cloud_token: str = Field("", validation_alias="DBT_CLOUD_TOKEN")

    def local(self) -> "Settings":
        missing = [
            name
            for name in ("control_url", "warehouse_url", "service_read_url")
            if not getattr(self, name)
        ]
        if missing:
            raise ValueError(
                "Explicit environment configuration required: "
                + ", ".join("MDP_" + name.upper() for name in missing)
            )
        return self.model_copy(update={"dbt_cloud_verify": False})
