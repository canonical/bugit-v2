import enum
import os
from collections.abc import Mapping
from pathlib import Path

LOGO_ASCII_ART = """
█████▄ ▄▄ ▄▄  ▄▄▄▄ ▄▄ ▄▄▄▄▄▄   ██  ██ ████▄
██▄▄██ ██ ██ ██ ▄▄ ██   ██     ██▄▄██  ▄██▀
██▄▄█▀ ▀███▀ ▀███▀ ██   ██      ▀██▀  ███▄▄
""".strip()

FEATURE_MAP: Mapping[str, tuple[str, ...]] = {
    "Audio": ("hwe-audio",),
    "Bluetooth": ("hwe-bluetooth",),
    "Brightness": ("hwe-brightness",),
    "CANBus": ("hwe-canbus",),
    "Camera": ("oem-camera",),
    "Checkbox": ("checkbox test-case",),
    "External Storage": ("oem-storage",),
    "Fingerprint Reader": ("hwe-fingerprint",),
    "Firmware": ("hwe-firmware",),
    "Full Disk Encryption": ("oem-fde",),
    "GPIO": ("hwe-gpio",),
    "Hotkeys": ("hwe-hotkeys",),
    "I2C": ("hwe-i2c",),
    "Install": ("hwe-installer",),
    "LED": ("hwe-led",),
    "Media Card": ("hwe-media",),
    "Missing driver": ("hwe-needs-driver",),
    "Model Assertion": ("oem-assertions",),
    "Model Pivot / Remodelling": ("oem-assertions",),
    "Networking (ethernet)": (
        "hwe-networking-ethernet",
        "oem-networking",
    ),
    "Networking (modem)": (
        "hwe-networking-modem",
        "oem-networking",
    ),
    "Networking (wifi)": (
        "hwe-networking-wifi",
        "oem-networking",
    ),
    "Other Problem": ("oem-other",),
    "Performance": ("oem-performance",),
    "Power Management": ("hwe-suspend-resume",),
    "Power On/Off": ("hwe-powercycle",),
    "Recovery": ("oem-recovery",),
    "Secure Boot": ("oem-secureboot",),
    "Sensor": ("hwe-sensor",),
    "Serial Assertion": ("oem-assertions",),
    "Serial": ("hwe-serial",),
    "Snapd": ("oem-snapd",),
    "Store": ("oem-store",),
    "Stress": ("oem-stress",),
    "TPM": ("hwe-tpm",),
    "Touchpad": ("hwe-touchpad",),
    "Touchscreen": ("oem-touchscreen",),
    "USB": ("hwe-usb",),
    "Video": ("hwe-graphics",),
    "Watchdog": ("hwe-watchdog",),
    "Zigbee": ("hwe-zigbee",),
}

VENDOR_MAP: Mapping[str, tuple[str, ...]] = {
    "AMD": ("ihv-amd",),
    "Atheros/Qualcomm": ("ihv-qualcomm-atheros",),
    "Gemalto": ("ihv-gemalto",),
    "Intel": ("ihv-intel",),
    "MediaTek": ("ihv-mtk",),
    "Marvell": ("ihv-marvell",),
    "Mighty Gecko": ("ihv-mightygecko",),
    "Nvidia": ("ihv-nvidia",),
    "Quectel": ("ihv-quectel",),
    "Realtek": ("ihv-realtek",),
    "Redpine": ("ihv-redpine",),
    "Sierra": ("ihv-sierra",),
    "Telegesis": ("ihv-telegesis",),
    "Telit": ("ihv-telit",),
}


AUTOSAVE_DIR = (
    Path(os.getenv("SNAP_USER_DATA", str(Path().home().absolute() / ".cache")))
    / "bugit-v2-autosave"
)
VISUAL_CONFIG_DIR = (
    Path(os.getenv("SNAP_USER_DATA", str(Path().home().absolute() / ".config")))
    / "bugit-v2-visual-config"
)
DUT_INFO_DIR = (
    Path(os.getenv("SNAP_USER_DATA", str(Path().home().absolute() / ".config")))
    / "bugit-v2-dut-info"
)
HOST_FS = Path("/var/lib/snapd/hostfs")
MAX_JOB_OUTPUT_LEN = 10_000


class NullSelection(enum.Enum):
    """
    Represent an explicit selection of "No Session" and "No Job" in
    the selection screens
    """

    # NO_SESSION is also used when a checkbox submission is passed from the CLI
    # semantically, selecting a checkbox submission => explicitly not selecting any sessions
    NO_SESSION = enum.auto()
    NO_JOB = enum.auto()
    NO_BACKUP = enum.auto()
    NO_CHECKBOX_SUBMISSION = enum.auto()
