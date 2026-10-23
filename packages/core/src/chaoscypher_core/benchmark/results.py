# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Benchmark result rows and their JSON file format.

Lives in Core so every producer of result rows - the CLI's local runner and
the MCP benchmark bridge - writes the same file the leaderboard reads.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from dataclasses import asdict, dataclass, field
from datetime import datetime
from functools import cache
from pathlib import Path
from typing import TYPE_CHECKING, Any

from chaoscypher_core.benchmark.types import ScoreResult
from chaoscypher_core.utils.decoding import seed_honoured


if TYPE_CHECKING:
    from collections.abc import Callable


BENCHMARK_VERSION = "2.0"


@cache
def current_app_version() -> str:
    """The ChaosCypher build that produced a row: package version plus git commit.

    The package version alone does not pin a build - every commit between two
    releases reports the same one - so a source checkout appends ``+g<sha>``
    and ``.dirty`` when the tree has uncommitted changes (PEP 440 local label).
    An installed wheel, or a checkout git cannot read, gives the version alone.
    """
    from chaoscypher_core import __version__

    git = shutil.which("git")
    if git is None:
        return __version__
    repo = Path(__file__).resolve().parent
    try:
        sha = subprocess.run(  # noqa: S603 - fixed git argv, no user input
            [git, "rev-parse", "--short=9", "HEAD"],
            cwd=repo,
            capture_output=True,
            text=True,
            check=True,
            timeout=5,
        ).stdout.strip()
        dirty = subprocess.run(  # noqa: S603
            [git, "status", "--porcelain", "--untracked-files=no"],
            cwd=repo,
            capture_output=True,
            text=True,
            check=True,
            timeout=5,
        ).stdout.strip()
    except OSError, subprocess.SubprocessError:
        return __version__
    if not sha:
        return __version__
    return f"{__version__}+g{sha}" + (".dirty" if dirty else "")


# Captured when the benchmark modules load, not when the first row is written: a run that
# started on uncommitted changes committed minutes later recorded the later commit, clean
# (2026-10-15 provenance audit: 30 such runs). The cache holds the import-time answer.
current_app_version()


_ALLOWED_TOP_LEVEL_KEYS = {"results", "schema_version"}
_RESULTS_SCHEMA_VERSION = 1


@dataclass
class BenchmarkResult:
    """One row in the benchmark output: results of running one model on one dataset.

    Versions, seed and temperature are pinned per row so heterogeneous runs
    are detectable downstream. A harness-track row (``pins_applied`` False,
    e.g. a model benchmarked through an MCP client) ran under settings the
    benchmark did not control: its ``temperature`` and ``thinking`` are None
    because nothing pinned or observed them, and ``harness`` names the client.
    ``seed`` is None when nothing the row measured used one (a harness-track
    grounded-chat row).
    """

    model_id: str
    model_label: str
    dataset_id: str
    dataset_kind: str
    dataset_version: str
    dataset_source: str  # "builtin" | "user"
    config_name: str | None
    headline_score: float
    metrics: dict[str, Any]
    latency_ms_total: int
    latency_ms_per_chunk_p50: int
    input_tokens: int
    output_tokens: int
    cost_usd: float
    success: bool
    error: str | None
    timestamp: datetime
    benchmark_version: str
    scorer_version: int
    seed: int | None
    temperature: float | None
    thinking: bool | None
    thinking_honoured: bool | None
    chunks_truncated: int
    chunks_aborted_by_loop: int
    extras: dict[str, Any] = field(default_factory=dict)
    pins_applied: bool = True
    """False when the benchmark could not pin seed/temperature/thinking - the
    model ran inside another harness (an MCP client) on its own settings."""
    harness: str | None = None
    """Who ran the model when the benchmark did not, e.g. ``"mcp:claude-code"``."""
    harness_settings: dict[str, Any] | None = None
    """What the harness reported about its own settings - effort, thinking
    mode, client version - since any of them can move the score. None when
    the benchmark ran the model itself or the harness reported nothing."""
    app_version: str | None = field(default_factory=current_app_version)
    """The ChaosCypher build the row was measured on (see
    :func:`current_app_version`). None on rows written before it was recorded."""
    scorer_recipe: str | None = None
    """The scorer's computed recipe, next to the hand-bumped ``scorer_version``:
    a hash of its verdicts on fixed samples (the chat probes'
    :func:`~chaoscypher_core.benchmark.scorers.chat_probe_recipe.scorer_recipe`).
    Taken from ``metrics["scorer_recipe"]`` when not given, so every producer
    that builds a row from a scorer's metrics stamps it without restating it.
    None for a scorer that computes none, an errored row with no metrics, and
    rows written before 2026-10-06 (scorer v5 drifted without a version bump,
    H3 of that day's pipeline audit)."""

    seed_honoured: bool | None = None
    """Whether the model's provider sends the row's ``seed`` to the model at all
    (:func:`provider_honours_seed`): only the Ollama adapter does, so a cloud
    row's seed is a label, not a decoding setting. None on rows written before
    audit wave 3 (2026-10-06) and on harness rows, which record no seed."""

    def __post_init__(self) -> None:
        """Stamp the scorer's recipe from its metrics when the producer did not pass one."""
        if self.scorer_recipe is None and isinstance(self.metrics, dict):
            recipe = self.metrics.get("scorer_recipe")
            self.scorer_recipe = str(recipe) if recipe is not None else None


CACHE_RUN_SETTING = "cache"
"""The ``run_settings`` key of the LLM caches a row ran with (:func:`cache_run_setting`)."""

INFORMATIONAL_RUN_SETTINGS: frozenset[str] = frozenset({CACHE_RUN_SETTING})
"""``run_settings`` keys recorded for the reader, never compared (:func:`row_comparability`).

Whether the caches were on and whether the run was a replay do not change
an answer: a hit is the recorded answer of the same model build to the same
input under the same decoding, so a replayed row and a recorded one are one
measurement. A test pins that no comparability field reads them.
"""


def cache_run_setting(cache: Any) -> dict[str, bool]:
    """``{completions_enabled, embeddings_enabled, replay}`` of a run's cache settings.

    ``cache`` is a :class:`~chaoscypher_core.settings.CacheSettings`; ``replay``
    is whether either store refused misses (``require_hit``, ``benchmark run
    --replay``).
    """
    return {
        "completions_enabled": bool(cache.completions.enabled),
        "embeddings_enabled": bool(cache.embeddings.enabled),
        "replay": bool(cache.completions.require_hit or cache.embeddings.require_hit),
    }


def stamp_cache_run_setting(extras: dict[str, Any], cache: Any) -> None:
    """Record :func:`cache_run_setting` on a row's ``extras``, informational only.

    In ``extras["run_settings"]`` when the row records its run settings (a
    chat row), else as ``extras["cache"]``: a row without run settings must
    not gain a ``run_settings`` (its absence is a comparability fact,
    :data:`UNRECORDED_RUN_SETTINGS`).
    """
    run = extras.get("run_settings")
    if isinstance(run, dict) and run:
        run[CACHE_RUN_SETTING] = cache_run_setting(cache)
    else:
        extras[CACHE_RUN_SETTING] = cache_run_setting(cache)


def provider_honours_seed(provider: str | None) -> bool | None:
    """Whether a row's seed reached its model (:func:`~chaoscypher_core.utils.decoding.seed_honoured`).

    None when the provider is unknown (nothing recorded which one ran).
    """
    if not provider:
        return None
    return seed_honoured(provider)


HARNESS_SETTINGS_LEAD = ("effort", "thinking")
"""Harness settings shown first, in this order; the rest follow alphabetically."""


def format_harness_settings(settings: dict[str, Any] | None) -> list[str]:
    """Render harness settings as ``"key value"`` items in a stable order.

    ``effort`` and ``thinking`` lead (they move scores most), then the other
    keys alphabetically, so every surface words the same row the same way.
    """
    if not settings:
        return []
    lead = [k for k in HARNESS_SETTINGS_LEAD if k in settings]
    rest = sorted(k for k in settings if k not in HARNESS_SETTINGS_LEAD)
    return [f"{k} {_setting_text(settings[k])}" for k in (*lead, *rest)]


def _setting_text(value: Any) -> str:
    """Show a setting value: booleans as on/off, everything else as its string form."""
    if isinstance(value, bool):
        return "on" if value else "off"
    return str(value)


def _result_to_jsonable(r: BenchmarkResult) -> dict[str, Any]:
    """Convert a result to a JSON-safe dict (datetime to ISO string)."""
    d = asdict(r)
    d["timestamp"] = r.timestamp.isoformat()
    return d


def _result_from_jsonable(d: dict[str, Any]) -> BenchmarkResult:
    """Rebuild a BenchmarkResult from its JSON-safe dict form."""
    payload = dict(d)
    payload["timestamp"] = datetime.fromisoformat(payload["timestamp"])
    # Results written before 2026-09-22 have no thinking fields. Those runs did
    # execute with thinking off - it was the product default and the benchmark's
    # flag never reached the provider anyway - so `thinking=False` is accurate.
    # `thinking_honoured` stays None because nothing verified it at the time,
    # and "unverified" must not read as "confirmed".
    payload.setdefault("thinking", False)
    payload.setdefault("thinking_honoured", None)
    # Pre-2026-09-22 rows never recorded truncation; 0 means "not measured"
    # for those files, which the leaderboard states rather than implying clean.
    payload.setdefault("chunks_truncated", 0)
    payload.setdefault("chunks_aborted_by_loop", 0)
    payload.setdefault("extras", {})
    # Rows written before the MCP harness track existed all came from the
    # local runner, which applies the pins.
    payload.setdefault("pins_applied", True)
    payload.setdefault("harness", None)
    payload.setdefault("harness_settings", None)
    # Rows written before 2026-10-01 did not record the build; None says
    # "unknown" rather than stamping them with whatever build reads them.
    payload.setdefault("app_version", None)
    # Rows written before 2026-10-06 carry no recipe; a rescore stamps one.
    payload.setdefault("scorer_recipe", None)
    # Rows written before audit wave 3 did not say whether the seed reached the model.
    payload.setdefault("seed_honoured", None)
    return BenchmarkResult(**payload)


def dump_results(results: list[BenchmarkResult], path: Path) -> None:
    """Write results to a JSON file with a stable top-level shape.

    Args:
        results: Result rows to serialize.
        path: Destination file. Parent directory must already exist.
    """
    payload = {
        "schema_version": _RESULTS_SCHEMA_VERSION,
        "results": [_result_to_jsonable(r) for r in results],
    }
    # A trailing newline: the archived runs are committed, and the end-of-file
    # fixer rewrote every one that lacked it during the push hook (2026-10-07).
    path.write_text(json.dumps(payload, indent=2) + "\n")


def load_results(path: Path) -> list[BenchmarkResult]:
    """Load results from a JSON file written by :func:`dump_results`.

    Raises:
        ValueError: If the file's top-level shape contains unknown keys, or
            if the top-level value is not a JSON object.
    """
    payload = json.loads(path.read_text())
    if not isinstance(payload, dict):
        msg = "results file must be a JSON object, got array or other"
        raise TypeError(msg)
    unknown = set(payload.keys()) - _ALLOWED_TOP_LEVEL_KEYS
    if unknown:
        msg = f"unexpected top-level keys in results file: {unknown}"
        raise ValueError(msg)
    return [_result_from_jsonable(d) for d in payload["results"]]


TIMEOUT_ERROR_NAMES: frozenset[str] = frozenset(
    {
        "TimeoutError",
        "TimeoutException",
        "ConnectTimeout",
        "ReadTimeout",
        "WriteTimeout",
        "PoolTimeout",
    }
)
"""The exception classes a timeout raises: the wall clock's and httpx's (audit wave 5)."""


def is_timeout_error(exc: BaseException) -> bool:
    """Whether ``exc`` is a timeout: a ``TimeoutError`` or one of httpx's (by class name, no import).

    The runner stamps ``extras["timed_out"]`` with it; until audit wave 5 it
    asked ``isinstance(exc, TimeoutError)``, which httpx's ``ConnectTimeout``
    and ``ReadTimeout`` are not.
    """
    return any(cls.__name__ in TIMEOUT_ERROR_NAMES for cls in type(exc).__mro__)


def _error_is_timeout(error: str | None) -> bool:
    """Whether a row's recorded error (``"<Type>: <message>"``) names a timeout."""
    if not error:
        return False
    head = error.split(":", 1)[0].strip()
    lowered = error.lower()
    return head in TIMEOUT_ERROR_NAMES or "timeout" in lowered or "timed out" in lowered


def row_timed_out(row: BenchmarkResult) -> bool:
    """Whether a row hit a timeout: ``extras["timed_out"]``, or its error names one (rows before the flag).

    The one test :func:`row_problems` and the tools' ``--drop-timed-out-models``
    read, so a timed-out row is never dropped as merely errored (audit wave 4).
    The error is matched on :data:`TIMEOUT_ERROR_NAMES` and "timed out", so an
    httpx ``ConnectTimeout``/``ReadTimeout`` (or a provider error wrapping one)
    counts too (wave 5).
    """
    return bool((row.extras or {}).get("timed_out")) or (
        not row.success and _error_is_timeout(row.error)
    )


LATENCY_EXCLUDES_RETRY_WAIT = "latency_excludes_retry_wait"
"""The ``extras`` flag a row sets when its ``latency_ms_total`` already leaves out the retry backoff."""


def per_query_retry_wait_ms(per_query: list[dict[str, Any]] | None) -> int:
    """The backoff a run's question retries slept: the sum of each record's ``retry_wait_ms``."""
    return sum(int(q.get("retry_wait_ms") or 0) for q in per_query or [])


def retry_wait_ms(row: BenchmarkResult) -> int:
    """The backoff a row's question retries slept (:func:`per_query_retry_wait_ms` of its records)."""
    return per_query_retry_wait_ms((row.extras or {}).get("per_query"))


def row_active_latency_ms(row: BenchmarkResult) -> int:
    """A row's wall clock without the retry backoff: the model's time, not the harness's sleep.

    Rows written since audit wave 5 record ``latency_ms_total`` without the
    wait and set :data:`LATENCY_EXCLUDES_RETRY_WAIT`; an older row's total
    still holds it, so it is subtracted here. Every surface that shows a
    row's minutes reads this, never ``latency_ms_total`` directly.
    """
    if (row.extras or {}).get(LATENCY_EXCLUDES_RETRY_WAIT):
        return int(row.latency_ms_total)
    return max(0, int(row.latency_ms_total) - retry_wait_ms(row))


def row_problems(row: BenchmarkResult) -> list[str]:
    """Why a row is not a finished, fully answered run; empty when it is.

    A timed-out row (``extras["timed_out"]``, or a ``TimeoutError`` on rows
    written before the flag), a failed row (``success`` False) and a row with
    any question that errored (``per_query[*]["error"]``) all scored as if
    finished until 2026-10-06: p2-lean-3090's qwen3:14b row held 120
    connection errors and pooled as a 0.0 (audit H8). The exporter and the
    panel tools refuse a row with problems unless told to include it.
    """
    extras = row.extras or {}
    problems: list[str] = []
    if row_timed_out(row):
        problems.append("timed out")
    elif not row.success:
        problems.append(f"failed ({(row.error or 'no error recorded')[:120]})")
    errored = [q for q in extras.get("per_query") or [] if q.get("error")]
    if errored:
        first = errored[0]
        problems.append(
            f"{len(errored)} question(s) errored (first {first.get('query_id')}: "
            f"{str(first.get('error'))[:120]})"
        )
    return problems


def context_overflows(row: BenchmarkResult) -> list[str]:
    """Question ids whose prompt plus answer exceeded the chat model's context window.

    Reads ``per_query[*]["prompt_tokens"]``, ``["output_tokens"]`` and the
    window (``["num_ctx"]``, else ``extras["run_settings"]["num_ctx"]``);
    a question without the counts is not judged (rows before 2026-10-06
    recorded no prompt tokens, audit H9).
    """
    extras = row.extras or {}
    default_ctx = (extras.get("run_settings") or {}).get("num_ctx")
    out: list[str] = []
    for q in extras.get("per_query") or []:
        num_ctx = q.get("num_ctx") or default_ctx
        prompt = q.get("prompt_tokens")
        if not num_ctx or prompt is None:
            continue
        if int(prompt) + int(q.get("output_tokens") or 0) > int(num_ctx):
            out.append(str(q.get("query_id")))
    return out


def recorded_queries_file(row: BenchmarkResult) -> str | None:
    """The question file a chat row says it was asked (``extras["queries_file"]``), or None."""
    value = (row.extras or {}).get("queries_file")
    return str(value) if value else None


def row_corpus_id(row: BenchmarkResult) -> str:
    """The dataset a chat row's questions belong to: its ``dataset_id`` before ``__chat__``."""
    return row.dataset_id.split("__chat__")[0]


def resolve_queries_file(recorded: str | None, dataset_queries: Path | None) -> Path | None:
    """The question file to rescore a row against; None means the dataset's own questions.

    ``recorded`` is what the row says it was asked (:func:`recorded_queries_file`);
    a row that records nothing, or records the dataset's own file (the same
    path, a file with the same content in another checkout, or the same last
    three parts when the checkout moved), is the dataset's. Identity is by
    content, never by where a checkout lives: two worktrees of one fixture
    are one question file (2026-10-07, the v5 rows refused against B0).
    Anything else is a holdout set, which until 2026-10-06 (audit wave 2)
    every rescoring tool ignored: a holdout row rescored against the main
    fixture answered none of its questions and silently scored zero.

    Raises:
        FileNotFoundError: When the row's own question file is gone.
    """
    if not recorded:
        return None
    path = Path(recorded).expanduser()
    if dataset_queries is not None:
        if path.exists() and dataset_queries.exists():
            if path.resolve() == dataset_queries.resolve():
                return None
            from chaoscypher_core.cache.export import file_sha256

            if file_sha256(path) == file_sha256(dataset_queries):
                return None
        if not path.exists() and path.parts[-3:] == dataset_queries.parts[-3:]:
            return None
    if not path.is_file():
        msg = f"the question file the row was asked is missing: {recorded}"
        raise FileNotFoundError(msg)
    return path.resolve()


def row_queries_path(
    row: BenchmarkResult, dataset_queries: Callable[[str], Path | None]
) -> Path | None:
    """The question file a chat row was asked: its recorded file (a holdout set), else its dataset's.

    The one resolution every rescoring tool uses (:func:`resolve_queries_file`
    with the dataset's own file as the fallback): until audit wave 3
    ``review_check``, ``audit_chat_context`` and ``chat_miss_classifier``
    rescored every row against the main fixture.

    Raises:
        FileNotFoundError: When the row's own question file is gone.
    """
    own = dataset_queries(row_corpus_id(row))
    resolved = resolve_queries_file(recorded_queries_file(row), own)
    return resolved if resolved is not None else own


class QuestionFileMismatchError(ValueError):
    """Rows asked different question files, or not the one a caller named."""


def common_queries_file(
    rows: list[BenchmarkResult],
    dataset_queries: Callable[[str], Path | None],
    *,
    override: Path | None = None,
) -> Path | None:
    """The one question file every row was asked; None when it is each dataset's own.

    Args:
        rows: The chat rows a tool is about to rescore or pool.
        dataset_queries: The dataset's own question file for a corpus id
            (the CLI's ``load_dataset_bundle(...).queries_path``).
        override: A file the caller names (``--queries``); used for rows that
            record none (holdout rows from before the field), refused when a
            row records another.

    Raises:
        QuestionFileMismatchError: Naming each file and how many rows asked it.
    """
    from chaoscypher_core.cache.export import file_sha256

    # Identity by content (2026-10-08): two checkouts' copies of one question
    # file are one file. Rows are grouped by the sha256 of what they resolve
    # to, and the first path seen stands for the group.
    seen: dict[Path | None, list[str]] = {}
    path_by_sha: dict[str, Path] = {}
    for row in rows:
        own = dataset_queries(row_corpus_id(row))
        resolved = resolve_queries_file(recorded_queries_file(row), own)
        if resolved is not None:
            resolved = path_by_sha.setdefault(file_sha256(resolved), resolved)
        seen.setdefault(resolved, []).append(row.model_label)
    recorded = {path for path in seen if path is not None}
    if override is not None:
        wanted = override.expanduser().resolve()
        other = sorted(str(path) for path in recorded if path != wanted)
        if other:
            msg = (
                f"--queries {override} but rows record another question file ({', '.join(other)}); "
                "a row is rescored against the file it was asked"
            )
            raise QuestionFileMismatchError(msg)
        return wanted
    if len(seen) > 1:
        detail = "; ".join(
            f"{path or 'the dataset fixture'}: {len(models)} row(s)"
            for path, models in sorted(seen.items(), key=lambda kv: str(kv[0]))
        )
        msg = f"rows asked different question files ({detail}); rescore each set on its own"
        raise QuestionFileMismatchError(msg)
    return next(iter(recorded), None)


def answered_questions(row: BenchmarkResult, question_ids: set[str]) -> int:
    """How many of a row's per-query records are questions of the set it is rescored with.

    Zero for a row with records means the wrong question file: every verdict
    would read "no result: missing" and the row would score zero without a
    word (holdout rows rescored against the main fixture, audit wave 2).
    """
    records = (row.extras or {}).get("per_query") or []
    return sum(1 for r in records if str(r.get("query_id")) in question_ids)


def unanswered_message(row: BenchmarkResult, question_ids: set[str], source: str) -> str | None:
    """Why a row with answers cannot be rescored with ``source``'s questions; None when it can.

    Every tool that rescores rows refuses with this message (a rescore whose
    verdicts are all "missing" is a wrong question file, never "0 changed").
    """
    records = (row.extras or {}).get("per_query") or []
    if not records or answered_questions(row, question_ids):
        return None
    sample = ", ".join(sorted({str(r.get("query_id")) for r in records})[:5])
    return (
        f"{row.model_label} ({row.dataset_id}): none of its {len(records)} answers is a "
        f"question of {source} (it answered {sample}, ...); wrong question file? A holdout "
        "row records its file as extras.queries_file; an older one needs --queries"
    )


def recorded_queries_sha256(row: BenchmarkResult) -> str | None:
    """The sha256 of the question file's bytes a chat row was asked (``extras["queries_sha256"]``).

    None on rows written before 2026-10-06 (audit wave 3), which recorded the
    file's name but not its content: two rows of one name could have been
    asked, and scored on, two versions of the fixture.
    """
    value = (row.extras or {}).get("queries_sha256")
    return str(value) if value else None


def row_retrieval_mode(row: BenchmarkResult) -> str:
    """The rung a chat row was answered under (``extras["retrieval"]``, else its dataset id's suffix).

    Rows from before rungs existed are graphrag.
    """
    from chaoscypher_core.benchmark.retrieval import DEFAULT_RETRIEVAL_MODE, RETRIEVAL_MODES

    mode = (row.extras or {}).get("retrieval")
    if isinstance(mode, str) and mode in RETRIEVAL_MODES:
        return mode
    for candidate in RETRIEVAL_MODES:
        if row.dataset_id.endswith(f"__retrieval_{candidate}"):
            return candidate
    return DEFAULT_RETRIEVAL_MODE


UNRECORDED_RUN_SETTINGS = (
    "unrecorded (a row from before 2026-10-06: ran at temperature 0.3, no seed)"
)
"""What :func:`row_comparability` reports for a row without ``extras["run_settings"]``."""

COMPARABILITY_FIELDS: tuple[str, ...] = (
    "run_settings",
    "pack",
    "graph_sha256",
    "extraction_domain",
    "corpus_sha256",
    "resolution",
    "extraction_recipe",
    "temperature",
    "queries_file",
    "queries_sha256",
    "oracle",
    "dataset_id",
    "retrieval",
    "scorer_version",
    "scorer_recipe",
    "rescored_queries_sha256",
    "judge_model",
    "judge_prompts_sha256",
    "judge_model_build",
    "thinking",
    "num_ctx",
    "ai_max_tokens",
    "loop_guard",
)
"""What must be one value across every row a comparison pools or compares (audit waves 2 to 4).

Wave 3 added the question file's content, the dataset id and rung, the scorer
version, thinking, the context window, the output cap and the loop guard:
``variant_compare`` pooled rows that differed in any of them without a word.
Wave 4 added what scored the stored verdicts: the scorer's computed recipe
(two rows of one version scored by different code), the content of the
question file a rescore used (``rescored_queries_sha256``), and for a judged
row the judge model and its prompts' fingerprint. What differs by model by
design (the model's build) is compared per model: :data:`PER_MODEL_FIELDS`.
Wave 5 added the graph every row read, pinned or not (:data:`GRAPH_IDENTITY_FIELDS`:
until then only a pinned row carried the graph's sha256, from its pins) and the
judge's model build; and an unrecorded value is a value: a field one row
records and another does not is a finding (until wave 5 the question file's
content, the recipe, the rescore's questions and the judge prompts were
compared only between the rows that recorded them, so a row that recorded
none matched every row).
"""

GRAPH_IDENTITY_FIELDS: frozenset[str] = frozenset(
    {
        "pack",
        "graph_sha256",
        "extraction_domain",
        "corpus_sha256",
        "resolution",
        "extraction_recipe",
    }
)
"""The :data:`COMPARABILITY_FIELDS` that name the graph a row read (audit wave 5).

``graph_sha256`` is the snapshot's sha256 the row records
(``run_settings["graph_snapshot_sha256"]``, else its pins' ``graph_sha256``);
the others are the slot record's identity fields
(:data:`~chaoscypher_core.benchmark.chat_run.GRAPH_IDENTITY_KEYS`). A variant
whose subject *is* the graph (a re-extraction, another domain) asks for them to
be exempt, with the pins (:data:`PINS_EXEMPTION`): ``--graph-varies``, which
always reads "mixed, no verdict" (:func:`graph_varies_finding`).
"""

PINS_EXEMPTION = "pins"
"""The ``exempt`` name that skips the per-model pins comparison (pins are built per graph)."""

PER_MODEL_FIELDS: tuple[str, ...] = ("model_build",)
"""Facts compared between the rows of one model, never across models (audit wave 4).

``model_build`` (``extras["model_build"]``: the Ollama model digest, the
server version, the Modelfile parameters that still apply at temperature 0
such as ``repeat_penalty``, and the ``num_batch``/``num_thread``/``num_parallel``
the factory sends): an ``ollama pull`` or a server upgrade between a baseline
and a variant is two measurements, not an effect of the variant. Each key is
compared on its own; within one model, a key any row records (not None) must
be recorded by every row (audit wave 5: a row whose build fetch failed, or
from before the field, matched any build).
"""

MODEL_BUILD_CHANGED = "model_build_changed"
"""The ``extras`` key of the build a row's model reported after the row, when it moved (audit wave 6).

The runner asks for the build before the row (``model_build``) and again
after; an ``ollama pull`` or a server restart mid-row is two builds in one
row, which :func:`comparability_findings` reports for that model.
"""

RUNG_SPECIFIC_FIELDS: frozenset[str] = frozenset({"retrieval", "dataset_id"})
"""Fields that differ between the rungs of one three-rung comparison by design (audit wave 4).

The rung itself and the dataset id that names it (``__retrieval_<rung>``).
The pins differ by rung too (each rung is pinned on its own), which
:func:`comparability_findings` already allows: pins are compared per model,
lever set and rung. Everything else must be one value across the rungs.
"""

HARNESS_FIELDS_NOT_PINNED: frozenset[str] = frozenset(
    {"run_settings", "temperature", "thinking", "num_ctx", "ai_max_tokens", "loop_guard"}
)
"""Fields a harness-track row (``pins_applied`` False) is not held to: its client decoded it.

Such a row records no temperature, seed, thinking or context window (the
benchmark did not set them); it is still held to the graph, the pack, the
build, the question file and every other field.
"""

UNRECORDED = "unrecorded"
"""How a finding shows a field a row does not record (None): a value like any other (audit wave 5)."""


def row_comparability(row: BenchmarkResult) -> dict[str, Any]:
    """The facts two chat rows must share to be compared, as :data:`COMPARABILITY_FIELDS`.

    Whether ``run_settings`` was recorded (rows from before 2026-10-06 record
    seed 42 / temperature 0 but ran at 0.3 with no seed, audit H1), the pack,
    the served graph's sha256, the temperature, the question
    file's name and content hash, whether the pins were an oracle ceiling,
    the dataset id and rung, the scorer version and recipe, the question
    content a rescore used, the judge, its prompts and its build, the graph's
    identity fields (:data:`GRAPH_IDENTITY_FIELDS`), thinking, and the
    context window, output cap and loop guard the chat ran with. Seeds, pins
    and the model build are compared by :func:`comparability_findings`,
    which knows the labels and the models.
    """
    extras = row.extras or {}
    run = extras.get("run_settings") or {}
    pins = extras.get("retrieval_pins") or {}
    recorded = recorded_queries_file(row)
    guard = run.get("loop_guard")
    judge_build = extras.get("judge_model_build")
    return {
        "run_settings": "recorded" if run else UNRECORDED_RUN_SETTINGS,
        "pack": run.get("pack") or pins.get("pack"),
        "graph_sha256": run.get("graph_snapshot_sha256") or pins.get("graph_sha256"),
        "extraction_domain": run.get("extraction_domain") or None,
        "corpus_sha256": run.get("corpus_sha256") or None,
        "resolution": run.get("resolution") or None,
        "extraction_recipe": run.get("extraction_recipe") or None,
        # app_version stays recorded on the row but is not compared (2026-10-07): a git
        # describe moves with every data commit (archiving the seed-42 pass changed it
        # before the seed-43 pass), while the code that shapes a row is already compared
        # through the scorer, extraction, resolution and retrieval recipes and the pins.
        "temperature": row.temperature,
        "queries_file": Path(recorded).name if recorded else None,
        "queries_sha256": recorded_queries_sha256(row),
        "oracle": bool(pins.get("oracle")),
        "dataset_id": row.dataset_id,
        "retrieval": row_retrieval_mode(row),
        "scorer_version": row.scorer_version,
        "scorer_recipe": row.scorer_recipe,
        "rescored_queries_sha256": extras.get("rescored_queries_sha256") or None,
        "judge_model": extras.get("judge_model") or None,
        "judge_prompts_sha256": extras.get("judge_prompts_sha256") or None,
        "judge_model_build": (
            json.dumps(judge_build, sort_keys=True) if isinstance(judge_build, dict) else None
        ),
        "thinking": row.thinking,
        "num_ctx": run.get("num_ctx"),
        "ai_max_tokens": run.get("ai_max_tokens"),
        "loop_guard": json.dumps(guard, sort_keys=True) if guard is not None else None,
    }


PINS_IDENTITY: tuple[str, ...] = (
    "file",
    "fingerprint",
    "content_digest",
    "retrieval_recipe",
    "index_digest",
)
"""What names the pins a row was served: the file and fingerprint, and (wave 3) the digests of
its contents, its retrieval recipe and the index it was built from."""


def row_model_build(row: BenchmarkResult) -> dict[str, Any] | None:
    """The model build a row recorded (``extras["model_build"]``), or None (not Ollama, or older)."""
    build = (row.extras or {}).get("model_build")
    return build if isinstance(build, dict) else None


def comparability_findings(
    rows_by_label: dict[str, list[BenchmarkResult]],
    *,
    baseline: str | None = None,
    exempt: frozenset[str] = frozenset(),
) -> list[str]:
    """What the labelled rows differ in that makes them two measurements; empty when none.

    Across every row of every label (not only within one label's repeats):
    each of :data:`COMPARABILITY_FIELDS` must be one value (a harness row is
    not held to :data:`HARNESS_FIELDS_NOT_PINNED`; a field one row records and
    another does not is two values, :data:`UNRECORDED`); no row may lack its run settings
    (it ran at temperature 0.3 with no seed, whatever it records, audit H1:
    one value across rows that all lack them is still not a measurement at
    temperature 0); rows of one model with the same resolved levers must have
    been served the same pins (:data:`PINS_IDENTITY`; a variant that changes
    the levers gets its own pins, so the pins are compared per lever set);
    and every seed a variant ran at must be one the ``baseline`` label ran at
    for the same model (the baseline may pool several, its seed band; until
    audit wave 4 the seeds were pooled per label, so a variant's model at a
    seed only another model's baseline ran passed). Without a baseline (the
    leaderboard's board) every pinned row must share one seed. One model's
    rows must share its build (:data:`PER_MODEL_FIELDS`). ``exempt`` names
    fields not compared (:data:`RUNG_SPECIFIC_FIELDS` across the rungs of the
    three-rung comparison; :data:`GRAPH_IDENTITY_FIELDS` and
    :data:`PINS_EXEMPTION` for a variant whose subject is the graph, which
    then adds :func:`graph_varies_finding` itself).
    """
    return [
        *_field_findings(rows_by_label, exempt),
        *([] if PINS_EXEMPTION in exempt else _pins_findings(rows_by_label)),
        *_seed_findings(rows_by_label, baseline),
        *_model_build_findings(rows_by_label),
    ]


def _field_findings(
    rows_by_label: dict[str, list[BenchmarkResult]], exempt: frozenset[str] = frozenset()
) -> list[str]:
    """Each :data:`COMPARABILITY_FIELDS` value seen more than once, and rows with no run settings."""
    findings: list[str] = []
    for name in COMPARABILITY_FIELDS:
        if name in exempt:
            continue
        values: dict[str, set[str]] = {}
        for label, rows in rows_by_label.items():
            for row in rows:
                if name in HARNESS_FIELDS_NOT_PINNED and not row.pins_applied:
                    continue
                value = row_comparability(row)[name]
                shown = UNRECORDED if value is None else str(value)
                values.setdefault(shown, set()).add(label)
        if len(values) > 1:
            detail = "; ".join(f"{v} ({', '.join(sorted(ls))})" for v, ls in sorted(values.items()))
            findings.append(f"{name}: {detail}")
    unrecorded = sorted(
        {
            label
            for label, rows in rows_by_label.items()
            for row in rows
            if row.pins_applied and not (row.extras or {}).get("run_settings")
        }
    )
    if unrecorded:
        findings.append(
            f"run_settings: {UNRECORDED_RUN_SETTINGS} ({', '.join(unrecorded)}): sampled rows, "
            "not a temperature-0 measurement"
        )
    return findings


def _pins_findings(rows_by_label: dict[str, list[BenchmarkResult]]) -> list[str]:
    """One model with one lever set on one rung served more than one pins (:data:`PINS_IDENTITY`).

    Per rung: each rung is pinned on its own, so the rungs of one comparison
    differ in pins by design; within one rung a second pins is a re-pin.
    """
    from chaoscypher_core.benchmark.chat_run import lever_signature

    served: dict[tuple[str, str, str], set[tuple[str, ...]]] = {}
    for rows in rows_by_label.values():
        for row in rows:
            extras = row.extras or {}
            pins = extras.get("retrieval_pins") or {}
            levers = (extras.get("run_settings") or {}).get("levers")
            key = (row.model_label, lever_signature(levers), row_retrieval_mode(row))
            served.setdefault(key, set()).add(tuple(str(pins.get(k)) for k in PINS_IDENTITY))
    return [
        f"pins: {model} with levers {levers} was served {len(pins_seen)} different {rung} pins "
        f"({', '.join(sorted({p[0] for p in pins_seen}))}): a re-pin between the runs"
        for (model, levers, rung), pins_seen in sorted(served.items())
        if len(pins_seen) > 1
    ]


def _model_build_findings(rows_by_label: dict[str, list[BenchmarkResult]]) -> list[str]:
    """One model's rows recording two values of one :data:`PER_MODEL_FIELDS` key (its build).

    A key one of the model's rows records (not None) and another does not
    (None, or no build at all) is two values: :data:`UNRECORDED` is one of
    them (audit wave 5: a failed build fetch matched any digest). A model no
    row records a build for (a cloud model) has nothing to compare. A row
    whose build moved while it ran (:data:`MODEL_BUILD_CHANGED`) is a finding
    on its own (audit wave 6).
    """
    by_model: dict[str, list[tuple[str, dict[str, Any]]]] = {}
    changed: dict[str, set[str]] = {}
    for label, rows in rows_by_label.items():
        for row in rows:
            by_model.setdefault(row.model_label, []).append((label, row_model_build(row) or {}))
            if (row.extras or {}).get(MODEL_BUILD_CHANGED) is not None:
                changed.setdefault(row.model_label, set()).add(label)
    findings: list[str] = [
        f"model_build: {model} changed while a row ran ({', '.join(sorted(labels))}): "
        "a pull or a server change mid-row"
        for model, labels in sorted(changed.items())
    ]
    for model, builds in sorted(by_model.items()):
        keys = sorted({k for _, build in builds for k, v in build.items() if v is not None})
        for key in keys:
            values: dict[str, set[str]] = {}
            for label, build in builds:
                value = build.get(key)
                if value is None:
                    shown = UNRECORDED
                elif isinstance(value, str):
                    shown = value
                else:
                    shown = json.dumps(value, sort_keys=True)
                values.setdefault(shown, set()).add(label)
            if len(values) > 1:
                findings.append(
                    f"model_build: {model} ran with {len(values)} values of {key} ("
                    + "; ".join(
                        f"{v} ({', '.join(sorted(ls))})" for v, ls in sorted(values.items())
                    )
                    + "): a pull, a server change or an unrecorded build between the runs"
                )
    return findings


def graph_varies_finding(rows_by_label: dict[str, list[BenchmarkResult]]) -> str:
    """The finding a comparison declared ``--graph-varies`` always carries: what the graphs were.

    The graph-identity findings :func:`comparability_findings` would have
    given (:data:`GRAPH_IDENTITY_FIELDS`, the pins), or "one graph" when the
    rows read one; either way a finding, so the verdict reads "mixed, no
    verdict": a variant whose subject is the graph measures two graphs, which
    the adoption rule was not written for (audit wave 5).
    """
    differs = [
        *(f for f in _field_findings(rows_by_label) if f.split(":", 1)[0] in GRAPH_IDENTITY_FIELDS),
        *_pins_findings(rows_by_label),
    ]
    detail = "; ".join(differs) if differs else "the rows read one graph"
    return f"graph: --graph-varies declared, the graph is what this comparison tests ({detail})"


def _seed_findings(
    rows_by_label: dict[str, list[BenchmarkResult]], baseline: str | None
) -> list[str]:
    """A variant model's seed its baseline rows never ran; without a baseline, more than one seed.

    Keyed on (label, model): a variant's model is held to the seeds the
    baseline ran *that model* at. A model the baseline lacks is not judged
    here; the model-set checks name it.
    """
    pinned_seeds: dict[tuple[str, str], set[int | None]] = {}
    for label, rows in rows_by_label.items():
        for row in rows:
            if row.pins_applied:
                pinned_seeds.setdefault((label, row.model_label), set()).add(row.seed)
    if baseline is None:
        every = sorted({str(s) for seeds in pinned_seeds.values() for s in seeds})
        if len(every) > 1:
            return [f"seed: the rows ran at seeds {', '.join(every)}; one board is one seed"]
        return []
    if baseline not in rows_by_label:
        return []
    findings: list[str] = []
    for (label, model), seeds in sorted(pinned_seeds.items(), key=lambda kv: kv[0]):
        base_seeds = pinned_seeds.get((baseline, model))
        if label == baseline or base_seeds is None:
            continue
        extra = sorted(str(s) for s in seeds - base_seeds)
        if extra:
            findings.append(
                f"seed: {label}'s {model} ran at seed {', '.join(extra)}, which the baseline "
                f"{baseline} never ran it at (it ran {sorted(str(s) for s in base_seeds)})"
            )
    return findings


__all__ = [
    "BENCHMARK_VERSION",
    "CACHE_RUN_SETTING",
    "COMPARABILITY_FIELDS",
    "GRAPH_IDENTITY_FIELDS",
    "HARNESS_FIELDS_NOT_PINNED",
    "HARNESS_SETTINGS_LEAD",
    "INFORMATIONAL_RUN_SETTINGS",
    "LATENCY_EXCLUDES_RETRY_WAIT",
    "MODEL_BUILD_CHANGED",
    "PER_MODEL_FIELDS",
    "PINS_EXEMPTION",
    "PINS_IDENTITY",
    "RUNG_SPECIFIC_FIELDS",
    "TIMEOUT_ERROR_NAMES",
    "UNRECORDED",
    "UNRECORDED_RUN_SETTINGS",
    "BenchmarkResult",
    "QuestionFileMismatchError",
    "ScoreResult",
    "answered_questions",
    "cache_run_setting",
    "common_queries_file",
    "comparability_findings",
    "context_overflows",
    "dump_results",
    "format_harness_settings",
    "graph_varies_finding",
    "is_timeout_error",
    "load_results",
    "per_query_retry_wait_ms",
    "provider_honours_seed",
    "recorded_queries_file",
    "recorded_queries_sha256",
    "resolve_queries_file",
    "retry_wait_ms",
    "row_active_latency_ms",
    "row_comparability",
    "row_corpus_id",
    "row_model_build",
    "row_problems",
    "row_queries_path",
    "row_retrieval_mode",
    "row_timed_out",
    "stamp_cache_run_setting",
    "unanswered_message",
]
