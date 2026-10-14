import pytest
from pydantic import ValidationError

from core.store import FieldDef, ObjectDef


def test_reference_field_needs_target():
    with pytest.raises(ValidationError):
        FieldDef(name="owner", type="reference")


def test_non_reference_field_rejects_target():
    with pytest.raises(ValidationError):
        FieldDef(name="owner", type="string", reference_to="employee")


def test_duplicate_field_names_rejected():
    f = FieldDef(name="a", type="string")
    with pytest.raises(ValidationError):
        ObjectDef(name="thing", fields=[f, f])


def test_empty_object_rejected():
    with pytest.raises(ValidationError):
        ObjectDef(name="thing", fields=[])


@pytest.mark.parametrize("bad", ["Upper", "1abc", "has space", "a-b", "", "x" * 80])
def test_identifier_pattern_enforced(bad):
    with pytest.raises(ValidationError):
        FieldDef(name=bad, type="string")


def test_unknown_keys_rejected():
    # Matters for stage 1: model output with invented keys must fail validation, not pass silently.
    with pytest.raises(ValidationError):
        ObjectDef.model_validate(
            {"name": "thing", "fields": [{"name": "a", "type": "string"}], "sneaky": 1}
        )
