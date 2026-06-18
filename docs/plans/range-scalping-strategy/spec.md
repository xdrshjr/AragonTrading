# Range Scalping Strategy Design Spec

- **Feature**: `range-scalping-strategy`
- **Planned strategy key**: `range_scalping_strategy`
- **Version**: `v2`
- **Authoring node**: Subtask #0, design only
- **Scope of this document**: implementation plan for a new built-in strategy.
  This node does not modify application source code.

## 璇勫璁板綍

- **P1 / Resolved - helper API was oversized and internally inconsistent**:
  the original `attach_causal_range(...)` signature exceeded the project's
  five-parameter clean-code guideline and also referenced `range_lookback` and
  `boll_width_atr_max` without passing them consistently. The v2 body replaces
  the long positional signature with a typed config object.
- **P1 / Resolved - `touch_atr_mult` was used but not defined**: entry rules
  and execution flags referred to `touch_atr_mult`, but default parameters and
  schema did not define it. The v2 body adds it to `default_params` and exposes
  it as a core tuning parameter.
- **P1 / Resolved - structural stop semantics were too vague for the current
  simulator**: `SimBroker` supports percentage intrabar stops and next-open
  signal fills, not arbitrary structural stop prices. The v2 body now specifies
  that range-boundary stops are explicit close signals filled by the existing
  next-open contract, with `stop_loss_pct` retained only as a disaster fallback;
  exact boundary-price stop orders are out of scope.
- **P1 / Resolved - hidden `target_mode` implied unimplemented behavior**: the
  original draft mentioned optional `"opposite"` targets while planning only a
  midpoint implementation. The v2 body removes `target_mode` from the first
  implementation and defines `range_mid` as the only v2 profit target.
- **P2 / Accepted - non-1m execution is a degraded mode**: the current engine
  accepts one base candle stream per run. Running this strategy on 5m/15m bars
  remains safe and silent where needed, but the intended product behavior is
  documented as `bar="1m"`.
- **P2 / Accepted - parameter panel can still become dense**: the v2 schema
  keeps only execution-critical controls visible and leaves advanced constants
  in defaults.

## Overview

This feature adds a new built-in trading strategy for ultra-short-term range
scalping. The strategy is designed for high-frequency simulated trading where
the active decision cadence is a 1-minute K-line, while the tradable structure is
formed from a 5-minute or 15-minute consolidation range. Once the market forms a
stable sideways band, the strategy sells short near the upper edge of the band
and buys long near the lower edge. Stop-loss levels are placed outside the
range boundary with an ATR buffer, and the v2 take-profit target is the range
center.

The user requirement explicitly asks for trades inside a stable RSI or Bollinger
environment rather than trend-following entries. The strategy therefore has two
layers: a higher-range detector that confirms the market is range-bound on 5m or
15m bars, and a 1m execution detector that waits for boundary touches,
rejection candles, and non-expanding volatility before emitting a signal. This
maps the discretionary idea of "trade the wave segment after it forms" into a
deterministic rule set that can be backtested, compared, and run in paper mode
using the existing `SimBroker`.

The platform remains simulation-only. This strategy does not promise stable
profitability, does not place real orders, and does not introduce fake data. It
uses the same OKX public candle feed, strategy registry, backtest engine, paper
session loop, and frontend generic parameter rendering already used by existing
built-in strategies.

## Technical Design

### Existing Contracts To Preserve

The backend strategy contract is defined in
`services/quant/app/strategies/base.py`. A strategy subclass provides
`meta: StrategyMeta`, implements `prepare(df, bar)`, `warmup()`, and
`on_candle(df, i, ctx)`, and returns `Signal(side, strength, reason)` objects.
Backtests call `prepare()` once and then call `on_candle()` on each candle.
Paper sessions call `prepare()` repeatedly as the rolling candle window grows.
For this reason every derived indicator column must be calculated inside
`prepare()`, while any per-position runtime state must live in the strategy
instance and must not be reset by `prepare()`.

The engine currently provides only one base candle stream per run. To support
"1m decision, 5m/15m wave range", the user should run this strategy on `bar=1m`.
The strategy will causally resample the incoming 1m DataFrame into range buckets
inside `prepare()`. If the user runs the strategy on 5m or 15m directly, the
strategy must still run without errors, but it will treat the base bar as the
execution bar and will be less precise. The strategy description and
`params_schema` must state that 1m is the recommended base period.

### Range Structure Helper

Create `services/quant/app/strategies/_range_structure.py` with pure helper
functions. This keeps range-specific logic separate from
`_trend_structure.py`, whose current API is oriented around EMA trend regimes.
The helper module should contain a small typed configuration object plus pure
functions. The config object keeps function signatures within the project
guideline of no more than five parameters and avoids long positional parameter
lists.

```text
RangeStructureConfig (@dataclass(frozen=True)):
  range_seconds: int
  range_lookback: int
  boll_period: int
  boll_std_mult: float
  boll_width_atr_max: float
  rsi_period: int
  rsi_mid: float
  rsi_band: float
  atr_period: int
  width_atr_max: float
```

The helper module should contain:

1. `resample_closed_buckets(df, bucket_seconds) -> pd.DataFrame`: group rows by
   `ts // bucket_seconds`, aggregate `o` as first, `h` as max, `l` as min, `c`
   as last, `vol` as sum, and `bucket` as the group key. The returned bucket
   frame must be sorted ascending.
2. `attach_causal_range(df, base_seconds, config) -> pd.DataFrame`: compute
   5m/15m Bollinger bands, RSI, ATR, range high/low, range width, and
   `is_range_stable` on the bucket frame, then `shift(1)` every bucket-level
   value before mapping it back to the base rows. The `shift(1)` is mandatory
   because a 1m candle inside the current 5m/15m bucket cannot see that
   bucket's final high, low, close, Bollinger state, or RSI.
3. `attach_execution_flags(df, wick_ratio, touch_atr_mult) -> pd.DataFrame`:
   derive 1m upper/lower rejection features from already attached range
   boundaries. This function may be used by the strategy or implemented as
   private methods if it keeps code smaller.

Stable range definition:

- `range_high` is the rolling max of closed range-bucket highs over
  `range_lookback` buckets.
- `range_low` is the rolling min of closed range-bucket lows over
  `range_lookback` buckets.
- `range_mid = (range_high + range_low) / 2`.
- `range_width = range_high - range_low`.
- `is_range_stable` is true only when all of the following are true:
  `range_width > 0`, `range_width <= width_atr_max * range_atr`,
  `boll_width <= boll_width_atr_max * range_atr`, and
  `abs(range_rsi - rsi_mid) <= rsi_band`.

The helper should return NaN for early rows rather than backfilling incomplete
history. `on_candle()` will use NaN guards and emit no signal until enough
history exists.

### Strategy Class

Create `services/quant/app/strategies/range_scalping_strategy.py` with
`RangeScalpingStrategy(BaseStrategy)`.

Default parameters:

| Param | Default | Meaning |
|---|---:|---|
| `execution_seconds` | `60` | Expected base execution cadence; validation is descriptive only. |
| `range_seconds` | `300` | Higher range bucket, default 5m. User can set 900 for 15m. |
| `range_lookback` | `12` | Number of closed 5m/15m buckets used to form the band. |
| `boll_period` | `20` | Bollinger period on range buckets. |
| `boll_std_mult` | `2.0` | Bollinger standard deviation multiplier. |
| `boll_width_atr_max` | `2.2` | Maximum Bollinger width relative to range ATR. |
| `rsi_period` | `14` | RSI period on range-bucket closes. |
| `rsi_mid` | `50` | RSI center for range-bound state. |
| `rsi_band` | `12` | Acceptable RSI deviation from center. |
| `atr_period` | `14` | ATR period on range buckets. |
| `width_atr_max` | `3.0` | Maximum rolling range width relative to range ATR. |
| `entry_zone_atr_mult` | `0.35` | How close to boundary price must be to trigger. |
| `touch_atr_mult` | `0.10` | Small outside-boundary tolerance for wick touches. |
| `wick_ratio` | `1.5` | Minimum wick-to-body ratio for rejection candles. |
| `stop_atr_mult` | `0.6` | Stop buffer outside the range boundary. |
| `stop_loss_pct` | `0.02` | Hard disaster fallback handled by `SimBroker`. |
| `max_hold_bars` | `30` | Maximum 1m bars in a position. |
| `strength` | `0.7` | Position notional ratio. |

Expose only the most useful parameters in `params_schema`: `range_seconds`,
`range_lookback`, `boll_period`, `boll_std_mult`, `rsi_period`, `rsi_band`,
`entry_zone_atr_mult`, `touch_atr_mult`, `stop_atr_mult`, `max_hold_bars`, and
`strength`. Keep `execution_seconds`, `boll_width_atr_max`, `width_atr_max`,
`rsi_mid`, `wick_ratio`, and `stop_loss_pct` in `default_params` but out of the
panel initially to avoid an overlong UI. The first implementation has exactly
one profit target: `range_mid`. Do not include a hidden `target_mode` until an
opposite-edge target is actually implemented and tested.

`warmup()` should be a pure function of params:

```text
range_bars_needed = max(
  range_lookback,
  boll_period,
  rsi_period,
  atr_period
) + 2
base_bars_per_range = max(1, range_seconds // execution_seconds)
return range_bars_needed * base_bars_per_range + 2
```

`prepare(df, bar)` should copy the DataFrame, resolve `base_seconds` from
`BAR_SECONDS.get(bar, execution_seconds)`, build `RangeStructureConfig` from
normalized params, call the range helper, attach all columns, and return the new
DataFrame. It must not mutate strategy instance state.

`on_candle(df, i, ctx)` behavior:

- If required values at row `i` are NaN, return `[]`.
- If `ctx.position_side == "long"`, emit a close signal when price reaches
  `range_mid`, when the current candle breaches
  `range_low - stop_atr_mult * range_atr`, or when the range is no longer
  stable.
- If `ctx.position_side == "short"`, emit a close signal when price reaches
  `range_mid`, when the current candle breaches
  `range_high + stop_atr_mult * range_atr`, or when the range is no longer
  stable.
- If flat and `is_range_stable` is false, return `[]`.
- Flat short entry: current 1m high or close is inside the upper boundary zone
  `range_high - entry_zone_atr_mult * range_atr <= price <= range_high +
  touch_atr_mult * range_atr`, the candle rejects downward (`upper_wick` is
  meaningfully larger than body), and close is below open or below range high.
- Flat long entry: current 1m low or close is inside the lower boundary zone
  `range_low - touch_atr_mult * range_atr <= price <= range_low +
  entry_zone_atr_mult * range_atr`, the candle rejects upward (`lower_wick` is
  meaningfully larger than body), and close is above open or above range low.

The strategy should rely on `BaseStrategy.risk_params()` for
`max_hold_bars` and the fallback `stop_loss_pct`. It should not use
`take_profit_pct` because the target is structural (`range_mid`) rather than
percentage-based. The primary structural stop is an explicit `close` signal
filled by the existing next-candle-open broker contract. Do not change
`SimBroker` in this feature to support arbitrary price-level stop orders; that
would be a separate simulator contract enhancement.

### Sequence Of Operations

Backtest path:

1. Frontend posts the existing backtest request to
   `POST /api/py/strategies/{id}/backtest` with `bar="1m"`.
2. FastAPI loads real OKX candles and instantiates the registered built-in row.
3. `run_backtest()` calls `RangeScalpingStrategy.prepare(candles_df, "1m")`.
4. `prepare()` creates closed 5m/15m range buckets, shifts bucket indicators by
   one bucket, maps them back to 1m rows, and attaches execution features.
5. `run_backtest()` iterates through rows. `on_candle()` emits long/short/close
   signals only after warmup and only for causally known range data.
6. `SimBroker` fills signals at the next candle open, applies configured
   fallback percentage risk and `max_hold_bars`, and persists trades/equity
   through the existing repository path.

Paper path:

1. Existing paper start endpoints create a `PaperSession` for the strategy.
2. The session primes real OKX history, then processes confirmed candles only.
3. Each confirmed 1m candle recomputes `prepare()` over the rolling window.
4. Because range-bucket columns are shifted by one closed bucket, partial live
   5m/15m buckets never leak future data into 1m decisions.
5. Trade, equity, marker, and signal events flow through the existing WebSocket
   event shape without frontend changes.

## File / Module Change Plan

| File | Action | Intent |
|---|---|---|
| `docs/plans/range-scalping-strategy/spec.md` | Update now | v2 design document for downstream implementation. |
| `services/quant/app/strategies/_range_structure.py` | Create later | Pure causal range-bucket resampling, Bollinger/RSI/ATR range detection, and boundary columns. |
| `services/quant/app/strategies/range_scalping_strategy.py` | Create later | Built-in `RangeScalpingStrategy` implementing upper-bound short and lower-bound long entries. |
| `services/quant/app/strategies/__init__.py` | Modify later | Import the new module and append `range_scalping_strategy` to `BUILTIN_ORDER`. |
| `services/quant/tests/test_range_scalping_strategy.py` | Create later | Unit tests for causal resampling, range detection, long/short entries, exits, warmup, and repeated `prepare()`. |
| `services/quant/tests/test_strategies.py` | Modify later | Assert registry includes `range_scalping_strategy` and all registered strategies backtest successfully. |

No frontend file, API router, DB model, or desktop file is required for the
initial implementation because the existing registry, seed, repository, and
parameter schema flow already expose built-in strategies generically.

## Interface Design

No new REST, WebSocket, or CLI endpoint is introduced.

Existing interfaces used by this strategy:

- `GET /api/strategies`: returns the seeded built-in strategy row with
  `templateKey="range_scalping_strategy"`, default params, and `paramsSchema`.
- `POST /api/strategies/{id}/backtest`: unchanged request body. Recommended
  `bar` is `"1m"`, with `limit` high enough to cover warmup plus test range.
- `POST /api/paper/start` and `POST /api/paper/batch/start`: unchanged request
  bodies. Recommended `bar` is `"1m"`.
- `WS /api/ws/paper/{session_id}`: unchanged event stream. The strategy emits
  standard `signal`, `trade`, and `equity` events.

Parameter schema examples must use existing shape:

```json
{
  "range_seconds": {
    "type": "int",
    "default": 300,
    "min": 300,
    "max": 900,
    "step": 600,
    "label": "Range bucket seconds"
  }
}
```

If the implementation team wants labels localized to Chinese, keep the key names
English and only localize `name`, `description`, and `label` strings, matching
existing strategy conventions.

## Data Model

No database schema change is required. Built-in strategy seeding should reuse
the existing `Strategy` table. On app startup, `db/session.py` will seed the new
template key if `__init__.py` imports it and `BUILTIN_ORDER` contains it. Existing
run, trade, and equity persistence remains unchanged.

In-memory DataFrame columns added by `prepare()`:

| Column | Meaning |
|---|---|
| `range_high` | Upper edge of the most recent stable closed 5m/15m range. |
| `range_low` | Lower edge of the most recent stable closed 5m/15m range. |
| `range_mid` | Midpoint target for both long and short positions. |
| `range_width` | `range_high - range_low`. |
| `range_atr` | ATR computed on range buckets and causally mapped to 1m rows. |
| `range_rsi` | RSI computed on range-bucket closes. |
| `range_boll_upper` | Bollinger upper band on range-bucket closes. |
| `range_boll_ma` | Bollinger middle band on range-bucket closes. |
| `range_boll_lower` | Bollinger lower band on range-bucket closes. |
| `range_boll_width` | `range_boll_upper - range_boll_lower`. |
| `is_range_stable` | Boolean gate for flat entries and continued holding. |
| `upper_rejection` | 1m candle rejects from the upper boundary zone. |
| `lower_rejection` | 1m candle rejects from the lower boundary zone. |

No new `Signal`, `TradeRecord`, or `EquityRecord` fields are needed. Trade
reasons should be concise, for example `Range upper short`, `Range lower long`,
`Target range mid`, `Range stop upper`, and `Range invalidated`.

## Testing & Acceptance Criteria

Backend unit tests in `services/quant/tests/test_range_scalping_strategy.py`:

- `test_resample_closed_buckets_aggregates_ohlc`: verifies 1m rows aggregate
  into exact 5m buckets.
- `test_range_mapping_is_causal`: mutating rows after index `i` must not change
  range columns at or before `i`; the current unclosed bucket must not influence
  its own 1m rows.
- `test_strategy_warmup_is_pure`: `warmup()` returns a positive integer before
  `prepare()` and scales upward when `range_seconds` or indicator windows grow.
- `test_prepare_does_not_mutate_runtime_state`: repeated `prepare()` calls do
  not reset any strategy instance fields.
- `test_short_emits_at_upper_boundary`: synthetic stable range plus 1m upper
  rejection emits a short signal.
- `test_long_emits_at_lower_boundary`: synthetic stable range plus 1m lower
  rejection emits a long signal.
- `test_no_signal_when_rsi_or_bollinger_not_stable`: boundary touch without a
  stable RSI/Bollinger range emits no signal.
- `test_long_closes_at_mid_or_structural_stop`: long position closes at
  `range_mid` or emits a close signal after the candle breaches the lower
  structural stop.
- `test_short_closes_at_mid_or_structural_stop`: short position closes at
  `range_mid` or emits a close signal after the candle breaches the upper
  structural stop.
- `test_default_params_define_all_rule_terms`: all terms referenced by entry
  and exit rules, including `touch_atr_mult`, `wick_ratio`, and
  `width_atr_max`, exist in `default_params`.
- `test_registered_builtin`: registry includes `range_scalping_strategy`.

Existing tests to update:

- Add the new key to `test_registry_has_builtins`.
- Ensure `test_backtest_runs_for_all_strategies` still passes with synthetic
  candles. If synthetic 1H data produces no signals, that is acceptable; the
  assertion should remain about metrics/equity completeness, not trade count.

Acceptance criteria:

- `pytest services/quant/tests/test_range_scalping_strategy.py -q` passes.
- `pytest services/quant/tests/test_strategies.py -q` passes.
- `pytest services/quant -q` passes in the normal offline suite.
- `GET /api/strategies` includes the new built-in after startup.
- Running a 1m backtest over real OKX candles returns a `runId`, metrics,
  equity points, and, when market conditions match the synthetic scenario,
  trade markers with the expected reason strings.
- Starting a 1m paper session does not error during priming and does not emit
  duplicate signals for the same confirmed candle.

## Risks & Mitigations

| Risk | Severity | Mitigation |
|---|---|---|
| Future leakage from 5m/15m bucket data into 1m decisions | P0 | Shift all bucket-level indicators by one closed bucket before mapping them back to base rows; add causal mutation tests. |
| Strategy trades in trends instead of ranges | P1 | Require both RSI near center and Bollinger/range width below ATR-based thresholds before entries. |
| Too few signals because filters are too strict | P2 | Keep thresholds configurable through params; acceptance should validate correct behavior, not a minimum trade count on arbitrary history. |
| Too many signals near boundaries | P1 | Use single-position `SimBroker` behavior, rejection candle requirements, and `max_hold_bars`; do not emit same-direction entries while already positioned. |
| Running on non-1m bars weakens the intended design | P2 | Keep behavior safe and silent where needed; document 1m as recommended in strategy description and labels. |
| Parameter panel becomes too complex | P2 | Expose only core controls in `params_schema`; keep advanced constants in `default_params`. |
| Structural stop differs from engine percentage stop behavior | P2 | Use explicit close signals for range boundary stops and optionally retain `stop_loss_pct` only as disaster fallback. |
| Users interpret the strategy as guaranteed profitable | P1 | Strategy description and docs must state that it is for simulation/backtest validation and does not guarantee profit. |

The implementation should remain narrow: add the strategy, add a pure helper
module if needed, register the built-in, and cover behavior with focused tests.
Do not modify API contracts, frontend state, database schema, OKX adapters, or
the simulator unless a test proves the existing contract cannot support this
strategy.

## 璇勫缁撹

**閫氳繃**. The v2 design is feasible with the current FastAPI, pandas,
strategy registry, `SimBroker`, and generic frontend parameter flow. All P0/P1
review concerns have been resolved in the document body. Remaining P2 items are
documented implementation tradeoffs and do not block downstream development.
