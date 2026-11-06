from sqlalchemy import exists, func, select
from silencio_set.resources.custody_models import Checkout
from silencio_set.resources.models import Reservation


async def outstanding(session, crew_id, *, resource_id=None, reservation_id=None):
    query = select(func.coalesce(func.sum(Checkout.quantity - Checkout.returned_quantity), 0)).where(Checkout.crew_id == crew_id)
    if resource_id is not None: query = query.where(Checkout.resource_id == resource_id)
    if reservation_id is not None: query = query.where(Checkout.reservation_id == reservation_id)
    return await session.scalar(query)


def unfinished_booking(crew_id, member_id):
    return exists(select(1).where(Checkout.crew_id == crew_id, Checkout.reservation_id == Reservation.id,
        Checkout.borrower_member_id == member_id, Checkout.quantity > Checkout.returned_quantity))
