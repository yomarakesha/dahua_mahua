"""Parser tests for the NVR RemoteDevice camera-IP import.

The fixture mirrors the actual response shape of a DHI-NVR5232-EI
(`configManager.cgi?action=getConfig&name=RemoteDevice`): per-slot keys under
`table.RemoteDevice.uuid:System_CONFIG_NETCAMERA_INFO_<slot>.`, where slot is
0-based and maps to NVR channel slot+1. Unused slots carry Address=192.168.0.0
and Enable=false.
"""

from app.services.camera_import import parse_remote_devices

FIXTURE = """\
table.RemoteDevice.uuid:System_CONFIG_NETCAMERA_INFO_0.Address=192.168.23.11
table.RemoteDevice.uuid:System_CONFIG_NETCAMERA_INFO_0.DeviceType=DH-IPC-HFW1431S1-A
table.RemoteDevice.uuid:System_CONFIG_NETCAMERA_INFO_0.Enable=true
table.RemoteDevice.uuid:System_CONFIG_NETCAMERA_INFO_0.Password=******
table.RemoteDevice.uuid:System_CONFIG_NETCAMERA_INFO_0.Port=37777
table.RemoteDevice.uuid:System_CONFIG_NETCAMERA_INFO_0.RtspPort=0
table.RemoteDevice.uuid:System_CONFIG_NETCAMERA_INFO_0.UserName=admin
table.RemoteDevice.uuid:System_CONFIG_NETCAMERA_INFO_0.VideoInputs[0].MainStreamUrl=
table.RemoteDevice.uuid:System_CONFIG_NETCAMERA_INFO_0.VideoInputs[0].Name=stolb kamera
table.RemoteDevice.uuid:System_CONFIG_NETCAMERA_INFO_1.Address=192.168.23.12
table.RemoteDevice.uuid:System_CONFIG_NETCAMERA_INFO_1.Enable=true
table.RemoteDevice.uuid:System_CONFIG_NETCAMERA_INFO_1.VideoInputs[0].Name=kpp derweze 2
table.RemoteDevice.uuid:System_CONFIG_NETCAMERA_INFO_10.Address=192.168.23.21
table.RemoteDevice.uuid:System_CONFIG_NETCAMERA_INFO_10.Enable=true
table.RemoteDevice.uuid:System_CONFIG_NETCAMERA_INFO_28.Address=192.168.0.0
table.RemoteDevice.uuid:System_CONFIG_NETCAMERA_INFO_28.Enable=false
table.RemoteDevice.uuid:System_CONFIG_NETCAMERA_INFO_29.Address=192.168.0.0
table.RemoteDevice.uuid:System_CONFIG_NETCAMERA_INFO_29.Enable=false
"""


def test_slot_maps_to_channel_plus_one():
    chans = parse_remote_devices(FIXTURE)
    assert chans[1] == "192.168.23.11"
    assert chans[2] == "192.168.23.12"


def test_double_digit_slot_parses():
    chans = parse_remote_devices(FIXTURE)
    assert chans[11] == "192.168.23.21"


def test_empty_and_disabled_slots_are_skipped():
    chans = parse_remote_devices(FIXTURE)
    assert 29 not in chans
    assert 30 not in chans
    assert set(chans) == {1, 2, 11}


def test_disabled_slot_with_real_ip_is_skipped():
    text = (
        "table.RemoteDevice.uuid:System_CONFIG_NETCAMERA_INFO_5.Address=192.168.23.40\n"
        "table.RemoteDevice.uuid:System_CONFIG_NETCAMERA_INFO_5.Enable=false\n"
    )
    assert parse_remote_devices(text) == {}


def test_garbage_lines_are_ignored():
    text = "OK\nname=RemoteDevice\n\n" + FIXTURE
    chans = parse_remote_devices(text)
    assert chans[1] == "192.168.23.11"
