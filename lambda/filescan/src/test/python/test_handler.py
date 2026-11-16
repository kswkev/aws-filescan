from filescan import handler


def test_placeholder_reports_no_batch_failures():
    assert handler.handler({"Records": []}, None) == {"batchItemFailures": []}
