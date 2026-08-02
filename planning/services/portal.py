"""Department portal helpers (published plan + scoped picking)."""

from __future__ import annotations

from datetime import date

from django.db.models import Q
from django.utils import timezone

from locations.models import Location
from planning.models import Plan, PlanRunStatus, PlanStatus
from planning.services.exceptions import PlanningError
from planning.services.picking import build_picking_list


def resolve_location(location: str) -> Location:
    """Resolve location by numeric id or exact name."""
    raw = (location or '').strip()
    if not raw:
        raise PlanningError('location is required.')
    if raw.isdigit():
        loc = Location.objects.filter(pk=int(raw)).first()
    else:
        loc = Location.objects.filter(name=raw).first()
    if loc is None:
        raise PlanningError(f'Location not found: {raw}')
    return loc


def portal_today(
    *,
    location: str,
    plan_date: date | None = None,
    mode: str = 'outbound',
) -> dict:
    """
    Today's published plans with picking lines for one department.

    mode=outbound → from_location filter (issue sheet)
    mode=inbound → to_location filter (expecting receipts)
    """
    loc = resolve_location(location)
    day = plan_date or timezone.localdate()
    mode = (mode or 'outbound').strip().lower()
    if mode not in ('outbound', 'inbound'):
        raise PlanningError('mode must be outbound or inbound.')

    plans = (
        Plan.objects.filter(
            plan_date=day,
            published_at__isnull=False,
        )
        .exclude(status=PlanStatus.CLOSED)
        .order_by('id')
    )

    items = []
    for plan in plans:
        run = (
            plan.runs.filter(status=PlanRunStatus.COMPLETE)
            .order_by('-run_number')
            .first()
        )
        if run is None:
            continue

        involved = run.requirements.filter(closed=False).filter(
            Q(source_location_id=loc.id) | Q(destination_location_id=loc.id),
        ).exists()
        if not involved:
            continue

        if mode == 'outbound':
            plist = build_picking_list(run, from_location=loc.name)
        else:
            plist = build_picking_list(run, to_location=loc.name)

        if not plist['lines']:
            continue

        items.append({
            'plan_id': plan.id,
            'run_id': run.id,
            'plan_date': plan.plan_date.isoformat(),
            'plan_location_id': plan.location_id,
            'status': plan.status,
            'published_at': (
                plan.published_at.isoformat() if plan.published_at else None
            ),
            'lines': plist['lines'],
            'by_department': plist['by_department'],
        })

    return {
        'plan_date': day.isoformat(),
        'location': loc.name,
        'location_id': loc.id,
        'mode': mode,
        'items': items,
    }
