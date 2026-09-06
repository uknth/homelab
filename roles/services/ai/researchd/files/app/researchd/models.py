"""Pydantic models.

Two distinct jobs live here, and the split matters:

1. HTTP request/response shapes for the API.
2. Schemas the LLM's JSON output is validated against before a single
   field of it is trusted anywhere else in the app. This second group is
   the security boundary described in docs/spec/research.md: the model is
   called exactly three ways (plan, summarise, synthesise), and in every
   case its raw text is parsed as JSON and validated against one of the
   models below. Anything that fails validation is discarded by the
   caller in llm.py -- never retried with the invalid data, never passed
   through partially.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from pydantic import BaseModel, Field, field_validator, model_validator

# --------------------------------------------------------------------------
# HTTP API
# --------------------------------------------------------------------------


class ResearchRequest(BaseModel):
    topic: str | None = None
    url: str | None = None
    depth: int = 2
    # None means "use settings.default_engine" -- main.py resolves that and
    # validates the result against the engine registry (a 422 for an unknown
    # id belongs at the API boundary, not buried in pipeline.submit).
    engine: str | None = None

    @field_validator("depth")
    @classmethod
    def depth_in_range(cls, v: int) -> int:
        if v < 1 or v > 5:
            raise ValueError("depth must be between 1 and 5")
        return v

    @model_validator(mode="after")
    def topic_or_url(self) -> "ResearchRequest":
        topic = (self.topic or "").strip()
        url = (self.url or "").strip()
        if not topic and not url:
            raise ValueError("one of topic or url is required")
        self.topic = topic or None
        self.url = url or None
        return self


class PublishedRequest(BaseModel):
    wiki_url: str


class JobCreated(BaseModel):
    job_id: str


# --------------------------------------------------------------------------
# LLM output schemas -- the security boundary
# --------------------------------------------------------------------------


class PlanSubtopic(BaseModel):
    title: str
    scope: str = ""
    search_queries: list[str] = Field(default_factory=list)

    @field_validator("title")
    @classmethod
    def title_non_empty(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("subtopic title must not be empty")
        return v

    @field_validator("search_queries")
    @classmethod
    def clean_queries(cls, v: list[str]) -> list[str]:
        # Queries are strings, and only ever used as search-engine query
        # text (see search.py) -- never as URLs, never shelled out.
        return [q.strip() for q in v if isinstance(q, str) and q.strip()][:6]


class PlanOutline(BaseModel):
    # Optional on purpose: a model that omits it must not fail schema
    # validation, because a failed plan kills the whole job. The caller
    # falls back to the topic when it is absent.
    title: str | None = None
    subtopics: list[PlanSubtopic]

    @field_validator("subtopics")
    @classmethod
    def at_least_one(cls, v: list[PlanSubtopic]) -> list[PlanSubtopic]:
        if not v:
            raise ValueError("plan produced no subtopics")
        return v


class ExcerptOffset(BaseModel):
    """What the model may say about an excerpt: WHERE it is, not WHAT it
    says. `start`/`end` are integer character offsets into the source text
    it was shown; the actual excerpt text is never accepted from the model
    and is sliced from our own copy of the document in excerpts.py.
    """

    start: int
    end: int
    note: str = ""

    @field_validator("note")
    @classmethod
    def cap_note(cls, v: str) -> str:
        return (v or "").strip()[:400]


class SummariseResult(BaseModel):
    summary: str
    excerpts: list[ExcerptOffset] = Field(default_factory=list)

    @field_validator("summary")
    @classmethod
    def summary_non_empty(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("summary must not be empty")
        return v

    @field_validator("excerpts")
    @classmethod
    def cap_excerpts(cls, v: list[ExcerptOffset]) -> list[ExcerptOffset]:
        return v[:5]


class SynthesiseOutcome(BaseModel):
    """The result of one synthesise call. `body` is prose Markdown -- it is
    written into a note file verbatim as text and is never interpreted as
    anything else. `related_topics`/`open_questions` are lists of plain
    strings: related_topics are consumed strictly as search-query strings
    (never URLs) by the recursion step in pipeline.py, and open_questions
    are only ever rendered as bullet text.
    """

    body: str = ""
    related_topics: list[str] = Field(default_factory=list)
    open_questions: list[str] = Field(default_factory=list)

    @field_validator("related_topics", "open_questions")
    @classmethod
    def clean_string_list(cls, v: list[str]) -> list[str]:
        return [s.strip() for s in v if isinstance(s, str) and s.strip()][:10]


# --------------------------------------------------------------------------
# Remote engine output -- the other trust boundary
# --------------------------------------------------------------------------
#
# A sidecar engine (currently GPT Researcher) is a separate container reached
# over HTTP: its JSON result is exactly as untrusted as raw LLM output, so
# it gets the same schema-validate-or-discard treatment as the models
# above, via engines/remote.py. Unlike the LLM schemas, a malformed remote
# result is never silently trimmed to something usable -- engines/remote.py
# treats any ValidationError here as a hard PipelineError, because there is
# no equivalent of "one dead source, skip it and continue" for a payload
# that failed to parse as a whole.
#
# Caps below exist purely so a broken or hostile sidecar cannot make this
# process hold an unbounded amount of data in memory (or write an unbounded
# tree to disk) before write.py ever sees it; the numbers are generous
# relative to any real depth-5 job (which tops out at a few dozen notes and
# a few hundred sources).


class RemoteExcerpt(BaseModel):
    text: str
    note: str | None = None


class RemoteSourceCitation(BaseModel):
    index: int
    title: str
    url: str
    doi: str | None = None
    excerpts: list[RemoteExcerpt] = Field(default_factory=list)

    @field_validator("excerpts")
    @classmethod
    def cap_excerpts(cls, v: list[RemoteExcerpt]) -> list[RemoteExcerpt]:
        if len(v) > 50:
            raise ValueError("a single source citation carried more than 50 excerpts")
        return v


class RemoteNote(BaseModel):
    subtopic_title: str
    body: str
    sources: list[RemoteSourceCitation] = Field(default_factory=list)


class RemoteEngineResult(BaseModel):
    """The `POST /research/{eid}` result body once an engine job reaches
    `status: "done"`. Field names match write.NoteSpec / write.SourceCitation
    exactly on purpose -- engines/remote.py's conversion to those is then a
    plain field-by-field copy, never a remapping that could quietly drop or
    misplace something.
    """

    title: str | None = None
    # Required, unlike all_sources/related_topics/open_questions below: a
    # result with no notes at all has nothing for write.py to write, which
    # is a sign the sidecar sent a broken payload rather than a legitimate
    # empty one -- so a missing key here fails validation instead of
    # silently defaulting to an empty tree.
    notes: list[RemoteNote]
    all_sources: list[RemoteSourceCitation] = Field(default_factory=list)
    related_topics: list[str] = Field(default_factory=list)
    open_questions: list[str] = Field(default_factory=list)

    @field_validator("notes")
    @classmethod
    def cap_notes(cls, v: list[RemoteNote]) -> list[RemoteNote]:
        if len(v) > 2000:
            raise ValueError("result carried more than 2000 notes")
        return v

    @field_validator("all_sources")
    @classmethod
    def cap_sources(cls, v: list[RemoteSourceCitation]) -> list[RemoteSourceCitation]:
        if len(v) > 20_000:
            raise ValueError("result carried more than 20000 sources")
        return v

    @field_validator("related_topics", "open_questions")
    @classmethod
    def cap_string_lists(cls, v: list[str]) -> list[str]:
        if len(v) > 500:
            raise ValueError("result carried more than 500 related_topics/open_questions")
        return [s for s in v if isinstance(s, str) and s.strip()]


# --------------------------------------------------------------------------
# Internal plumbing (not LLM-validated; built by our own code)
# --------------------------------------------------------------------------


@dataclass
class SearchResult:
    title: str
    url: str
    engine: str
    doi: str | None = None
    snippet: str = ""


@dataclass
class FetchedDoc:
    url: str
    title: str
    text: str
    doi: str | None = None
    content_type: str = "text/html"


@dataclass
class ExcerptRecord:
    start: int
    end: int
    text: str
    note: str = ""


@dataclass
class SourceRecord:
    source_id: int
    subtopic: str
    url: str
    title: str
    doi: str | None = None
    summary: str = ""
    excerpts: list[ExcerptRecord] = field(default_factory=list)
