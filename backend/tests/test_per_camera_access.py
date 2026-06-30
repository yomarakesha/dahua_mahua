"""Per-camera access control: admins see all; operators see only granted cameras."""

import uuid

from app.deps import user_can_access_camera
from app.models import Camera, Role, User


def _cam() -> Camera:
    c = Camera(nvr_id="nvr01", channel=1)
    c.id = uuid.uuid4()  # transient instances don't get the default until flush
    return c


def _user(role: Role, cameras: list[Camera]) -> User:
    u = User(username="u", password_hash="x", role=role)
    u.cameras = cameras
    return u


def test_admin_sees_every_camera():
    admin = _user(Role.admin, [])  # no grants needed
    assert user_can_access_camera(admin, _cam()) is True


def test_operator_sees_only_granted_cameras():
    granted = _cam()
    other = _cam()
    op = _user(Role.operator, [granted])
    assert user_can_access_camera(op, granted) is True
    assert user_can_access_camera(op, other) is False


def test_operator_without_grants_sees_nothing():
    op = _user(Role.operator, [])
    assert user_can_access_camera(op, _cam()) is False
