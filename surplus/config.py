"""Configuration: where data lives, which model runs, and the operator profile.

Everything is overridable by environment variable so the CLI works with no
config file at all. The operator profile (your business details) is stored in
``$SURPLUS_HOME/config.json`` and merged into every generated document.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field, asdict
from pathlib import Path

DEFAULT_MODEL = "claude-opus-5"
DEFAULT_EFFORT = "high"


def home_dir() -> Path:
    return Path(os.environ.get("SURPLUS_HOME", Path.home() / ".surplus")).expanduser()


@dataclass
class OperatorProfile:
    """Who is doing the recovery work. Rendered into letters and agreements."""

    business_name: str = "[YOUR BUSINESS NAME]"
    signer_name: str = "[YOUR NAME]"
    signer_title: str = "Principal"
    address: str = "[STREET, CITY, STATE ZIP]"
    phone: str = "[PHONE]"
    email: str = "[EMAIL]"
    website: str = ""
    # Fee you actually charge per state, as a fraction. Must not exceed the
    # statutory cap in surplus/statutes/<state>.json; the compliance gate enforces it.
    fee_policy: dict[str, float] = field(
        default_factory=lambda: {"FL": 0.12, "TX": 0.0, "GA": 0.10}
    )
    # Texas: what fraction of the claim you pay at assignment (min 0.80 by statute).
    tx_purchase_fraction: float = 0.80
    # Optional licensed attorney partner (needed to charge any fee in Texas,
    # and helpful in Georgia counties that only accept attorney filings).
    attorney_name: str = ""
    attorney_bar_state: str = ""
    attorney_firm: str = ""
    attorney_email: str = ""
    # Florida ch. 717 claimant's representative registration (needed once funds
    # have been remitted to the state as unclaimed property).
    fl_dfs_registration: str = ""


@dataclass
class Settings:
    home: Path
    model: str
    effort: str
    operator: OperatorProfile

    @property
    def db_path(self) -> Path:
        return self.home / "surplus.sqlite3"

    @property
    def documents_dir(self) -> Path:
        return self.home / "documents"

    @property
    def packets_dir(self) -> Path:
        return self.home / "packets"

    @property
    def config_path(self) -> Path:
        return self.home / "config.json"


def load_settings() -> Settings:
    home = home_dir()
    home.mkdir(parents=True, exist_ok=True)
    cfg_path = home / "config.json"
    operator = OperatorProfile()
    if cfg_path.exists():
        try:
            raw = json.loads(cfg_path.read_text())
            known = {k: v for k, v in raw.items() if k in OperatorProfile.__dataclass_fields__}
            operator = OperatorProfile(**known)
        except (json.JSONDecodeError, TypeError) as exc:
            raise SystemExit(f"Could not parse {cfg_path}: {exc}") from exc
    return Settings(
        home=home,
        model=os.environ.get("SURPLUS_MODEL", DEFAULT_MODEL),
        effort=os.environ.get("SURPLUS_EFFORT", DEFAULT_EFFORT),
        operator=operator,
    )


def write_default_config(settings: Settings, overwrite: bool = False) -> Path:
    path = settings.config_path
    if path.exists() and not overwrite:
        return path
    path.write_text(json.dumps(asdict(settings.operator), indent=2) + "\n")
    return path
