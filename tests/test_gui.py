import pytest

from vito_diag.gui import build_args


def test_build_args():
    assert build_args("scan", "COM7", "38400") == ["scan", "--port", "COM7", "--baudrate", "38400"]
    assert build_args("scan-all", "COM7", "", line="9") == ["ecu", "scan-all", "--port", "COM7", "--line", "9"]
    assert build_args("read", "COM7", "", block="12")[-4:] == ["--kline", "12", "--line", "7"]
    assert "--can" in build_args("read", "COM7", "", block="7E1:7E9")
    assert build_args("scan", "COM7", "38400", demo=True) == ["scan", "--demo"]
    with pytest.raises(ValueError):
        build_args("read", "COM7", "", block="")
