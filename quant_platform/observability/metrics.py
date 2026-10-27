"""10 Core Prometheus Metrics & Exposition Registry for QuantPlatform (P12)."""

from __future__ import annotations

from prometheus_client import CollectorRegistry, Counter, Gauge, Histogram, generate_latest


class PlatformMetrics:
    """
    Isolated Prometheus metric registry exposing the 10 mandatory telemetry signals:
    1. `quant_orders_submitted_total`
    2. `quant_order_submit_latency_ms`
    3. `quant_fills_recorded_total`
    4. `quant_fill_slippage_bps`
    5. `quant_account_equity_usdt`
    6. `quant_position_notional_usdt`
    7. `quant_reconciliation_discrepancies_total`
    8. `quant_risk_rejections_total`
    9. `quant_ws_connection_healthy`
    10. `quant_exchange_clock_skew_ms`
    """

    def __init__(self, registry: CollectorRegistry | None = None) -> None:
        self.registry = registry or CollectorRegistry()

        self.orders_submitted_total = Counter(
            "quant_orders_submitted_total",
            "Total orders submitted through unified execution pipeline",
            labelnames=("env", "exchange", "symbol", "side", "status"),
            registry=self.registry,
        )
        self.order_submit_latency_ms = Histogram(
            "quant_order_submit_latency_ms",
            "Order submission round-trip latency in milliseconds",
            labelnames=("env", "exchange"),
            buckets=(1.0, 5.0, 10.0, 25.0, 50.0, 100.0, 250.0, 500.0, 1000.0, 2500.0),
            registry=self.registry,
        )
        self.fills_recorded_total = Counter(
            "quant_fills_recorded_total",
            "Total execution fills persisted to unified ledger",
            labelnames=("env", "exchange", "liquidity"),
            registry=self.registry,
        )
        self.fill_slippage_bps = Histogram(
            "quant_fill_slippage_bps",
            "Realized execution slippage vs reference price in basis points",
            labelnames=("env", "exchange", "symbol"),
            buckets=(-20.0, -5.0, 0.0, 1.0, 2.5, 5.0, 10.0, 25.0, 50.0, 100.0),
            registry=self.registry,
        )
        self.account_equity_usdt = Gauge(
            "quant_account_equity_usdt",
            "Current account net equity in USDT equivalent",
            labelnames=("env", "run_id", "exchange"),
            registry=self.registry,
        )
        self.position_notional_usdt = Gauge(
            "quant_position_notional_usdt",
            "Absolute open position notional in USDT",
            labelnames=("env", "exchange", "symbol"),
            registry=self.registry,
        )
        self.reconciliation_discrepancies_total = Counter(
            "quant_reconciliation_discrepancies_total",
            "Total discrepancies detected across 3-tier reconciliation",
            labelnames=("tier", "severity"),
            registry=self.registry,
        )
        self.risk_rejections_total = Counter(
            "quant_risk_rejections_total",
            "Total orders blocked by pre-trade LiveRiskEngine rules",
            labelnames=("env", "rule_code"),
            registry=self.registry,
        )
        self.ws_connection_healthy = Gauge(
            "quant_ws_connection_healthy",
            "WebSocket stream health status (1 = healthy, 0 = stale/disconnected)",
            labelnames=("exchange", "channel"),
            registry=self.registry,
        )
        self.exchange_clock_skew_ms = Gauge(
            "quant_exchange_clock_skew_ms",
            "Measured clock offset vs exchange server in milliseconds",
            labelnames=("exchange",),
            registry=self.registry,
        )

    def render_prometheus(self) -> bytes:
        return generate_latest(self.registry)
