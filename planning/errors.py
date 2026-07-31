class PlanningError(Exception):
    """Business-rule failure in planning services."""


class PlanningStateError(PlanningError):
    """Invalid plan status / lifecycle transition."""
