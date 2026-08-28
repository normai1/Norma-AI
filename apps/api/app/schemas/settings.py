import re
from zoneinfo import available_timezones

from pydantic import BaseModel, field_validator, model_validator

# A curated allow-list, not open ISO 4217 format-matching: a garbage-but-
# well-formed code stored now would need a data migration to fix once item 55
# wires up real billing. Extend by adding to this tuple.
SUPPORTED_CURRENCIES = ("USD", "EUR", "GBP", "CAD", "AUD")

DAYS_OF_WEEK = (
    "monday",
    "tuesday",
    "wednesday",
    "thursday",
    "friday",
    "saturday",
    "sunday",
)

_LOCALE_PATTERN = re.compile(r"^[a-z]{2}(-[A-Z]{2})?$")
_TIME_PATTERN = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")


def _check_currency(value: str) -> str:
    if value not in SUPPORTED_CURRENCIES:
        raise ValueError(
            f"currency must be one of {', '.join(SUPPORTED_CURRENCIES)}",
        )

    return value


class OrganizationSettings(BaseModel):
    currency: str = "USD"

    @field_validator("currency")
    @classmethod
    def _currency_is_supported(cls, value: str) -> str:
        return _check_currency(value)


class OrganizationSettingsUpdate(BaseModel):
    """
    A partial update - only fields present in the request are merged onto the
    existing stored settings (see organization_service.update_organization).
    """

    currency: str | None = None

    @field_validator("currency")
    @classmethod
    def _currency_is_supported(cls, value: str | None) -> str | None:
        return value if value is None else _check_currency(value)


def _check_timezone(value: str) -> str:
    if value not in available_timezones():
        raise ValueError(f"{value!r} is not a recognized IANA timezone name")

    return value


def _check_locale(value: str) -> str:
    if not _LOCALE_PATTERN.match(value):
        raise ValueError("locale must look like 'en' or 'en-US'")

    return value


def _check_business_hours_keys(
    value: dict[str, "BusinessHoursWindow | None"] | None,
) -> dict[str, "BusinessHoursWindow | None"] | None:
    if value is None:
        return value

    unknown = sorted(set(value) - set(DAYS_OF_WEEK))

    if unknown:
        raise ValueError(
            f"unknown day(s) {unknown}, must be one of {DAYS_OF_WEEK}",
        )

    return value


class BusinessHoursWindow(BaseModel):
    open: str
    close: str

    @field_validator("open", "close")
    @classmethod
    def _time_is_well_formed(cls, value: str) -> str:
        if not _TIME_PATTERN.match(value):
            raise ValueError("time must be 24-hour HH:MM")

        return value

    @model_validator(mode="after")
    def _close_after_open(self) -> "BusinessHoursWindow":
        if self.close <= self.open:
            raise ValueError("close must be after open")

        return self


class WorkspaceSettings(BaseModel):
    timezone: str = "UTC"
    locale: str = "en-US"
    business_hours: dict[str, BusinessHoursWindow | None] | None = None

    @field_validator("timezone")
    @classmethod
    def _timezone_is_recognized(cls, value: str) -> str:
        return _check_timezone(value)

    @field_validator("locale")
    @classmethod
    def _locale_is_well_formed(cls, value: str) -> str:
        return _check_locale(value)

    @field_validator("business_hours")
    @classmethod
    def _business_hours_keys_are_days(
        cls,
        value: dict[str, BusinessHoursWindow | None] | None,
    ) -> dict[str, BusinessHoursWindow | None] | None:
        return _check_business_hours_keys(value)


class WorkspaceSettingsUpdate(BaseModel):
    """
    A partial update - only fields present in the request are merged onto the
    existing stored settings (see workspace_service.update_workspace).
    """

    timezone: str | None = None
    locale: str | None = None
    business_hours: dict[str, BusinessHoursWindow | None] | None = None

    @field_validator("timezone")
    @classmethod
    def _timezone_is_recognized(cls, value: str | None) -> str | None:
        return value if value is None else _check_timezone(value)

    @field_validator("locale")
    @classmethod
    def _locale_is_well_formed(cls, value: str | None) -> str | None:
        return value if value is None else _check_locale(value)

    @field_validator("business_hours")
    @classmethod
    def _business_hours_keys_are_days(
        cls,
        value: dict[str, BusinessHoursWindow | None] | None,
    ) -> dict[str, BusinessHoursWindow | None] | None:
        return _check_business_hours_keys(value)
