import cv2
import numpy as np

from app.api.routes.mask import _mask_to_contours


def test_mask_contours_preserve_disconnected_subject_components(tmp_path):
    mask = np.zeros((200, 300), dtype=np.uint8)
    cv2.rectangle(mask, (80, 20), (190, 150), 255, -1)
    cv2.rectangle(mask, (85, 165), (120, 195), 255, -1)
    cv2.rectangle(mask, (150, 165), (190, 195), 255, -1)
    path = tmp_path / "mask.png"
    cv2.imwrite(str(path), mask)

    contours = _mask_to_contours(str(path))

    assert len(contours) == 3
    assert all(len(contour) >= 3 for contour in contours)
