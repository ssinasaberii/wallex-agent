"""Typed configuration (pydantic). Unknown keys are errors, so a typo cannot silently fall back
to a default. `config_hash` is the sha256 of the canonical JSON dump and goes into every log record."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

from wallex_agent import secrets

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG = ROOT / "config" / "default.yaml"


class _M(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Settlement(_M):
    stop_entries_min: int
    start_selling_min: int
    final_check_min: int


class Challenge(_M):
    end_utc: datetime
    initial_equity_tmn: float
    settlement: Settlement


class Data(_M):
    base_url: str
    trigger_resolution: str
    regime_hours: int
    udf_chunk_bars: int = Field(le=600)
    history_days: int
    min_regime_bars: int
    min_trigger_bars: int
    quote_asset: str
    btc_regime_symbol: str
    btc_tmn_symbol: str
    usdt_tmn_symbol: str
    fetch_top_n: int
    signal_price: Literal["usdt_adjusted", "tmn"] = "tmn"
    trigger_hours: int = 1   # entry/trigger timeframe (1, 4, 24); regime timeframe is regime_hours


class Universe(_M):
    top_n_by_volume: int
    max_spread_pct: float
    exclude_categories: list[int]
    exclude_stablecoins: list[str]
    exclude_non_crypto: list[str]
    data_quality_window_h: int
    max_missing_pct: float
    spread_estimate_source: str


class Indicators(_M):
    ema_fast: int
    ema_slow: int
    ema_slope_lookback: int
    atr_period: int
    adx_period: int
    rsi_period: int
    bb_period: int
    bb_std: float
    donchian_period: int
    relvol_period: int
    atr_pct_rank_window: int
    ad_slope_bars: int


class BtcFilter(_M):
    enabled: bool
    high_vol_size_mult: float


class Regime(_M):
    high_vol_atr_pct_rank: float
    trend_adx_min: float
    range_adx_max: float
    hysteresis_bars: int
    hysteresis_enabled: bool


class T1(_M):
    enabled: bool
    lookback_bars: int
    touch_atr_mult: float
    rsi_min: float
    rsi_max: float
    target_r: float
    rr_min: float
    h_max: int


class T2(_M):
    enabled: bool
    relvol_min: float
    breakout_ad_confirm: bool
    target_r: float
    rr_min: float
    h_max: int


class T3(_M):
    enabled: bool
    rsi_max: float
    rr_min: float
    h_max: int


class Gray(_M):
    enabled: bool
    size_mult: float


class Setups(_M):
    T1_pullback: T1
    T2_breakout: T2
    T3_meanrev: T3
    gray_zone: Gray
    priority: list[Literal["T1_pullback", "T2_breakout", "T3_meanrev"]]


class Stops(_M):
    k_atr: float
    min_stop_pct: float
    max_stop_pct: float


class FeeGate(_M):
    enabled: bool
    safety_buffer_pct: float
    min_net_edge_pct: float
    c_move: float
    horizons_h: list[int]
    max_cost_to_move: float


class Fees(_M):
    assumed: bool
    source: str
    snapshot_id: str
    maker_pct: float
    taker_pct: float
    sensitivity_per_side_pct: list[float]


class Slippage(_M):
    assumed: bool
    tier1_top_n: int
    tier1_per_side_pct: float
    tier2_per_side_pct: float


class Ranking(_M):
    max_candidates_per_cycle: int
    w_rs_vs_btc: float
    w_adx: float
    w_relvol: float


class Risk(_M):
    equity_cap_tmn: float
    risk_per_trade_pct: float
    max_open_risk_pct: float
    max_position_pct: float
    max_slots: int
    min_buy_tmn: float
    max_daily_loss_pct: float
    kill_switch_drawdown_pct: float
    # When a tripped kill switch re-arms and the drawdown peak restarts (UTC calendar boundaries).
    # "never" = the original rule (stopped until a person decides); others are experiments for long operation.
    kill_switch_reset: Literal["never", "daily", "weekly", "monthly"] = "never"


class Exits(_M):
    breakeven_at_r: float
    trail_atr_mult: float
    trail_t1_after_r: float
    time_exit_t3_bars: int
    time_exit_t12_max_bars: int
    stop_check_seconds: int
    thesis_exit_orderflow: Literal["log_only", "off", "on"]
    # Staged selling: [[at_R, fraction_of_initial_qty], ...]; after the first partial sale the stop can move to
    # break-even. Reducing a position is allowed (Article 4 forbids only adding).
    scale_out: list[list[float]] = Field(default_factory=list)
    scale_out_breakeven: bool = True
    t1_fixed_target: bool = True        # False: T1 has no fixed 2R exit and trails like T2
    # Staged buying (adding to a winner). FORBIDDEN by Constitution Article 4 / ADR-004: backtest experiments only.
    pyramid_enabled: bool = False
    pyramid_at_r: float = 1.0
    pyramid_add_fraction: float = 0.5

    @model_validator(mode="after")
    def _checks(self) -> Exits:
        if sum(f for _, f in self.scale_out) > 1.0 + 1e-9:
            raise ValueError("scale_out fractions add up to more than the whole position")
        return self


class OrderFlow(_M):
    obi_mode: Literal["confirm", "log_only", "off"]
    tfi_mode: Literal["confirm", "log_only", "off"]
    obi_band_pct: float
    w_obi_frac_of_h: float
    w_obi_min_min: float
    w_obi_max_min: float
    w_obi_min_updates: int
    w_tfi_min_trades: int
    w_tfi_max_frac_of_h: float
    min_tfi_value_tmn: float
    reject_below: float
    thesis_exit_below: float


class News(_M):
    enabled: bool
    poll_seconds: int
    block_asset_hours: float
    block_all_hours: float
    max_age_hours: float
    max_block_tier: int = 2
    min_choice_prob: float
    systemic_min_prob: float = 0.6
    context_hours: float = 24
    max_feed_silence_min: float = 45
    fng_url: str = "https://api.alternative.me/fng/"


class Macro(_M):
    caution_before_min: int
    block_before_min: int
    block_after_min: int
    spread_recovery_mult: float


class LayaThresholds(_M):
    insufficient: float
    conflict: float
    news: float
    gray: float
    approve: float
    min: float


class LayaCfg(_M):
    url_env: str
    checkpoint: Literal["english"]
    max_state_tokens: int = Field(le=320)
    thresholds: LayaThresholds


class LlmPrice(_M):
    input: float
    output: float
    assumed: bool


class LlmCfg(_M):
    model: str
    allowed_models: list[str]
    max_spend_usd: float = Field(le=3.0)
    max_calls: int
    price_per_1m_usd: LlmPrice

    @model_validator(mode="after")
    def _cheap_only(self) -> LlmCfg:
        if self.model not in self.allowed_models:
            raise ValueError(f"LLM model {self.model!r} not in allowed_models (team rule: no expensive models)")
        return self


class Judge(_M):
    provider: Literal["rules_baseline", "laya", "llm"]
    timeout_s: float
    candidate_ttl_s: float
    baseline_gray_policy: Literal["reject", "approve"]
    laya: LayaCfg
    llm: LlmCfg


class Split(_M):
    train: float
    validation: float
    test: float


class Backtest(_M):
    seed: int
    split: Split
    episode_hours: int
    episode_start_hours_utc: list[int]
    random_entry_runs: int


class Shadow(_M):
    candle_poll_delay_s: int
    depth_poll_s: int
    trades_poll_s: int
    record_depth_levels: int
    virtual_end_utc: datetime | None


class Config(_M):
    strategy_version: str
    challenge: Challenge
    data: Data
    universe: Universe
    indicators: Indicators
    btc_filter: BtcFilter
    regime: Regime
    setups: Setups
    stops: Stops
    fee_gate: FeeGate
    fees: Fees
    slippage: Slippage
    ranking: Ranking
    risk: Risk
    exits: Exits
    order_flow: OrderFlow
    news: News
    macro: Macro
    judge: Judge
    backtest: Backtest
    shadow: Shadow

    @property
    def config_hash(self) -> str:
        canon = json.dumps(self.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(canon.encode()).hexdigest()[:16]


def load_config(path: str | Path = DEFAULT_CONFIG, apply_env: bool = True) -> Config:
    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    if apply_env:
        end = secrets.get("CHALLENGE_END_UTC")
        if end:  # Q-15: .env overrides config when set
            raw["challenge"]["end_utc"] = end
    return Config.model_validate(raw)
