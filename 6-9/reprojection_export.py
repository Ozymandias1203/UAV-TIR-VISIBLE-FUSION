"""
reprojection_export.py
Step 7: 3D Reprojection and Visibility checking with occlusion handling via Z-Buffering.
"""
import logging
import numpy as np
from typing import Dict, List, Any

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

class ReprojectionExporter:
    def __init__(self, point_cloud: Any, camera_params: Dict[str, Dict[str, Any]]) -> None:
        self.point_cloud = point_cloud
        self.camera_params = camera_params
        self.z_buffer_tolerance = 0.05 # 5cm depth tolerance for occlusion

    def export_reprojections(self) -> Dict[str, List[Dict[str, Any]]]:
        points = self.point_cloud.points
        num_points = points.shape[0]
        reprojection_dict = {f"point_{i:06d}": [] for i in range(num_points)}
        logging.info(f"Computing reprojections & occlusions for {num_points} points.")

        # Process per camera for efficient Z-Buffer generation
        for cam_id, params in self.camera_params.items():
            K = params.get("K")
            pose = params.get("pose")
            width, height = int(params.get("width", 4000)), int(params.get("height", 3000))
            
            # Inverse pose for World-to-Camera
            R_cam = pose[0:3, 0:3].T
            t_cam = -R_cam @ pose[0:3, 3]
            
            # Transform all points to camera space
            points_cam = (R_cam @ points.T).T + t_cam
            depths = points_cam[:, 2]
            
            # Project to image plane
            points_proj = (K @ points_cam.T).T
            u = (points_proj[:, 0] / depths).astype(np.float32)
            v = (points_proj[:, 1] / depths).astype(np.float32)

            # Valid in-front-of-camera and within image bounds
            valid_mask = (depths > 0) & (u >= 0) & (u < width - 1) & (v >= 0) & (v < height - 1)
            
            # Build Z-buffer for occlusion detection
            z_buffer = np.full((height, width), np.inf, dtype=np.float32)
            valid_indices = np.where(valid_mask)[0]
            
            u_int = np.round(u[valid_indices]).astype(np.int32)
            v_int = np.round(v[valid_indices]).astype(np.int32)
            d_valid = depths[valid_indices]

            # Populate Z-buffer (Min depth per pixel)
            for idx, ui, vi, d in zip(valid_indices, u_int, v_int, d_valid):
                if d < z_buffer[vi, ui]:
                    z_buffer[vi, ui] = d

            # Generate final reprojection records
            for idx in valid_indices:
                point_id = f"point_{idx:06d}"
                px_u, px_v, pt_depth = u[idx], v[idx], depths[idx]
                
                # Check occlusion state using Z-buffer
                ui, vi = int(round(px_u)), int(round(px_v))
                is_occluded = pt_depth > (z_buffer[vi, ui] + self.z_buffer_tolerance)
                
                if not is_occluded:
                    reprojection_dict[point_id].append({
                        "camera_id": cam_id,
                        "x": float(px_u),
                        "y": float(px_v),
                        "distance_to_center": float(np.sqrt((px_u - width/2)**2 + (px_v - height/2)**2)),
                        "visibility_state": "visible",
                        "occlusion_state": "not_occluded"
                    })

        logging.info("Reprojection and visibility state computation completed.")
        return reprojection_dict