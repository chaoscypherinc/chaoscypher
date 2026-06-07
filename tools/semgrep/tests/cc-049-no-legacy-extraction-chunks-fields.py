# Fixture for cc-049-no-legacy-extraction-chunks-fields.
# The six extraction_chunks_* / extraction_* fields were dropped by
# migration 0030. Bare-name, attribute access, dict subscript, and dict
# key string literals are all caught.


# ruleid: cc-049-no-legacy-extraction-chunks-fields
x1 = extraction_chunks_submitted

# ruleid: cc-049-no-legacy-extraction-chunks-fields
x2 = extraction_chunks_total

# ruleid: cc-049-no-legacy-extraction-chunks-fields
x3 = extraction_chunk_indices

# ruleid: cc-049-no-legacy-extraction-chunks-fields
x4 = extraction_last_activity

# ruleid: cc-049-no-legacy-extraction-chunks-fields
x5 = extraction_entities_preview

# ruleid: cc-049-no-legacy-extraction-chunks-fields
x6 = extraction_relationships_preview


def read_attrs(source):
    # ruleid: cc-049-no-legacy-extraction-chunks-fields
    a = source.extraction_chunks_submitted
    # ruleid: cc-049-no-legacy-extraction-chunks-fields
    b = source.extraction_chunks_total
    # ruleid: cc-049-no-legacy-extraction-chunks-fields
    c = source.extraction_chunk_indices
    # ruleid: cc-049-no-legacy-extraction-chunks-fields
    d = source.extraction_last_activity
    # ruleid: cc-049-no-legacy-extraction-chunks-fields
    e = source.extraction_entities_preview
    # ruleid: cc-049-no-legacy-extraction-chunks-fields
    f = source.extraction_relationships_preview
    return a, b, c, d, e, f


def read_subscript(row):
    # ruleid: cc-049-no-legacy-extraction-chunks-fields
    return row["extraction_chunks_submitted"]


def write_dict_key():
    return {
        # ruleid: cc-049-no-legacy-extraction-chunks-fields
        "extraction_chunks_total": 5,
    }


def ok(source):
    # ok: cc-049-no-legacy-extraction-chunks-fields
    return source.stage_progress["mcp_extraction"]


def ok_extras(source):
    # ok: cc-049-no-legacy-extraction-chunks-fields
    return source.stage_progress["mcp_extraction"].extras.get("entities_preview")
