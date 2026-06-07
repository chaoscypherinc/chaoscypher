# Fixture for cc-050-no-legacy-vision-columns.
# Legacy vision columns dropped by migration 0034 — any reference fires.


def attr_bad(source):
    # ruleid: cc-050-no-legacy-vision-columns
    return source.vision_pages_failed


def attr_bad2(source):
    # ruleid: cc-050-no-legacy-vision-columns
    return source.vision_failed_pages


def attr_bad3(source):
    # ruleid: cc-050-no-legacy-vision-columns
    return source.loader_pdf_failed_pages


def subscript_bad(row):
    # ruleid: cc-050-no-legacy-vision-columns
    return row["vision_pages_failed"]


def ok(adapter, source_id):
    from chaoscypher_core.constants import VisionPageStatus

    # ok: cc-050-no-legacy-vision-columns
    return adapter.list_vision_page_descriptions(
        source_id, statuses=[VisionPageStatus.FAILED]
    )
