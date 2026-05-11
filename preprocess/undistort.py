import cv2
import numpy as np
import os
import json
from typing import Dict, Any
from pipeline_io.io_utils import safe_imread

try:
    import tifffile
    _HAS_TIFFILE = True
except ImportError:
    tifffile = None
    _HAS_TIFFILE = False

try:
    from PIL import Image
    _HAS_PIL = True
except ImportError:
    Image = None
    _HAS_PIL = False


def _to_json_serializable(value):
    """Attempt to convert various TIFF tag value types to JSON-serializable Python types."""
    try:
        # Primitives
        if isinstance(value, (str, int, float, bool)) or value is None:
            return value
        # Bytes
        if isinstance(value, (bytes, bytearray)):
            try:
                return value.decode('utf-8', errors='replace')
            except Exception:
                return repr(value)
        # NumPy scalars
        if isinstance(value, np.generic):
            return value.item()
        # NumPy arrays
        if isinstance(value, np.ndarray):
            return _to_json_serializable(value.tolist())
        # Lists / tuples
        if isinstance(value, (list, tuple)):
            return [_to_json_serializable(v) for v in value]
        # Dictionaries
        if isinstance(value, dict):
            return {str(k): _to_json_serializable(v) for k, v in value.items()}
        # Fallback: try to convert to str
        return str(value)
    except Exception:
        return str(value)

class ImageUndistorter:
    """图像去畸变层，利用标定基线提供稳定的几何图像用于匹配与重建"""
    
    def __init__(self, calibration_data: Dict[str, Any]):
        self.calibration_data = calibration_data
        
        if not calibration_data.get("focal_length_px") or len(calibration_data["focal_length_px"]) < 2:
            raise ValueError(f"Invalid calibration data for sensor {calibration_data.get('sensor_name', 'unknown')}. Make sure calibration passed.")
        
        # 从绝对像素格式重建 OpenCV 内参矩阵
        self.camera_matrix = np.array([
            [calibration_data["focal_length_px"][0], 0, calibration_data["principal_point_x_px"]],
            [0, calibration_data["focal_length_px"][1], calibration_data["principal_point_y_px"]],
            [0, 0, 1]
        ], dtype=np.float64)
        
        self.dist_coeffs = np.array(calibration_data["distortion_coefficients"], dtype=np.float64)
        
        self.width = calibration_data["image_width_px"]
        self.height = calibration_data["image_height_px"]
        
        # 计算最优新相机矩阵与有效 ROI (保留全图比例不裁剪以维持单应性近似条件，alpha=0)
        # 根据 calibration_model.md，去畸变过程必须保留映射关系与裁切记录
        self.new_camera_matrix, self.roi = cv2.getOptimalNewCameraMatrix(
            self.camera_matrix, self.dist_coeffs, (self.width, self.height), 0, (self.width, self.height)
        )

    def process_image(self, input_path: str, output_path: str, is_tir: bool = False):
        """
        执行去畸变，并按照契约保存相应格式
        RGB 保留为无损 PNG，TIR 热红外主处理对象强制为 TIFF
        """
        # 读取输入图像（支持 Unicode 路径的安全读取）
        img = safe_imread(input_path, cv2.IMREAD_UNCHANGED)
        if img is None:
            raise FileNotFoundError(f"Cannot read image: {input_path}")

        dst = cv2.undistort(img, self.camera_matrix, self.dist_coeffs, None, self.new_camera_matrix)

        # 根据 ROI 进行裁切
        x, y, w, h = self.roi
        if w > 0 and h > 0:
            dst_cropped = dst[y:y+h, x:x+w]
        else:
            dst_cropped = dst

        # 保存去畸变结果
        os.makedirs(os.path.dirname(output_path) or '.', exist_ok=True)

        if is_tir:
            # TIR 主处理必须使用 TIFF
            if not output_path.lower().endswith(('.tif', '.tiff')):
                output_path = os.path.splitext(output_path)[0] + '.tiff'
            cv2.imwrite(output_path, dst_cropped)
        else:
            # RGB 使用 PNG 避免有损压缩引入匹配误差
            if not output_path.lower().endswith('.png'):
                output_path = os.path.splitext(output_path)[0] + '.png'
            cv2.imwrite(output_path, dst_cropped, [cv2.IMWRITE_PNG_COMPRESSION, 3])
            
        # 记录元数据关联 (依据 file_formats.md)
        meta_path = os.path.splitext(output_path)[0] + '_meta.json'
        # 动态描述去畸变模型（基于畸变系数长度）并保留原始 TIFF 元数据（sidecar）
        coeffs = np.asarray(self.dist_coeffs).flatten() if self.dist_coeffs is not None else np.array([])
        size = int(coeffs.size)
        if size == 0:
            und_model = 'none'
        elif size == 4:
            und_model = 'brown_conrady_4_params'
        elif size == 5:
            und_model = 'brown_conrady_5_params'
        elif size == 8:
            und_model = 'rational_8_params'
        else:
            und_model = f'unspecified_{size}_params'

        metadata = {
            "schema_version": "1.0",
            "source_image": os.path.basename(input_path),
            "undistortion_model": und_model,
            "cropped": True if (w > 0 and h > 0) else False,
            "resampled": False,
            "scale_factor": 1.0,
            "calibration_ref": self.calibration_data.get("sensor_name", "unknown"),
            "output_size": [dst_cropped.shape[1], dst_cropped.shape[0]]
        }

        # 如果处理的是热红外 TIFF，尝试读取并保存原始 TIFF 标签/元数据到 sidecar
        if is_tir:
            orig_meta = None
            if _HAS_TIFFILE:
                try:
                    with tifffile.TiffFile(input_path) as tf:
                        tags = {}
                        for page in tf.pages:
                            for tag in page.tags.values():
                                try:
                                    tags[str(tag.name)] = _to_json_serializable(tag.value)
                                except Exception:
                                    try:
                                        tags[str(tag.name)] = str(tag.value)
                                    except Exception:
                                        tags[str(tag.name)] = None
                        orig_meta = tags
                        metadata['metadata_preserved_by'] = 'tifffile'
                except Exception as e:
                    metadata['original_tiff_tags'] = {'error': str(e)}
                    metadata['metadata_preserved_by'] = 'tifffile_failed'
            elif _HAS_PIL:
                try:
                    im = Image.open(input_path)
                    tags = {}
                    try:
                        # PIL exposes TIFF tags via tag_v2 for newer versions
                        if hasattr(im, 'tag_v2'):
                            for k, v in im.tag_v2.items():
                                try:
                                    tags[str(k)] = _to_json_serializable(v)
                                except Exception:
                                    tags[str(k)] = str(v)
                        else:
                            tags = {str(k): _to_json_serializable(v) for k, v in im.info.items()}
                    except Exception:
                        tags = {str(k): _to_json_serializable(v) for k, v in im.info.items()} 
                    orig_meta = tags
                    metadata['metadata_preserved_by'] = 'PIL'
                except Exception as e:
                    metadata['original_tiff_tags'] = {'error': str(e)}
                    metadata['metadata_preserved_by'] = 'PIL_failed'
            else:
                metadata['original_tiff_tags'] = None
                metadata['metadata_preserved_by'] = 'none'

            if orig_meta is not None:
                metadata['original_tiff_tags'] = orig_meta

        with open(meta_path, 'w', encoding='utf-8') as f:
            json.dump(metadata, f, indent=4, ensure_ascii=False)
