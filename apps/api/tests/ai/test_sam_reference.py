import cv2
import numpy as np

from app.ai.services.sam import create_subject_reference


def test_create_subject_reference_isolates_mask_on_neutral_background(tmp_path):
    frame_path = tmp_path / "frame.png"
    mask_path = tmp_path / "mask.png"
    output_path = tmp_path / "subject.png"

    frame = np.full((480, 640, 3), (30, 20, 10), dtype=np.uint8)
    frame[80:440, 220:420] = (50, 40, 220)
    cv2.imwrite(str(frame_path), frame)

    mask = np.zeros((480, 640), dtype=np.uint8)
    mask[80:440, 220:420] = 255
    cv2.imwrite(str(mask_path), mask)

    result = create_subject_reference(str(frame_path), str(mask_path), str(output_path))
    reference = cv2.imread(result, cv2.IMREAD_COLOR)

    assert reference is not None
    assert reference.shape[1] >= 360
    center = reference[reference.shape[0] // 2, reference.shape[1] // 2]
    assert tuple(center) == (50, 40, 220)
    assert tuple(reference[0, 0]) == (238, 238, 238)
