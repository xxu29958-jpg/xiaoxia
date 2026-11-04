"""Flush phase timings so a killed pytest batch still has execution evidence."""

import json


def pytest_collection_finish(session):
    print(
        "XIAOXIA_TEST_PROGRESS " + json.dumps({"collected": len(session.items)}),
        flush=True,
    )


def pytest_runtest_logreport(report):
    print(
        "XIAOXIA_TEST_PHASE "
        + json.dumps(
            {
                "nodeid": report.nodeid,
                "phase": report.when,
                "outcome": report.outcome,
                "seconds": round(report.duration, 6),
            }
        ),
        flush=True,
    )
