from bugit_v2.checkbox_utils import get_checkbox_version
from bugit_v2.checkbox_utils.models import CertificationStatus


def get_certification_status(test_plan: str) -> CertificationStatus | None:
    cb_info = get_checkbox_version()
    if cb_info is None:
        return None
    # checkbox_bin = cb_info[2]
