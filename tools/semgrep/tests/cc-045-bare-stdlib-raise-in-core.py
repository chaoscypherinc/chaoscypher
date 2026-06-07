# Fixture for cc-045-bare-stdlib-raise-in-core.
# Bare stdlib raise (ValueError / RuntimeError / ...) in core/services
# or core/operations is banned — use a ChaosCypherException subclass.


def service_func_bad():
    # ruleid: cc-045-bare-stdlib-raise-in-core
    raise ValueError("nope")


def service_func_runtime_bad():
    # ruleid: cc-045-bare-stdlib-raise-in-core
    raise RuntimeError("nope")


def service_func_key_bad():
    # ruleid: cc-045-bare-stdlib-raise-in-core
    raise KeyError("nope")


def service_func_ok():
    from chaoscypher_core.exceptions import ValidationError

    # ok: cc-045-bare-stdlib-raise-in-core
    raise ValidationError("nope")


# Pydantic validator decorator carve-out — raises here are allowed.
def field_validator(*args, **kwargs):
    def deco(fn):
        return fn
    return deco


@field_validator("name")
def _validate(cls, v):
    # ok: cc-045-bare-stdlib-raise-in-core
    raise ValueError("invalid")
