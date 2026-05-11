import cv2
import numpy as np
import os
import logging

logger = logging.getLogger(__name__)

def safe_imread(path: str, flags: int = cv2.IMREAD_COLOR) -> np.ndarray:
    """Fallback strategy path reader for problematic windows paths"""
    try:
        # 修复：正确的 OpenCV API 调用路径
        if hasattr(cv2, 'utils') and hasattr(cv2.utils, 'logging'):
            old_log_level = cv2.utils.logging.getLogLevel()
            cv2.utils.logging.setLogLevel(cv2.utils.logging.LOG_LEVEL_ERROR)
            try:
                img = cv2.imread(str(path), flags)
            finally:
                cv2.utils.logging.setLogLevel(old_log_level)
        else:
            img = cv2.imread(str(path), flags)
        
        if img is not None:
            return img
    except Exception:
        pass
    
    logger.warning(f"cv2.imread failed for {path}, falling back to imdecode")
    with open(path, 'rb') as f:
        img_array = np.frombuffer(f.read(), dtype=np.uint8)
        return cv2.imdecode(img_array, flags)

def safe_imwrite(path: str, img: np.ndarray) -> bool:
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        success = cv2.imwrite(str(path), img)
        if success:
            return True
    except Exception:
        pass
    
    logger.warning(f"cv2.imwrite failed for {path}, falling back to imencode")
    ext = os.path.splitext(path)[1]
    success, img_array = cv2.imencode(ext, img)
    if success:
        with open(path, 'wb') as f:
            f.write(img_array.tobytes())
        return True
    return False