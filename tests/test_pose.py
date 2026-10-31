import math as _math

import pytest

from hydra_umc_visual_servoing_api.pose import POSE_FIELDS, Pose6D, PoseParseError


def test_parse_valid():
    p = Pose6D.parse("1.0,2.0,3.0,0.1,0.2,0.3")
    assert p == Pose6D(1.0, 2.0, 3.0, 0.1, 0.2, 0.3)


def test_parse_wrong_field_count():
    with pytest.raises(PoseParseError):
        Pose6D.parse("1.0,2.0,3.0")


def test_parse_non_numeric():
    with pytest.raises(PoseParseError):
        Pose6D.parse("1.0,2.0,3.0,x,0.2,0.3")


@pytest.mark.parametrize("value", ["nan", "inf", "-inf"])
def test_parse_non_finite_value(value):
    with pytest.raises(PoseParseError):
        Pose6D.parse(f"1.0,2.0,3.0,{value},0.2,0.3")


def test_pose_is_immutable():
    p = Pose6D.parse("0,0,0,0,0,0")
    with pytest.raises(Exception):  # noqa: B017
        p.x = 1.0  # type: ignore[misc]


# ---- Pose6D.coerce: what a JSON request may carry ----

def test_coerce_accepts_the_string_the_list_and_the_object_and_they_agree():
    from_string = Pose6D.coerce("1,2,3,0.1,0.2,0.3")
    from_list = Pose6D.coerce([1, 2, 3, 0.1, 0.2, 0.3])
    from_object = Pose6D.coerce({"x": 1, "y": 2, "z": 3, "roll": 0.1, "pitch": 0.2, "yaw": 0.3})
    assert from_string == from_list == from_object == Pose6D(1.0, 2.0, 3.0, 0.1, 0.2, 0.3)
    assert Pose6D.coerce((1, 2, 3, 0, 0, 0)) == Pose6D(1.0, 2.0, 3.0, 0.0, 0.0, 0.0)


def test_coerce_names_a_missing_or_unknown_key_of_an_object():
    with pytest.raises(PoseParseError, match="missing yaw"):
        Pose6D.coerce({"x": 1, "y": 2, "z": 3, "roll": 0, "pitch": 0})
    with pytest.raises(PoseParseError, match="unknown q"):
        Pose6D.coerce({"x": 1, "y": 2, "z": 3, "roll": 0, "pitch": 0, "yaw": 0, "q": 1})
    with pytest.raises(PoseParseError, match="exactly"):
        Pose6D.coerce({})


def test_coerce_refuses_text_booleans_non_finite_numbers_and_wrong_lengths():
    for bad in (
        {"x": "1", "y": 2, "z": 3, "roll": 0, "pitch": 0, "yaw": 0},
        {"x": True, "y": 2, "z": 3, "roll": 0, "pitch": 0, "yaw": 0},
        {"x": _math.nan, "y": 2, "z": 3, "roll": 0, "pitch": 0, "yaw": 0},
        {"x": _math.inf, "y": 2, "z": 3, "roll": 0, "pitch": 0, "yaw": 0},
        [1, 2, 3],
        [1, 2, 3, 4, 5, "6"],
        [1, 2, 3, 4, 5, None],
        5,
        None,
    ):
        with pytest.raises(PoseParseError):
            Pose6D.coerce(bad)


def test_the_field_order_is_the_documented_one():
    assert POSE_FIELDS == ("x", "y", "z", "roll", "pitch", "yaw")
