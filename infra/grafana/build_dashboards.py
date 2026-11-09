#!/usr/bin/env python3
"""Generate the Grafana dashboards as JSON in infra/grafana/dashboards/.

    python3 infra/grafana/build_dashboards.py

Dashboards are generated rather than hand-edited so the five share one set of helpers, units and
per-entity colours (the same categorical slots the web UI uses: a guard layer or a provider has one
colour everywhere). Commit the generated JSON; Grafana provisions it read-only.
"""

import json
from pathlib import Path
from typing import Any

OUT = Path(__file__).parent / "dashboards"
DS = {"type": "prometheus", "uid": "gateway-prom"}

# Reference palette (web/ and dataviz skill): fixed slot order, never cycled.
SLOTS = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#6250d6", "#e34948"]
ENTITY = {
    # guard layers
    "regex": SLOTS[0], "jev": SLOTS[1], "classifier": SLOTS[2], "ensemble": SLOTS[3],
    "documents": SLOTS[4], "output": SLOTS[5], "action_integrity": SLOTS[6],
    # cache layers
    "l1": SLOTS[0], "l2": SLOTS[1], "semantic": SLOTS[2],
    # answer source / cost kind / tier
    "llm": SLOTS[0], "cache": SLOTS[2], "actual": SLOTS[0], "shadow": SLOTS[1],
    "cheap": SLOTS[0], "expensive": SLOTS[1],
}


class Board:
    def __init__(self, uid: str, title: str, description: str) -> None:
        self.uid, self.title, self.description = uid, title, description
        self.panels: list[dict[str, Any]] = []
        self.y = 0
        self.x = 0

    def _place(self, w: int, h: int) -> dict[str, int]:
        if self.x + w > 24:
            self.x, self.y = 0, self.y + h
        pos = {"x": self.x, "y": self.y, "w": w, "h": h}
        self.x += w
        return pos

    def row(self, title: str) -> None:
        self.x, self.y = 0, self.y + 1
        self.panels.append({"type": "row", "title": title, "collapsed": False,
                            "gridPos": {"x": 0, "y": self.y, "w": 24, "h": 1}, "id": len(self.panels) + 1})
        self.y += 1

    def panel(self, kind: str, title: str, targets: list[tuple[str, str]], unit: str = "short",
              w: int = 8, h: int = 8, description: str = "", stack: bool = False,
              extra: dict[str, Any] | None = None) -> None:
        overrides = [
            {"matcher": {"id": "byName", "options": name},
             "properties": [{"id": "color", "value": {"mode": "fixed", "fixedColor": color}}]}
            for name, color in ENTITY.items()
        ]
        custom: dict[str, Any] = {"lineWidth": 2, "fillOpacity": 10 if not stack else 60,
                                  "showPoints": "never", "spanNulls": True}
        if stack:
            custom["stacking"] = {"mode": "normal", "group": "A"}
        p: dict[str, Any] = {
            "id": len(self.panels) + 1, "type": kind, "title": title, "description": description,
            "datasource": DS, "gridPos": self._place(w, h),
            "targets": [{"datasource": DS, "expr": expr, "legendFormat": legend, "refId": chr(65 + i)}
                        for i, (expr, legend) in enumerate(targets)],
            "fieldConfig": {"defaults": {"unit": unit, "color": {"mode": "palette-classic"},
                                         "custom": custom if kind == "timeseries" else {}},
                            "overrides": overrides},
            "options": {"legend": {"displayMode": "list", "placement": "bottom", "showLegend": True},
                        "tooltip": {"mode": "multi", "sort": "desc"}},
        }
        if extra:
            p.update(extra)
        self.panels.append(p)

    def stat(self, title: str, expr: str, unit: str, description: str = "", w: int = 4) -> None:
        self.panel("stat", title, [(expr, "")], unit, w=w, h=4, description=description,
                   extra={"options": {"reduceOptions": {"calcs": ["lastNotNull"]}, "graphMode": "area",
                                      "colorMode": "none", "textMode": "value"}})

    def save(self) -> None:
        board = {
            "uid": self.uid, "title": self.title, "description": self.description,
            "tags": ["procurement-copilot"], "timezone": "browser", "schemaVersion": 41,
            "time": {"from": "now-6h", "to": "now"}, "refresh": "30s", "editable": False,
            "panels": self.panels, "templating": {"list": []}, "annotations": {"list": []},
        }
        OUT.mkdir(parents=True, exist_ok=True)
        (OUT / f"{self.uid}.json").write_text(json.dumps(board, indent=2) + "\n")


R = "[$__rate_interval]"


def overview() -> None:
    b = Board("pc-overview", "Procurement Copilot · Overview", "Traffic, latency, outcomes, cache and guard at a glance.")
    b.stat("Chat requests / min", f"sum(rate(chat_requests_total{R})) * 60", "short")
    b.stat("p95 chat latency", f'histogram_quantile(0.95, sum by (le) (rate(http_request_duration_seconds_bucket{{route="/v1/chat"}}{R})))', "s")
    b.stat("Answers from cache", f'sum(rate(answers_total{{source="cache"}}{R})) / sum(rate(answers_total{R}))', "percentunit")
    b.stat("Guard blocks / h", f"sum(increase(guard_blocks_total[1h]))", "short")
    b.stat("Jev degraded", "max(jev_degraded)", "short", "1 = router/gate in degraded mode")
    b.stat("No-answer rate", f'sum(rate(abstentions_total{{reason=~"NoMatch|OutOfScope|ModelAbstained"}}{R})) / sum(rate(chat_requests_total{R}))', "percentunit")
    b.row("Traffic and latency")
    b.panel("timeseries", "Chat latency p50 / p95", [
        (f'histogram_quantile(0.5, sum by (le) (rate(http_request_duration_seconds_bucket{{route="/v1/chat"}}{R})))', "p50"),
        (f'histogram_quantile(0.95, sum by (le) (rate(http_request_duration_seconds_bucket{{route="/v1/chat"}}{R})))', "p95"),
    ], "s", w=12)
    b.panel("timeseries", "Answers by source", [(f"sum by (source) (rate(answers_total{R}))", "{{source}}")], "reqps", w=12, stack=True)
    b.panel("timeseries", "No-answer by reason", [(f"sum by (reason) (rate(abstentions_total{R}))", "{{reason}}")], "reqps", w=12, stack=True)
    b.panel("timeseries", "Router decisions", [(f"sum by (intent, tier) (rate(route_decisions_total{R}))", "{{intent}} / {{tier}}")], "reqps", w=12)
    b.save()


def llm_cost() -> None:
    b = Board("pc-llm-cost", "Procurement Copilot · LLM & Cost", "Providers, fallbacks, tokens and money (actual vs no-cache/no-router shadow).")
    b.panel("timeseries", "LLM requests by provider and result", [(f"sum by (provider, result) (rate(llm_requests_total{R}))", "{{provider}} · {{result}}")], "reqps", w=12)
    b.panel("timeseries", "Fallbacks", [(f"sum by (from, to, reason) (rate(llm_fallback_total{R}))", "{{from}} → {{to}} ({{reason}})")], "reqps", w=12)
    b.panel("timeseries", "Tokens by direction", [(f"sum by (dir) (rate(llm_tokens_total{R}))", "{{dir}}")], "short", w=12)
    b.panel("timeseries", "Cost per hour (USD)", [
        ('sum(rate(llm_cost_usd_micros_total{kind="actual"}[1h])) * 3600 / 1e6 + sum(rate(jev_cost_usd_micros_total[1h])) * 3600 / 1e6', "actual"),
        ('sum(rate(llm_cost_usd_micros_total{kind="shadow"}[1h])) * 3600 / 1e6', "shadow"),
    ], "currencyUSD", w=12, description="Shadow: what the LLM answers would cost at the expensive tier (no router).")
    b.panel("timeseries", "Jev requests by kind and result", [(f"sum by (kind, result) (rate(jev_requests_total{R}))", "{{kind}} · {{result}}")], "reqps", w=12)
    b.stat("Jev spend (USD, total)", "max(jev_budget_spent_usd)", "currencyUSD", w=6)
    b.save()


def retrieval_gate() -> None:
    b = Board("pc-retrieval", "Procurement Copilot · Retrieval, Gate & Cache", "Per-stage latency, what the Jev gate keeps, cache hit rates.")
    b.panel("timeseries", "Stage latency p95", [(f"histogram_quantile(0.95, sum by (le, stage) (rate(stage_duration_seconds_bucket{R})))", "{{stage}}")], "s", w=24, h=9)
    b.panel("bargauge", "Chunks kept by the gate per query (last 1h)", [("sum by (le) (increase(gate_kept_chunks_bucket[1h]))", "≤ {{le}}")], "short", w=12,
            extra={"options": {"orientation": "horizontal", "displayMode": "basic", "reduceOptions": {"calcs": ["lastNotNull"]}}},
            description="Cumulative buckets: each bar counts queries that kept at most N chunks.")
    b.panel("bargauge", "Gate relevance scores (last 1h)", [("sum by (le) (increase(gate_relevance_bucket[1h]))", "≤ {{le}}")], "short", w=12,
            extra={"options": {"orientation": "horizontal", "displayMode": "basic", "reduceOptions": {"calcs": ["lastNotNull"]}}})
    b.panel("timeseries", "Answer-cache hits by layer", [(f'sum by (layer) (rate(cache_requests_total{{cache="answer", result="hit"}}{R}))', "{{layer}}")], "reqps", w=12, stack=True)
    # Every lookup records exactly one L1 entry, so L1 lookups = lookups; hits come from any layer.
    b.panel("timeseries", "Hit rate by cache", [(f'sum by (cache) (rate(cache_requests_total{{result="hit"}}{R})) / sum by (cache) (rate(cache_requests_total{{layer="l1"}}{R}))', "{{cache}}")], "percentunit", w=12)
    b.save()


def security() -> None:
    b = Board("pc-security", "Procurement Copilot · Security", "Guard blocks, silent failures, throttling and refused actions.")
    b.panel("timeseries", "Guard blocks by layer", [(f"sum by (layer) (rate(guard_blocks_total{R}))", "{{layer}}")], "reqps", w=12, stack=True)
    b.panel("timeseries", "Silent templates served (blocked / locked)", [(f'sum by (reason) (rate(abstentions_total{{reason=~"Blocked.*|Locked"}}{R}))', "{{reason}}")], "reqps", w=12)
    b.panel("timeseries", "Rate-limit rejections", [(f"sum(rate(ratelimit_rejections_total{R}))", "rejected")], "reqps", w=12)
    b.panel("timeseries", "Actions by tool and result", [(f"sum by (tool, result) (rate(actions_total{R}))", "{{tool}} · {{result}}")], "reqps", w=12)
    b.save()


def dependencies() -> None:
    b = Board("pc-dependencies", "Procurement Copilot · Dependencies & Resilience", "Dependency health and degraded modes.")
    b.panel("state-timeline", "Dependency up", [("dependency_up", "{{dependency}}")], "short", w=24, h=6)
    b.panel("timeseries", "Jev degraded", [("max(jev_degraded)", "degraded")], "short", w=12)
    b.panel("timeseries", "LLM errors by provider", [(f'sum by (provider, result) (rate(llm_requests_total{{result!="ok"}}{R}))', "{{provider}} · {{result}}")], "reqps", w=12)
    b.panel("timeseries", "HTTP latency p95 by route", [(f"histogram_quantile(0.95, sum by (le, route) (rate(http_request_duration_seconds_bucket{R})))", "{{route}}")], "s", w=24)
    b.save()


if __name__ == "__main__":
    for build in (overview, llm_cost, retrieval_gate, security, dependencies):
        build()
    print(f"wrote {len(list(OUT.glob('*.json')))} dashboards to {OUT}")
