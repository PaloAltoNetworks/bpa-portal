"""Report summarization helpers — turn the raw BPA JSON into view-friendly data."""
from collections import defaultdict


CATEGORY_LABELS = {
    "network": "Network",
    "policies": "Policies",
    "panorama": "Panorama",
    "device": "Device",
    "service_health": "Service Health",
    "objects": "Objects",
}

SEVERITY_ORDER = ["Critical", "Warning", "Informational"]


def humanize(s):
    if not s:
        return ""
    return s.replace("_", " ").title()


def summarize(data):
    """Build the view model for the rendered report."""
    info = data.get("information", {}) or {}
    bp = data.get("best_practices", {}) or {}
    adoption_summary = data.get("adoption_summary", {}) or {}

    severity_failed = defaultdict(int)
    severity_passed = defaultdict(int)
    total_passed = 0
    total_failed = 0
    total_unknown = 0

    category_stats = {}
    findings = {}

    for cat, families in bp.items():
        if not isinstance(families, dict):
            continue
        cat_failed = defaultdict(int)
        cat_passed = 0
        cat_total = 0
        family_buckets = {}
        for family, items in families.items():
            if not isinstance(items, list):
                continue
            grouped = {}
            f_failed = 0
            f_passed = 0
            f_total = 0
            for item in items:
                cfg = item.get("configuration", {}) or {}
                for w in (item.get("warnings") or []):
                    sev = (w.get("check_type") or "Informational").title()
                    if sev not in SEVERITY_ORDER:
                        sev = "Informational"
                    passed = w.get("check_passed")
                    f_total += 1
                    cat_total += 1
                    if passed is True:
                        f_passed += 1
                        cat_passed += 1
                        total_passed += 1
                        severity_passed[sev] += 1
                    elif passed is False:
                        f_failed += 1
                        cat_failed[sev] += 1
                        total_failed += 1
                        severity_failed[sev] += 1
                    else:
                        total_unknown += 1

                    if passed is False:
                        key = (w.get("check_id"), w.get("check_name"), sev)
                        bucket = grouped.setdefault(key, {
                            "check_id": w.get("check_id"),
                            "check_name": w.get("check_name") or "(unnamed)",
                            "severity": sev,
                            "remediation": w.get("remediation"),
                            "messages": set(),
                            "affected_items": [],
                        })
                        if w.get("check_message"):
                            bucket["messages"].add(w["check_message"])
                        if w.get("remediation") and not bucket["remediation"]:
                            bucket["remediation"] = w["remediation"]
                        bucket["affected_items"].append({
                            "name": cfg.get("name") or cfg.get("rule_name") or "(unnamed)",
                            "location": cfg.get("location"),
                            "template_name": cfg.get("template_name"),
                            "template_type": cfg.get("template_type"),
                            "rulebase": cfg.get("rulebase"),
                        })
            if f_total == 0:
                continue
            buckets_sorted = sorted(
                grouped.values(),
                key=lambda b: (SEVERITY_ORDER.index(b["severity"]), -len(b["affected_items"])),
            )
            for b in buckets_sorted:
                b["messages"] = sorted(b["messages"])
                b["affected_count"] = len(b["affected_items"])
            family_buckets[family] = {
                "label": humanize(family),
                "total": f_total,
                "passed": f_passed,
                "failed": f_failed,
                "buckets": buckets_sorted,
            }

        if cat_total == 0:
            continue
        category_stats[cat] = {
            "label": CATEGORY_LABELS.get(cat, humanize(cat)),
            "total": cat_total,
            "passed": cat_passed,
            "failed": sum(cat_failed.values()),
            "by_severity": dict(cat_failed),
        }
        findings[cat] = {
            "label": CATEGORY_LABELS.get(cat, humanize(cat)),
            "families": family_buckets,
        }

    # Adoption summary — render security_rule metrics per location.
    adoption_rows = []
    sec = adoption_summary.get("policies", {}).get("security_rule", []) if isinstance(adoption_summary, dict) else []
    for rec in sec or []:
        cfg = rec.get("configuration", {}) or {}
        metrics = rec.get("metrics", {}) or {}
        adoption_rows.append({
            "location": cfg.get("location") or "—",
            "total_rules": metrics.get("total_rule_count"),
            "rule_enabled": metrics.get("rule_enabled"),
            "allow": metrics.get("allow_rule_count"),
            "deny": metrics.get("deny_rule_count"),
            "log_forwarding": metrics.get("log_forwarding_enabled"),
            "log_end": metrics.get("log_end"),
            "antivirus": metrics.get("antivirus_profile_enabled"),
            "anti_spyware": metrics.get("anti_spyware_profile_enabled"),
            "vuln": metrics.get("vulnerability_protection_profile_enabled"),
            "url": metrics.get("url_filtering_profile_enabled"),
            "wildfire": metrics.get("wildfire_analysis_profile_enabled"),
            "dns_security": metrics.get("dns_security_enabled"),
            "credential_theft": metrics.get("credential_theft_enabled"),
            "app_id": metrics.get("app_id_enabled"),
            "user_id": metrics.get("user_id_enabled"),
        })
    adoption_rows.sort(key=lambda r: r["location"])

    # Top findings — flatten then take the largest by affected_count.
    flat = []
    for cat, bucket in findings.items():
        for family, fdata in bucket["families"].items():
            for b in fdata["buckets"]:
                flat.append({
                    **b,
                    "category": CATEGORY_LABELS.get(cat, humanize(cat)),
                    "family": humanize(family),
                })
    flat.sort(key=lambda b: (SEVERITY_ORDER.index(b["severity"]), -b["affected_count"]))
    top_findings = flat[:15]

    # Aggregate identical check names across the whole report (executive view).
    by_check = {}
    for f in flat:
        key = (f["check_name"], f["severity"])
        agg = by_check.setdefault(key, {
            "check_name": f["check_name"],
            "severity": f["severity"],
            "affected_count": 0,
            "categories": set(),
            "messages": set(),
            "remediation": f.get("remediation"),
        })
        agg["affected_count"] += f["affected_count"]
        agg["categories"].add(f["category"])
        for m in f.get("messages", []) or []:
            agg["messages"].add(m)
        if f.get("remediation") and not agg["remediation"]:
            agg["remediation"] = f["remediation"]
    top_failing_checks = []
    for v in by_check.values():
        v["categories"] = sorted(v["categories"])
        v["messages"] = sorted(v["messages"])
        top_failing_checks.append(v)
    top_failing_checks.sort(
        key=lambda b: (SEVERITY_ORDER.index(b["severity"]), -b["affected_count"])
    )
    top_failing_checks = top_failing_checks[:10]

    # Adoption averages across locations.
    adoption_metric_labels = [
        ("antivirus", "Antivirus"),
        ("anti_spyware", "Anti-Spyware"),
        ("vuln", "Vulnerability"),
        ("url", "URL Filtering"),
        ("wildfire", "WildFire"),
        ("dns_security", "DNS Security"),
        ("credential_theft", "Cred Theft"),
        ("app_id", "App-ID"),
        ("user_id", "User-ID"),
        ("log_forwarding", "Log Forwarding"),
    ]
    adoption_avg = []
    if adoption_rows:
        for key, label in adoption_metric_labels:
            vals = [r.get(key) for r in adoption_rows if isinstance(r.get(key), (int, float))]
            if vals:
                adoption_avg.append({"label": label, "value": round(sum(vals) / len(vals), 1)})

    # Pass rate
    total_checks = total_passed + total_failed
    pass_rate = round((total_passed / total_checks) * 100, 1) if total_checks else 0.0

    return {
        "info": info,
        "totals": {
            "checks": total_passed + total_failed + total_unknown,
            "passed": total_passed,
            "failed": total_failed,
            "by_severity_failed": dict(severity_failed),
            "by_severity_passed": dict(severity_passed),
        },
        "category_stats": category_stats,
        "findings": findings,
        "adoption_rows": adoption_rows,
        "top_findings": top_findings,
        "top_failing_checks": top_failing_checks,
        "adoption_avg": adoption_avg,
        "pass_rate": pass_rate,
        "category_order": [c for c in CATEGORY_LABELS if c in findings],
    }
