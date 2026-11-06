from datetime import datetime
from uuid import UUID
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, StrictStr, field_validator
from silencio_set.crews.schemas import clean_text
from silencio_set.tasks.schemas import VersionInput


class ScriptCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)
    title: StrictStr

    @field_validator("title")
    @classmethod
    def valid_title(cls, value): return clean_text(value, 100)


class ScriptText(VersionInput):
    text: StrictStr = Field(min_length=1, max_length=100_000)

    @field_validator("text")
    @classmethod
    def valid_text(cls, value): return clean_text(value, 100_000, multiline=True)


class WorkingVersion(VersionInput):
    version_id: UUID


class ScriptView(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    crew_id: UUID
    title: str
    current_version_id: UUID | None
    lock_version: int
    created_at: datetime
    updated_at: datetime


class ParagraphView(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    paragraph_no: int
    text: str
    source_page: int | None


class VersionSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    script_id: UUID
    version_no: int
    status: str
    lock_version: int
    confirmed_by: UUID | None
    confirmed_at: datetime | None


class SourceFileView(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    original_name: str
    file_format: str
    byte_size: int
    page_count: int | None
    extraction_notes: list[str]


class ExtractedParagraph(BaseModel):
    text: str
    source_page: int | None


class ExtractedDocument(BaseModel):
    paragraphs: list[ExtractedParagraph]
    page_count: int | None
    notes: list[str]


class FileImportIdentity(VersionInput):
    original_name: str
    file_format: Literal['docx', 'pdf', 'txt']
    byte_size: int
    sha256: str


class VersionView(VersionSummary):
    paragraphs: list[ParagraphView]
    source_file: SourceFileView | None = None


class ScriptResult(BaseModel):
    script: ScriptView
    version: VersionView | None = None


class ScriptPage(BaseModel):
    items: list[ScriptView]
    total: int
    page: int
    page_size: int


class VersionPage(BaseModel):
    items: list[VersionSummary]
    total: int
    page: int
    page_size: int
