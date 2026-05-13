"""aximm.py — backward-compatibility shim; re-exports everything from memif.py."""
from pysilicon.hw.memif import *  # noqa: F401, F403
from pysilicon.hw.memif import (
    AXIMMAddressRange,
    AXIMMCrossBarIF,
    AXIMMCrossBarIFMaster,
    AXIMMCrossBarIFSlave,
    AXIMMProtocol,
    DirectMMIF,
    MMIFMaster,
    MMIFSlave,
    RxReadProc,
    RxWriteProc,
    assign_address_ranges,
)
