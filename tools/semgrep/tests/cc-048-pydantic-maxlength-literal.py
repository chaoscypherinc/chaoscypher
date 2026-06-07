# Fixture for cc-048-pydantic-maxlength-literal.
# Pydantic Field(max_length=N) literals are banned — use policy constants
# or max_length_from_settings().

from pydantic import BaseModel, Field

from chaoscypher_core import policy


class BadModel(BaseModel):
    # ruleid: cc-048-pydantic-maxlength-literal
    name: str = Field(max_length=100)


class OkPolicyModel(BaseModel):
    # ok: cc-048-pydantic-maxlength-literal
    username: str = Field(max_length=policy.USERNAME_MAX_LENGTH)
