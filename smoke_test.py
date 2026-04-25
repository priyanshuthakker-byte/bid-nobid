import requests

BASE = "http://127.0.0.1:10000"

TESTS = [
    ("GET", "/healthz", None),
    ("GET", "/health", None),
    ("GET", "/dashboard", None),
    ("GET", "/analytics", None),
    ("POST", "/bid-result", {"t247_id": "SMOKE1", "outcome": "Won"}),
    ("POST", "/tender/SMOKE1/technical-proposal", {}),
    ("POST", "/tender/SMOKE1/merge-pdf", {}),
    ("POST", "/tender/SMOKE1/auto-download", {}),
]


def main():
    failed = 0
    for method, path, body in TESTS:
        try:
            resp = requests.request(method, BASE + path, json=body, timeout=15)
            ok = resp.status_code < 500
            print(f"{method:4} {path:40} {resp.status_code} {'OK' if ok else 'FAIL'}")
            if not ok:
                failed += 1
        except Exception as exc:
            failed += 1
            print(f"{method:4} {path:40} ERROR {exc}")

    if failed:
        raise SystemExit(1)
    print("Smoke test passed.")


if __name__ == "__main__":
    main()
