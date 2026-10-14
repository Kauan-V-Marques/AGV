"""Profundidade do Kinect: distancias por janela e mapa colorido (funcoes puras)."""

from typing import Optional

import cv2
import numpy as np


def depth_segment_distance(depth_seg: np.ndarray, is_mm: bool) -> Optional[float]:
    if is_mm:
        valid = depth_seg[(depth_seg > 200) & (depth_seg < 8000)]
        if valid.size:
            return float(np.min(valid)) / 1000.0
        return None

    valid = depth_seg[(depth_seg > 100) & (depth_seg < 2040)]
    if not valid.size:
        return None
    # No formato 11 bits o valor bruto cresce com a distancia: o menor raw e o ponto mais proximo.
    raw = float(np.min(valid))
    denom = raw * -0.0030711016 + 3.3309495161
    if denom <= 0.0:
        return None
    depth_m = 1.0 / denom
    if 0.15 <= depth_m <= 10.0:
        return depth_m
    return None


def compute_depth_metrics(depth: np.ndarray) -> tuple[bool, Optional[float], Optional[float], Optional[float], Optional[float]]:
    if depth is None or depth.size == 0:
        return False, None, None, None, None

    height, width = depth.shape[:2]
    is_mm = bool(np.nanmax(depth) > 3000)

    row_a = int(height * 0.35)
    row_b = int(height * 0.65)
    left_a = int(width * 0.10)
    left_b = int(width * 0.30)
    center_a = int(width * 0.40)
    center_b = int(width * 0.60)
    right_a = int(width * 0.70)
    right_b = int(width * 0.90)

    left = depth_segment_distance(depth[row_a:row_b, left_a:left_b], is_mm)
    center = depth_segment_distance(depth[row_a:row_b, center_a:center_b], is_mm)
    right = depth_segment_distance(depth[row_a:row_b, right_a:right_b], is_mm)

    distance = center
    candidates = [value for value in (left, center, right) if value is not None]
    if distance is None and candidates:
        distance = min(candidates)

    return is_mm, distance, left, center, right


def depth_to_colormap(depth: np.ndarray, is_mm: bool) -> np.ndarray:
    if is_mm:
        clipped = np.clip(depth.astype(np.float32), 200, 4000)
        norm = ((clipped - 200.0) / 3800.0 * 255.0).astype(np.uint8)
    else:
        clipped = np.clip(depth.astype(np.float32), 0, 2047)
        norm = (clipped / 2047.0 * 255.0).astype(np.uint8)
    return cv2.applyColorMap(255 - norm, cv2.COLORMAP_JET)
