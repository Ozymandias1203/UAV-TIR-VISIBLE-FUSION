import cv2
import numpy as np
import os
import json
import logging
from datetime import datetime
from typing import List, Tuple, Dict, Any, Optional

from config.config_manager import ConfigManager
from pipeline_io.io_utils import safe_imread

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)


class DualSpectralCalibrator:
    """光学系统标定层实现。

    特点：
    - 使用 dataset_profile 中的 `chessboard_size`（内角点数）优先作为 `pattern_size`；
    - RGB 预处理：归一化 + CLAHE + 轻度平滑；
    - TIR 预处理：归一化 -> 反转 -> CLAHE -> Otsu（二值）返回用于检测/精化的图像对；
    - `save_calibration()` 支持将结果写入 `ConfigManager.get_calibration_output_path()`，并合并传感器条目。
    """

    def __init__(
        self,
        config: Optional[ConfigManager] = None,
        chessboard_physical: Tuple[int, int] = (12, 9),
        square_size_mm: Optional[float] = None,
    ) -> None:
        self.config = config

        # pattern_size 为 OpenCV 所需的内角点数 (cols, rows)
        if config and hasattr(config, 'dataset_profile'):
            self.pattern_size: Tuple[int, int] = tuple(config.dataset_profile.chessboard_size)
            if square_size_mm is None:
                square_size_mm = getattr(config.dataset_profile, 'square_size_mm', None)
        else:
            # 如果传入的是物理格数（12x9），则转换为内角点数 (11,8)
            self.pattern_size = (chessboard_physical[0] - 1, chessboard_physical[1] - 1)
            if square_size_mm is None:
                square_size_mm = 40.0

        if square_size_mm is None:
            square_size_mm = 40.0

        self.square_size_m = float(square_size_mm) / 1000.0

        cols, rows = self.pattern_size
        self.objp = np.zeros((cols * rows, 3), np.float32)
        self.objp[:, :2] = np.mgrid[0:cols, 0:rows].T.reshape(-1, 2) * self.square_size_m

        self.criteria = (cv2.TERM_CRITERIA_EPS + cv2.TERM_CRITERIA_MAX_ITER, 30, 0.001)

    def _preprocess_rgb(self, image: np.ndarray) -> np.ndarray:
        """RGB 标定前处理：归一化 -> CLAHE -> 轻度平滑"""
        if len(image.shape) == 3 and image.shape[2] >= 3:
            gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
        else:
            gray = image.copy()
        gray = cv2.normalize(gray, None, 0, 255, cv2.NORM_MINMAX, dtype=cv2.CV_8U)
        clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
        enhanced = clahe.apply(gray)
        smoothed = cv2.GaussianBlur(enhanced, (3, 3), 0)
        return smoothed

    def _preprocess_tir(self, image: np.ndarray) -> Tuple[np.ndarray, np.ndarray]:
        """TIR 标定前处理：映射到 8-bit -> 反转 -> CLAHE -> Otsu

        返回 (binary_otsu, enhanced) —— 用于检测与亚像素精化。
        """
        if image.ndim == 3:
            if image.shape[2] >= 3:
                gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
            else:
                gray = image[:, :, 0].copy()
        else:
            gray = image.copy()

        # 映射到 8-bit
        if gray.dtype in (np.uint16, np.float32, np.float64):
            gray = cv2.normalize(gray, None, 0, 255, cv2.NORM_MINMAX, dtype=cv2.CV_8U)
        elif gray.dtype != np.uint8:
            gray = gray.astype(np.uint8)

        inverted = cv2.bitwise_not(gray)
        clahe = cv2.createCLAHE(clipLimit=3.0, tileGridSize=(8, 8))
        enhanced = clahe.apply(inverted)
        _, binary_otsu = cv2.threshold(enhanced, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
        return binary_otsu, enhanced

    def calibrate(self, image_paths: List[str], sensor_name: str, is_tir: bool = False) -> Dict[str, Any]:
        """对单个传感器执行标定，返回字典形式结果而不是抛出异常（便于流水线记录）。"""
        objpoints: List[np.ndarray] = []
        imgpoints: List[np.ndarray] = []
        image_size: Optional[Tuple[int, int]] = None
        successful_images: List[str] = []

        if not image_paths:
            logger.warning(f"No input images provided for sensor {sensor_name}.")

        flags = cv2.CALIB_CB_ADAPTIVE_THRESH + cv2.CALIB_CB_NORMALIZE_IMAGE

        for img_path in image_paths:
            img = safe_imread(img_path, cv2.IMREAD_UNCHANGED)
            if img is None:
                continue

            if image_size is None:
                image_size = (img.shape[1], img.shape[0])

            if is_tir:
                detect_img, refine_img = self._preprocess_tir(img)
            else:
                detect_img = self._preprocess_rgb(img)
                refine_img = detect_img

            ret, corners = cv2.findChessboardCorners(detect_img, self.pattern_size, flags)
            if ret and corners is not None:
                objpoints.append(self.objp.copy())
                corners_subpix = cv2.cornerSubPix(refine_img, corners, (11, 11), (-1, -1), self.criteria)
                imgpoints.append(corners_subpix)
                successful_images.append(os.path.basename(img_path))

        detection_rate = len(successful_images) / len(image_paths) if image_paths else 0.0

        # 未检测到角点，返回失败标记
        if not objpoints:
            return {
                "schema_version": "calibration_v1",
                "sensor_name": sensor_name,
                "created_at": datetime.utcnow().isoformat() + "Z",
                "image_width_px": image_size[0] if image_size else None,
                "image_height_px": image_size[1] if image_size else None,
                "focal_length_px": [],
                "principal_point_x_px": None,
                "principal_point_y_px": None,
                "distortion_coefficients": [],
                "reprojection_rms_px": None,
                "chessboard_detection_rate": detection_rate,
                "quality_flag": "FAIL_NO_BOARD",
                "source_images": successful_images,
            }

        ret_rms, mtx, dist, rvecs, tvecs = cv2.calibrateCamera(objpoints, imgpoints, image_size, None, None)

        # 从 runtime config 获取阈值（回退到默认）
        reproj_thresh = 1.0
        detect_rate_thresh = 0.6
        if self.config and hasattr(self.config, 'runtime_config') and getattr(self.config, 'runtime_config'):
            qc = getattr(self.config.runtime_config, 'quality_thresholds', {})
            reproj_thresh = qc.get('reprojection_error_max', reproj_thresh)
            detect_rate_thresh = qc.get('min_detection_rate', detect_rate_thresh)

        quality_flag = "PASS" if (ret_rms is not None and ret_rms < reproj_thresh and detection_rate >= detect_rate_thresh) else "WARNING"

        result: Dict[str, Any] = {
            "schema_version": "calibration_v1",
            "source_version": "1.0.0",
            "created_at": datetime.utcnow().isoformat() + "Z",
            "sensor_name": sensor_name,
            "image_width_px": image_size[0],
            "image_height_px": image_size[1],
            "focal_length_px": [float(mtx[0, 0]), float(mtx[1, 1])],
            "principal_point_x_px": float(mtx[0, 2]),
            "principal_point_y_px": float(mtx[1, 2]),
            "affinity_or_skew": float(mtx[0, 1]) if mtx.shape[0] > 0 else 0.0,
            "distortion_coefficients": dist.flatten().tolist(),
            "reprojection_rms_px": float(ret_rms),
            "chessboard_detection_rate": detection_rate,
            "quality_flag": quality_flag,
            "units": {
                "focal_length_px": "pixels",
                "principal_point_x_px": "pixels",
                "principal_point_y_px": "pixels",
                "reprojection_rms_px": "pixels",
            },
            "source_images": successful_images,
        }

        return result

    def save_calibration(self, result: Dict[str, Any], config: ConfigManager) -> str:
        """将标定结果写入 `ConfigManager.get_calibration_output_path()` 指定的审计文件，并合并传感器条目。

        返回写入的文件路径。
        """
        if not config:
            raise ValueError("ConfigManager 实例为必需，用于确定审计输出路径")

        out_path = config.get_calibration_output_path()
        data: Dict[str, Any] = {}
        if os.path.exists(out_path):
            try:
                with open(out_path, 'r', encoding='utf-8') as f:
                    data = json.load(f)
            except Exception:
                data = {}

        data.setdefault('schema_version', 'calibration_v1')
        data['run_id'] = config.run_id
        data['timestamp'] = datetime.utcnow().isoformat() + 'Z'
        data.setdefault('sensors', {})
        data['sensors'][result.get('sensor_name', 'unknown')] = result

        os.makedirs(os.path.dirname(out_path), exist_ok=True)
        with open(out_path, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=4, ensure_ascii=False)

        logger.info(f"Saved calibration audit file: {out_path}")
        return out_path
