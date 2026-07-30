#!/usr/bin/env python3
"""
CloudMind Causal Incident Scenario Runner.

Triggers controlled stress states, dependency bottlenecks, and load spikes
against CloudMind services running on 127.0.0.1.
"""

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request

VALID_SCENARIOS = [
    "api-overload",
    "database-bottleneck",
    "cache-failure",
    "auth-failure",
    "transient-spike",
    "heal-all",
    "all",
]

SERVICE_PORTS = {
    "frontend": 5050,
    "api": 5051,
    "database": 5052,
    "cache": 5053,
    "auth": 5054,
}


def send_http_request(url, method="GET", data=None, timeout=3.0):
    """Sends an HTTP request with explicit timeout."""
    req = urllib.request.Request(url, method=method)
    if data:
        req.add_header("Content-Type", "application/json")
        encoded = json.dumps(data).encode("utf-8")
    else:
        encoded = None

    try:
        with urllib.request.urlopen(req, data=encoded, timeout=timeout) as resp:
            body = resp.read().decode("utf-8")
            return resp.status, body
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8") if e.fp else ""
        return e.code, body
    except Exception as e:
        return 0, str(e)


def heal_service(host, service_name, port, dry_run=False):
    """Heals a specific service."""
    url = f"http://{host}:{port}/heal"
    if dry_run:
        print(f"[DRY-RUN] Would call POST {url}")
        return True
    status, _ = send_http_request(url, method="POST", timeout=3.0)
    return status == 200


def heal_all_services(host, dry_run=False):
    """Heals all CloudMind microservices."""
    print("--- Healing All Services ---")
    success = True
    for name, port in SERVICE_PORTS.items():
        ok = heal_service(host, name, port, dry_run=dry_run)
        print(f"  {name.capitalize()} (port {port}): {'HEALED' if ok else 'FAILED TO HEAL'}")
        if not ok:
            success = False
    return success


def get_persisted_incidents(host, dry_run=False):
    """Fetches currently persisted incident records from frontend GET /aiops-incidents."""
    if dry_run:
        return []
    status, body = send_http_request(f"http://{host}:5050/aiops-incidents", timeout=3.0)
    if status == 200:
        try:
            data = json.loads(body)
            if isinstance(data, list):
                return data
        except Exception:
            pass
    return []


def _record_matches_expected(item, expected_service, expect_mode):
    """
    Private helper to validate that an incident record EXACTLY matches all required schema fields
    for expected_service in expect_mode (execute or recommend) without permissive fallbacks.
    """
    if not isinstance(item, dict):
        return False

    diag = item.get("diagnosis")
    pol = item.get("policy_decision")
    exec_res = item.get("execution_result")
    rec_res = item.get("recovery_result")

    if not (isinstance(diag, dict) and isinstance(pol, dict) and isinstance(exec_res, dict) and isinstance(rec_res, dict)):
        return False

    rec_act = diag.get("recommended_action")
    if not isinstance(rec_act, dict):
        return False

    if expect_mode == "execute":
        return (
            diag.get("probable_cause_service") == expected_service
            and rec_act.get("type") == "restart_service"
            and rec_act.get("target_service") == expected_service
            and pol.get("approved") is True
            and pol.get("mode") == "execute"
            and pol.get("action") == "restart_service"
            and pol.get("target") == expected_service
            and exec_res.get("executed") is True
            and exec_res.get("target") == expected_service
            and rec_res.get("status") == "recovered"
        )
    elif expect_mode == "recommend":
        return (
            diag.get("probable_cause_service") == expected_service
            and rec_act.get("type") == "restart_service"
            and rec_act.get("target_service") == expected_service
            and pol.get("approved") is True
            and pol.get("mode") == "recommend"
            and pol.get("action") == "restart_service"
            and pol.get("target") == expected_service
            and exec_res.get("executed") is False
            and rec_res.get("status") == "not_executed"
        )
    return False


def verify_scenario_incident(
    host,
    expected_service,
    initial_ids,
    timeout=30.0,
    expect_mode="recommend",
    settle_window=5.0,
    dry_run=False,
):
    """
    Polls /aiops-incidents for new matching incident records created after scenario start.
    Enforces exact schema matching without permissive fallbacks and re-validates the candidate
    after the settling window to prevent false positives.
    """
    if dry_run:
        print(f"[DRY-RUN] Would verify new incident record for service '{expected_service}' (expect-mode={expect_mode})")
        return True

    start_t = time.time()
    candidate_id = None

    while time.time() - start_t < timeout:
        incidents = get_persisted_incidents(host, dry_run=False)
        new_records = [item for item in incidents if item.get("incident_id") and item.get("incident_id") not in initial_ids]

        for item in new_records:
            if _record_matches_expected(item, expected_service, expect_mode):
                candidate_id = item.get("incident_id")
                break

        if candidate_id:
            break
        time.sleep(0.5)

    if not candidate_id:
        print(f"Error: Verification failed. No new matching incident record found for target '{expected_service}' (expect-mode={expect_mode}) within {timeout}s timeout.")
        return False

    # Settling window: continue polling briefly so late duplicate executions or candidate mutations are detected
    if settle_window > 0:
        time.sleep(settle_window)

    incidents = get_persisted_incidents(host, dry_run=False)
    new_records = [item for item in incidents if item.get("incident_id") and item.get("incident_id") not in initial_ids]

    # Re-find the exact candidate record by incident_id
    final_candidate = None
    for item in new_records:
        if item.get("incident_id") == candidate_id:
            final_candidate = item
            break

    if not final_candidate:
        print(f"Error: Candidate incident record '{candidate_id}' disappeared after settling window.")
        return False

    # Re-run complete exact validator against final_candidate
    if not _record_matches_expected(final_candidate, expected_service, expect_mode):
        print(f"Error: Candidate incident record '{candidate_id}' no longer matches expected criteria after settling window.")
        return False

    # Collect all executed records targeting expected_service
    executed_records = []
    for item in new_records:
        exec_res = item.get("execution_result")
        if isinstance(exec_res, dict) and exec_res.get("executed") is True and exec_res.get("target") == expected_service:
            executed_records.append(item)

    if expect_mode == "execute":
        if len(executed_records) > 1:
            print(f"Error: Duplicate execution detected for target service '{expected_service}'. Expected exactly 1 execution, found {len(executed_records)}.")
            return False

        if len(executed_records) == 1:
            if executed_records[0].get("incident_id") != candidate_id:
                print(f"Error: Executed record after settling window is not the validated candidate record '{candidate_id}'.")
                return False

            status_val = (final_candidate.get("recovery_result") or {}).get("status")
            print(f"Verified Execute-Mode Incident Record: ID={candidate_id}, Service={expected_service}, Executed=True, RecoveryStatus={status_val}")
            return True

        print(f"Error: Execute-mode verification failed for '{expected_service}'. No executed record found.")
        return False

    else:
        # expect_mode == "recommend"
        if len(executed_records) > 0:
            print(f"Error: Recommend-mode verification failed for target service '{expected_service}'. Expected 0 executions, found {len(executed_records)} executed records.")
            return False

        print(f"Verified Recommend-Mode Incident Record for Service={expected_service} (ID={candidate_id})")
        return True


def run_api_overload(host, requests_count, incident_timeout, expect_mode, settle_window, dry_run):
    """Scenario: Direct API overload stress."""
    print(f"=== Running Scenario: api-overload (expect-mode={expect_mode}) ===")
    if dry_run:
        print(f"[DRY-RUN] Stressing API at http://{host}:5051/stress")
        print(f"[DRY-RUN] Sending {requests_count} requests to API /work")
        return True

    initial_ids = {item.get("incident_id") for item in get_persisted_incidents(host, dry_run=False) if item.get("incident_id")}

    status, body = send_http_request(f"http://{host}:5051/stress", method="POST", timeout=3.0)
    if status != 200:
        print(f"Failed to stress API: HTTP {status} ({body})")
        return False

    print(f"API stressed. Exercising API endpoints ({requests_count} calls)...")
    err_count = 0
    stressed_observed = False
    for _ in range(requests_count):
        s, body = send_http_request(f"http://{host}:5051/work", timeout=3.0)
        if s != 200:
            err_count += 1
        if "is_stressed" in body and "true" in body.lower():
            stressed_observed = True
        time.sleep(0.05)

    if not stressed_observed:
        status_code, body = send_http_request(f"http://{host}:5051/status", timeout=2.0)
        if status_code == 200 and "true" in body.lower():
            stressed_observed = True

    if not stressed_observed:
        print("Error: API overload scenario failed to produce observable API stress state.")
        return False

    print(f"Scenario api-overload complete. Observable API stress verified.")
    return verify_scenario_incident(
        host, "api", initial_ids, timeout=incident_timeout, expect_mode=expect_mode, settle_window=settle_window, dry_run=False
    )


def run_database_bottleneck(host, requests_count, incident_timeout, expect_mode, settle_window, dry_run):
    """Scenario: Database degradation propagating into API latency/errors."""
    print(f"=== Running Scenario: database-bottleneck (expect-mode={expect_mode}) ===")
    if dry_run:
        print(f"[DRY-RUN] Stressing Database at http://{host}:5052/stress")
        print(f"[DRY-RUN] Exercising API /work for {requests_count} calls")
        return True

    initial_ids = {item.get("incident_id") for item in get_persisted_incidents(host, dry_run=False) if item.get("incident_id")}

    status, body = send_http_request(f"http://{host}:5052/stress", method="POST", timeout=3.0)
    if status != 200:
        print(f"Failed to stress Database: HTTP {status} ({body})")
        return False

    print("Database stressed. Exercising API /work to observe database dependency degradation...")
    db_degraded_observed = False
    for _ in range(requests_count):
        s, body = send_http_request(f"http://{host}:5051/work", timeout=3.0)
        if s == 503 and "database" in body:
            try:
                data = json.loads(body)
                deps = data.get("dependencies", {})
                if not deps.get("database", {}).get("up", True):
                    db_degraded_observed = True
            except Exception:
                db_degraded_observed = True
        time.sleep(0.05)

    if not db_degraded_observed:
        print("Error: Scenario database-bottleneck failed. Database dependency degradation was NOT observed at API.")
        return False

    print("Scenario database-bottleneck complete. Observed API database dependency degradation.")
    return verify_scenario_incident(
        host, "database", initial_ids, timeout=incident_timeout, expect_mode=expect_mode, settle_window=settle_window, dry_run=False
    )


def run_cache_failure(host, requests_count, incident_timeout, expect_mode, settle_window, dry_run):
    """Scenario: Cache dependency failure propagating to API."""
    print(f"=== Running Scenario: cache-failure (expect-mode={expect_mode}) ===")
    if dry_run:
        print(f"[DRY-RUN] Stressing Cache at http://{host}:5053/stress")
        print(f"[DRY-RUN] Exercising API /work for {requests_count} calls")
        return True

    initial_ids = {item.get("incident_id") for item in get_persisted_incidents(host, dry_run=False) if item.get("incident_id")}

    status, body = send_http_request(f"http://{host}:5053/stress", method="POST", timeout=3.0)
    if status != 200:
        print(f"Failed to stress Cache: HTTP {status} ({body})")
        return False

    print("Cache stressed. Exercising API /work to observe cache dependency degradation...")
    cache_degraded_observed = False
    for _ in range(requests_count):
        s, body = send_http_request(f"http://{host}:5051/work", timeout=3.0)
        if s == 503 and "cache" in body:
            try:
                data = json.loads(body)
                deps = data.get("dependencies", {})
                if not deps.get("cache", {}).get("up", True):
                    cache_degraded_observed = True
            except Exception:
                cache_degraded_observed = True
        time.sleep(0.05)

    if not cache_degraded_observed:
        print("Error: Scenario cache-failure failed. Cache dependency degradation was NOT observed at API.")
        return False

    print("Scenario cache-failure complete. Observed API cache dependency degradation.")
    return verify_scenario_incident(
        host, "cache", initial_ids, timeout=incident_timeout, expect_mode=expect_mode, settle_window=settle_window, dry_run=False
    )


def run_auth_failure(host, requests_count, incident_timeout, expect_mode, settle_window, dry_run):
    """Scenario: Auth dependency failure propagating to API."""
    print(f"=== Running Scenario: auth-failure (expect-mode={expect_mode}) ===")
    if dry_run:
        print(f"[DRY-RUN] Stressing Auth at http://{host}:5054/stress")
        print(f"[DRY-RUN] Exercising API /work for {requests_count} calls")
        return True

    initial_ids = {item.get("incident_id") for item in get_persisted_incidents(host, dry_run=False) if item.get("incident_id")}

    status, body = send_http_request(f"http://{host}:5054/stress", method="POST", timeout=3.0)
    if status != 200:
        print(f"Failed to stress Auth: HTTP {status} ({body})")
        return False

    print("Auth stressed. Exercising API /work to observe auth dependency degradation...")
    auth_degraded_observed = False
    for _ in range(requests_count):
        s, body = send_http_request(f"http://{host}:5051/work", timeout=3.0)
        if s == 503 and "auth" in body:
            try:
                data = json.loads(body)
                deps = data.get("dependencies", {})
                if not deps.get("auth", {}).get("up", True):
                    auth_degraded_observed = True
            except Exception:
                auth_degraded_observed = True
        time.sleep(0.05)

    if not auth_degraded_observed:
        print("Error: Scenario auth-failure failed. Auth dependency degradation was NOT observed at API.")
        return False

    print("Scenario auth-failure complete. Observed API auth dependency degradation.")
    return verify_scenario_incident(
        host, "auth", initial_ids, timeout=incident_timeout, expect_mode=expect_mode, settle_window=settle_window, dry_run=False
    )


def run_transient_spike(host, requests_count, duration_sec, incident_timeout, expect_mode, settle_window, dry_run):
    """Scenario: Temporary traffic increase starting with all services confirmed healed (remediation unnecessary)."""
    print("=== Running Scenario: transient-spike ===")
    if dry_run:
        print(f"[DRY-RUN] Healing all services first.")
        print(f"[DRY-RUN] Sending burst traffic to API /work for {duration_sec}s without triggering service stress.")
        return True

    # Transient scenario MUST start with every service confirmed healed
    if not heal_all_services(host, dry_run=False):
        print("Error: Transient scenario could not confirm all services healed before start.")
        return False

    initial_ids = {item.get("incident_id") for item in get_persisted_incidents(host, dry_run=False) if item.get("incident_id")}

    print(f"Sending burst traffic to API /work for {duration_sec}s (no stress state injected)...")
    start_t = time.time()
    count = 0
    while time.time() - start_t < duration_sec and count < requests_count:
        send_http_request(f"http://{host}:5051/work", timeout=3.0)
        count += 1
        time.sleep(0.02)

    # Verify services remain unstressed
    all_clean = True
    for name, port in SERVICE_PORTS.items():
        s, body = send_http_request(f"http://{host}:{port}/status", timeout=2.0)
        if s == 200 and "true" in body.lower() and "is_stressed\": true" in body.lower():
            print(f"Error: Service {name} is unexpectedly stressed after transient spike.")
            all_clean = False

    if not all_clean:
        return False

    # Check that no restart_service recommendation was produced
    incidents = get_persisted_incidents(host, dry_run=False)
    for item in incidents:
        inc_id = item.get("incident_id")
        if inc_id and inc_id not in initial_ids:
            diag = item.get("diagnosis") or {}
            pol = item.get("policy_decision") or item.get("policy") or {}
            rec_action = diag.get("recommended_action") or {}
            rec_type = rec_action.get("type") if isinstance(rec_action, dict) else None
            pol_action = pol.get("action")

            if rec_type == "restart_service" or pol_action == "restart_service":
                print(f"Error: Transient spike scenario produced unexpected restart_service recommendation: {inc_id}")
                return False

    print(f"Scenario transient-spike complete. Sent {count} requests. Services remain healthy.")
    return True


def main():
    parser = argparse.ArgumentParser(
        description="CloudMind AIOps Causal Incident Scenario Runner"
    )
    parser.add_argument(
        "scenario",
        nargs="?",
        default="all",
        choices=VALID_SCENARIOS,
        help="Scenario name to execute",
    )
    parser.add_argument(
        "--scenario",
        dest="scenario_opt",
        choices=VALID_SCENARIOS,
        help="Explicit scenario argument",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Display actions without making actual network requests",
    )
    parser.add_argument(
        "--requests",
        type=int,
        default=10,
        help="Number of requests per scenario (1 to 100)",
    )
    parser.add_argument(
        "--duration",
        type=float,
        default=5.0,
        help="Duration in seconds for transient-spike (0.5 to 60.0)",
    )
    parser.add_argument(
        "--incident-timeout",
        type=float,
        default=30.0,
        help="Timeout in seconds for incident verification (5.0 to 120.0)",
    )
    parser.add_argument(
        "--expect-mode",
        choices=["recommend", "execute"],
        default="recommend",
        help="Expected execution mode for verification (recommend or execute)",
    )
    parser.add_argument(
        "--settle-window",
        type=float,
        default=5.0,
        help="Settling window in seconds for duplicate execution detection (0.0 to 30.0)",
    )
    parser.add_argument(
        "--target-host",
        default="127.0.0.1",
        help="Target host (must be 127.0.0.1 or localhost)",
    )

    args = parser.parse_args()

    scenario = args.scenario_opt if args.scenario_opt else args.scenario

    # Target host validation
    host = args.target_host.strip()
    if host not in ("127.0.0.1", "localhost"):
        print(f"Error: Target host '{host}' is invalid. Target must strictly be 127.0.0.1 or localhost.")
        sys.exit(1)

    # Bounds validation
    requests_count = max(1, min(100, args.requests))
    duration_sec = max(0.5, min(60.0, args.duration))
    incident_timeout = max(5.0, min(120.0, args.incident_timeout))
    settle_window = max(0.0, min(30.0, args.settle_window))
    expect_mode = args.expect_mode

    print(f"--- CloudMind AIOps Scenario Runner ---")
    print(f"Target Host:      {host}")
    print(f"Scenario:         {scenario}")
    print(f"Dry Run:          {args.dry_run}")
    print(f"Requests:         {requests_count}")
    print(f"Duration:         {duration_sec}s")
    print(f"Incident Timeout: {incident_timeout}s")
    print(f"Expect Mode:      {expect_mode}")
    print(f"Settle Window:    {settle_window}s\n")

    overall_success = True

    try:
        if scenario == "all":
            print("--- Healing All Services Before Running 'all' Scenarios ---")
            if not heal_all_services(host, dry_run=args.dry_run):
                overall_success = False

        if scenario in ("api-overload", "all"):
            if scenario == "all":
                heal_all_services(host, dry_run=args.dry_run)
            ok = run_api_overload(host, requests_count, incident_timeout, expect_mode, settle_window, args.dry_run)
            if not ok:
                overall_success = False

        if scenario in ("database-bottleneck", "all"):
            if scenario == "all":
                heal_all_services(host, dry_run=args.dry_run)
            ok = run_database_bottleneck(host, requests_count, incident_timeout, expect_mode, settle_window, args.dry_run)
            if not ok:
                overall_success = False

        if scenario in ("cache-failure", "all"):
            if scenario == "all":
                heal_all_services(host, dry_run=args.dry_run)
            ok = run_cache_failure(host, requests_count, incident_timeout, expect_mode, settle_window, args.dry_run)
            if not ok:
                overall_success = False

        if scenario in ("auth-failure", "all"):
            if scenario == "all":
                heal_all_services(host, dry_run=args.dry_run)
            ok = run_auth_failure(host, requests_count, incident_timeout, expect_mode, settle_window, args.dry_run)
            if not ok:
                overall_success = False

        if scenario in ("transient-spike", "all"):
            if scenario == "all":
                heal_all_services(host, dry_run=args.dry_run)
            ok = run_transient_spike(host, requests_count, duration_sec, incident_timeout, expect_mode, settle_window, args.dry_run)
            if not ok:
                overall_success = False

        if scenario == "heal-all":
            ok = heal_all_services(host, dry_run=args.dry_run)
            if not ok:
                overall_success = False

    except Exception as e:
        print(f"Unhandled error during scenario execution: {e}")
        overall_success = False
    finally:
        # Guarantee cleanup/healing in finally block
        if scenario != "heal-all":
            heal_ok = heal_all_services(host, dry_run=args.dry_run)
            if not heal_ok:
                print("Warning: Cleanup healing had failures.")

    if not overall_success:
        print("\nScenario execution finished with errors.")
        sys.exit(1)

    print("\nScenario execution completed successfully.")
    sys.exit(0)


if __name__ == "__main__":
    main()
