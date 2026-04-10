"""
Read-only pollution measurement script.

Run manually against production to measure test pollution. Deletion happens
separately via manual SQL in the Supabase dashboard after review.

Usage:
    python scripts/check_test_pollution.py "postgresql://..."

DO NOT run automatically. DO NOT include in CI. DO NOT delete any rows.
"""

import sys

import psycopg2


def main():
    if len(sys.argv) != 2:
        print("Usage: python scripts/check_test_pollution.py \"postgresql://...\"")
        sys.exit(1)

    db_url = sys.argv[1]

    conn = psycopg2.connect(db_url)
    cur = conn.cursor()

    print("=" * 60)
    print("TEST POLLUTION MEASUREMENT (read-only)")
    print("=" * 60)

    # Total row count
    cur.execute("SELECT COUNT(*) FROM trips")
    total = cur.fetchone()[0]
    print(f"\nTotal trips in table: {total}")

    # Test-shaped rows by address pattern
    cur.execute("""
        SELECT COUNT(*) FROM trips
        WHERE LOWER(home_address) LIKE '%main st%'
           OR LOWER(home_address) LIKE '%test%'
           OR LOWER(home_address) LIKE '%fake%'
    """)
    test_shaped = cur.fetchone()[0]
    print(f"Test-shaped rows (address contains 'main st', 'test', 'fake'): {test_shaped}")

    # Draft trips created in last 20 days
    cur.execute("""
        SELECT COUNT(*) FROM trips
        WHERE trip_status = 'draft'
          AND created_at >= NOW() - INTERVAL '20 days'
    """)
    recent_drafts = cur.fetchone()[0]
    print(f"Draft trips created in last 20 days: {recent_drafts}")

    # Breakdown by day (last 20 days)
    cur.execute("""
        SELECT DATE(created_at) AS day, COUNT(*) AS cnt
        FROM trips
        WHERE created_at >= NOW() - INTERVAL '20 days'
        GROUP BY DATE(created_at)
        ORDER BY day DESC
    """)
    rows = cur.fetchall()
    print(f"\nTrips created per day (last 20 days):")
    print(f"  {'Date':<15} {'Count':>6}")
    print(f"  {'-' * 15} {'-' * 6}")
    for day, cnt in rows:
        print(f"  {str(day):<15} {cnt:>6}")

    # Test-shaped rows with user_id = NULL (anonymous drafts from tests)
    cur.execute("""
        SELECT COUNT(*) FROM trips
        WHERE user_id IS NULL
    """)
    anonymous = cur.fetchone()[0]
    print(f"\nAnonymous trips (user_id IS NULL): {anonymous}")

    cur.close()
    conn.close()

    print("\n" + "=" * 60)
    print("DONE — no rows were modified or deleted.")
    print("=" * 60)


if __name__ == "__main__":
    main()
