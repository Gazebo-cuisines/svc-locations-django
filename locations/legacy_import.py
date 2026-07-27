"""Map legacy tblcontainers rows into loc_* model payloads."""

from locations.models import LocationFeature, LocationRole

ROLE_COLUMNS = {
    'internal': LocationRole.INTERNAL,
    'process': LocationRole.PROCESS,
    'supplier': LocationRole.SUPPLIER,
    'customer': LocationRole.CUSTOMER,
    'courier': LocationRole.COURIER,
    'depot': LocationRole.DEPOT,
    'storage': LocationRole.STORAGE,
    'transform': LocationRole.TRANSFORM,
}

FEATURE_COLUMNS = {
    'customerOrders': LocationFeature.CUSTOMER_ORDERS,
    'customerForecasts': LocationFeature.CUSTOMER_FORECASTS,
    'productionplanshort': LocationFeature.PRODUCTION_PLAN_SHORT,
    'pickinglist': LocationFeature.PICKING_LIST,
    'breakdownlist': LocationFeature.BREAKDOWN_LIST,
    'linearplan': LocationFeature.LINEAR_PLAN,
    'issuereceipies': LocationFeature.ISSUE_RECIPES,
    'staffbudget': LocationFeature.STAFF_BUDGET,
    'anomalyReportStkIn': LocationFeature.ANOMALY_REPORT_STK_IN,
    'planClosingReport': LocationFeature.PLAN_CLOSING_REPORT,
}


def legacy_flag_true(value) -> bool:
    return value not in (None, 0)


def legacy_visible(value) -> bool:
    if value is None:
        return True
    return value != 0


def legacy_real_stock(value) -> bool:
    if value is None or value == -1:
        return True
    return value != 0


def roles_from_row(row: dict) -> list[str]:
    return [
        role.value
        for column, role in ROLE_COLUMNS.items()
        if legacy_flag_true(row.get(column))
    ]


def features_from_row(row: dict) -> list[str]:
    return [
        feature.value
        for column, feature in FEATURE_COLUMNS.items()
        if legacy_flag_true(row.get(column))
    ]
