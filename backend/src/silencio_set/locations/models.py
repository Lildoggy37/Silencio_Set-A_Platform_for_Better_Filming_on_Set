from datetime import datetime
from uuid import UUID

from sqlalchemy import CheckConstraint, DateTime, ForeignKeyConstraint, Index, Integer, String, func, text
from sqlalchemy.orm import Mapped, mapped_column

from silencio_set.infrastructure.database import Base


class LocationDetails(Base):
    __tablename__ = 'location_details'
    __table_args__ = (
        ForeignKeyConstraint(['crew_id', 'location_resource_id'], ['resources.crew_id', 'resources.id'], ondelete='RESTRICT'),
    )
    location_resource_id: Mapped[UUID] = mapped_column(primary_key=True)
    crew_id: Mapped[UUID]
    address: Mapped[str | None] = mapped_column(String(300))
    access_instructions: Mapped[str | None] = mapped_column(String(1000))
    shooting_constraints: Mapped[str | None] = mapped_column(String(2000))


class LocationPhoto(Base):
    __tablename__ = 'location_photos'
    __table_args__ = (
        ForeignKeyConstraint(['crew_id', 'location_resource_id'], ['resources.crew_id', 'resources.id'], ondelete='RESTRICT'),
        *(ForeignKeyConstraint(['crew_id', field], ['crew_members.crew_id', 'crew_members.id'],
            name=f'fk_location_photos_{field}', ondelete='RESTRICT') for field in ('created_by', 'removed_by')),
        CheckConstraint("file_format IN ('jpeg','png','webp') AND byte_size BETWEEN 1 AND 10485760", name='file_valid'),
        CheckConstraint('width_px > 0 AND height_px > 0 AND width_px::bigint * height_px <= 25000000', name='pixels_valid'),
        CheckConstraint('preview_byte_size BETWEEN 1 AND 10485760', name='preview_size_valid'),
        CheckConstraint('(removed_at IS NULL) = (removed_by IS NULL)', name='removal_pair'),
        Index('ix_location_photos_active', 'crew_id', 'location_resource_id', 'created_at', 'id', postgresql_where=text('removed_at IS NULL')),
    )
    id: Mapped[UUID] = mapped_column(primary_key=True)
    crew_id: Mapped[UUID]
    location_resource_id: Mapped[UUID]
    created_by: Mapped[UUID]
    description: Mapped[str | None] = mapped_column(String(500))
    file_format: Mapped[str] = mapped_column(String(10))
    byte_size: Mapped[int] = mapped_column(Integer)
    width_px: Mapped[int] = mapped_column(Integer)
    height_px: Mapped[int] = mapped_column(Integer)
    sha256: Mapped[str] = mapped_column(String(64))
    storage_key: Mapped[str] = mapped_column(String(100), unique=True)
    preview_storage_key: Mapped[str] = mapped_column(String(100), unique=True)
    preview_byte_size: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    removed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    removed_by: Mapped[UUID | None]
