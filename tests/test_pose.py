import pytest

from hydra_umc_visual_servoing_api.pose import Pose6D, PoseParseError


def test_parse_valid():
    p = Pose6D.parse("1.0,2.0,3.0,0.1,0.2,0.3")
    assert p == Pose6D(1.0, 2.0, 3.0, 0.1, 0.2, 0.3)


def test_parse_wrong_field_count():
    with pytest.raises(PoseParseError):
        Pose6D.parse("1.0,2.0,3.0")


def test_parse_non_numeric():
    with pytest.raises(PoseParseError):
        Pose6D.parse("1.0,2.0,3.0,x,0.2,0.3")


def test_pose_is_immutable():
    p = Pose6D.parse("0,0,0,0,0,0")
    with pytest.raises(Exception):
        p.x = 1.0  # type: ignore[misc]
