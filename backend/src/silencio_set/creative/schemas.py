from datetime import datetime
from typing import Literal
from uuid import UUID
from pydantic import BaseModel, ConfigDict, Field, StrictStr, field_validator, model_validator
from silencio_set.crews.schemas import clean_text
from silencio_set.tasks.schemas import VersionInput


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)


class Source(Input):
    source_id: UUID
    copy_from: UUID | None = None


class CharacterInput(Input):
    name: StrictStr

    @field_validator('name')
    @classmethod
    def text(cls, value):
        return clean_text(value, 100)


class ResourceNeedInput(Input):
    resource_type: Literal['equipment', 'prop', 'location', 'consumable']
    name: StrictStr
    quantity: int = Field(strict=True, ge=1, le=1000000)
    unit: StrictStr
    resource_id: UUID | None = None

    @field_validator('name', 'unit')
    @classmethod
    def text(cls, value, info):
        return clean_text(value, 100 if info.field_name == 'name' else 10)


class SceneInput(Input):
    scene_id: UUID | None = None
    scene_number: StrictStr
    location_text: StrictStr
    time_of_day: Literal['day','night','other','unknown'] = 'unknown'
    source_start: int = Field(strict=True, ge=1, le=100000)
    source_end: int = Field(strict=True, ge=1, le=100000)
    production_note: StrictStr | None = None
    characters: list[CharacterInput] = Field(default_factory=list, max_length=100)
    resource_needs: list[ResourceNeedInput] = Field(default_factory=list, max_length=100)

    @field_validator('scene_number','location_text','production_note')
    @classmethod
    def text(cls, value, info):
        if value is None: return None
        return clean_text(value, {'scene_number':20,'location_text':100,'production_note':2000}[info.field_name], multiline=info.field_name=='production_note')

    @model_validator(mode='after')
    def range_valid(self):
        if self.source_end < self.source_start: raise ValueError('Invalid source range')
        if len({item.name for item in self.characters}) != len(self.characters):
            raise ValueError('Duplicate character')
        needs = self.resource_needs
        linked = [item.resource_id for item in needs if item.resource_id]
        if len({(item.resource_type, item.name) for item in needs}) != len(needs) or len(set(linked)) != len(linked):
            raise ValueError('Duplicate resource need')
        return self


class ShotInput(Input):
    shot_number: StrictStr
    shot_size: StrictStr
    visual_content: StrictStr
    camera_movement: StrictStr | None = None
    dialogue_sound: StrictStr | None = None
    duration_seconds: int | None = Field(default=None,strict=True,ge=1)

    @field_validator('shot_number','shot_size','visual_content','camera_movement','dialogue_sound')
    @classmethod
    def text(cls,value,info):
        if value is None: return None
        return clean_text(value, {'shot_number':20,'shot_size':30,'visual_content':2000,'camera_movement':200,'dialogue_sound':2000}[info.field_name], multiline=info.field_name in {'visual_content','dialogue_sound'})


class SceneSave(VersionInput):
    scenes: list[SceneInput] = Field(max_length=100)

    @model_validator(mode='after')
    def unique(self):
        numbers=[item.scene_number for item in self.scenes]
        ids=[item.scene_id for item in self.scenes if item.scene_id]
        if len(set(numbers))!=len(numbers) or len(set(ids))!=len(ids): raise ValueError('Duplicate scene')
        return self


class ShotSave(VersionInput):
    shots: list[ShotInput] = Field(max_length=100)

    @model_validator(mode='after')
    def unique(self):
        if len({item.shot_number for item in self.shots})!=len(self.shots): raise ValueError('Duplicate shot number')
        return self


class Decision(VersionInput):
    decision: Literal['accept','reject']
    review_note: StrictStr | None = None

    @field_validator('review_note')
    @classmethod
    def note(cls,value): return clean_text(value,500,multiline=True) if value is not None and value.strip() else None

    @model_validator(mode='after')
    def required_reason(self):
        if self.decision=='reject' and self.review_note is None: raise ValueError('Rejection requires reason')
        return self


class CreativeSummary(BaseModel):
    id: UUID
    kind: Literal['breakdowns','storyboards']
    source_id: UUID
    created_by: UUID | None = None
    version_no: int
    review_status: str
    lock_version: int
    reviewed_by: UUID | None
    reviewed_at: datetime | None


class CreativeView(CreativeSummary):
    items: list[dict]


class CreativePage(BaseModel):
    items: list[CreativeSummary]
    total: int
    page: int
    page_size: int


class ReviewView(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    reviewed_by: UUID
    decision: str
    review_note: str | None
    reviewed_lock_version: int
    reviewed_at: datetime
    content_snapshot: dict


class ReviewPage(BaseModel):
    items: list[ReviewView]
    total: int
    page: int
    page_size: int
